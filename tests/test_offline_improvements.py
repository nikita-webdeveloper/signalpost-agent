from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.external_footprint import publishable_observation, validate_observation  # noqa: E402
from scripts.build_wikidata_observations import build as build_wikidata, names_agree  # noqa: E402
from scripts.merge_observations import merge  # noqa: E402
from scripts import run_annual_report_workforce_connector as workforce  # noqa: E402

PROFILES = [
    {"organisation_number": "976252128", "name": "STIFTELSEN GAUSDAL LEIRSTED"},
    {"organisation_number": "977126231", "name": "STATOILS FORSKNINGSFOND STI"},
    {"organisation_number": "976073169", "name": "FORSKNINGSSTIFTELSEN NIFU"},
    {"organisation_number": "923609016", "name": "TEST AS"},
]


def binding(org: str, item: str, label: str) -> dict:
    return {
        "orgnr": {"type": "literal", "value": org},
        "item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{item}"},
        "itemLabel": {"type": "literal", "value": label},
    }


def minimal_pdf(lines: list[str]) -> bytes:
    """Build a tiny, valid one-page text PDF (with a real xref table) for tests."""
    text = "BT /F1 12 Tf 72 700 Td " + " 0 -20 Td ".join(f"({line}) Tj" for line in lines) + " ET"
    objects = [
        "<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        "<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        f"<</Length {len(text)}>>\nstream\n{text}\nendstream",
        "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    out += f"trailer\n<</Root 1 0 R/Size {len(objects) + 1}>>\nstartxref\n{xref_at}\n%%EOF\n"
    return out.encode("latin-1")


class WikidataObservationTests(unittest.TestCase):
    def test_name_agreement_ignores_legal_form_and_case(self):
        self.assertTrue(names_agree("Statoils Forskningsfond", "STATOILS FORSKNINGSFOND STI"))
        self.assertTrue(names_agree("Sæther Kraft", "SÆTHER KRAFT AS"))
        self.assertFalse(names_agree("Nordisk institutt for studier av innovasjon", "FORSKNINGSSTIFTELSEN NIFU"))
        self.assertFalse(names_agree("", "TEST AS"))

    def test_build_publishes_exact_matches_and_quarantines_the_rest(self):
        bindings = [
            binding("976252128", "Q1", "Stiftelsen Gausdal Leirsted"),
            binding("976252128", "Q1", "Stiftelsen Gausdal Leirsted"),  # duplicate
            binding("976073169", "Q2", "Nordisk institutt for studier av innovasjon"),  # name disagrees
            binding("999999999", "Q3", "Unknown Company"),  # not in list
        ]
        observations, quarantined = build_wikidata(bindings, PROFILES, retrieved_at="2026-09-21T04:18:07Z")
        self.assertEqual([item["organisation_number"] for item in observations], ["976252128"])
        self.assertEqual(validate_observation(observations[0]), [])
        self.assertTrue(publishable_observation(observations[0]))
        self.assertEqual(len(quarantined), 2)
        self.assertEqual(observations[0]["retrieved_at"], "2026-09-21T04:18:07Z")

    def test_bad_item_uri_is_quarantined(self):
        row = {"orgnr": {"value": "976252128"}, "item": {"value": "http://evil.example/Q1"}, "itemLabel": {"value": "Stiftelsen Gausdal Leirsted"}}
        observations, quarantined = build_wikidata([row], PROFILES, retrieved_at="2026-09-21T04:18:07Z")
        self.assertEqual(observations, [])
        self.assertEqual(len(quarantined), 1)


class MergeObservationTests(unittest.TestCase):
    def workforce_row(self, org: str, row_id: str, **overrides) -> dict:
        row = {
            "id": row_id,
            "organisation_number": org,
            "platform": "brreg",
            "signal_type": "workforce_snapshot",
            "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/1",
            "retrieved_at": "2026-09-18T00:00:00Z",
            "content_sha256": "a" * 64,
            "exact_entity": True,
            "identity_proof": [{"type": "organisation_number_in_pdf", "value": org}],
            "acquisition_mode": "official_api",
            "rights_status": "approved",
            "source_class": "official_annual_account_copy",
            "strategy": "annual_report_workforce_snapshot",
        }
        row.update(overrides)
        return row

    def test_merge_adds_only_valid_new_rows(self):
        known = {"976252128", "977126231", "923609016"}
        base = [self.workforce_row("976252128", "base-1")]
        good = self.workforce_row("977126231", "new-good")
        same_id = self.workforce_row("923609016", "base-1")
        dup_company = self.workforce_row("976252128", "new-dup-company")
        unapproved = self.workforce_row("923609016", "new-unapproved", rights_status="review_required")
        stranger = self.workforce_row("111111111", "new-stranger")
        merged, details = merge(base, [[good, same_id, dup_company, unapproved, stranger]], known)
        self.assertEqual([row["id"] for row in merged], ["base-1", "new-good"])
        counts = details["counts"]
        self.assertEqual(counts["added"], 1)
        self.assertEqual(counts["duplicate_id"], 1)
        self.assertEqual(counts["duplicate_single_valued_signal"], 1)
        self.assertEqual(counts["failed_validation"], 1)
        self.assertEqual(counts["unknown_company"], 1)

    def test_cli_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / "base.jsonl").write_text(json.dumps(self.workforce_row("976252128", "b")) + "\n", encoding="utf-8")
            (d / "add.jsonl").write_text(json.dumps(self.workforce_row("977126231", "n")) + "\n", encoding="utf-8")
            (d / "profiles.jsonl").write_text("".join(json.dumps(p) + "\n" for p in PROFILES), encoding="utf-8")
            (d / "existing.jsonl").write_text("keep me", encoding="utf-8")
            base_command = [sys.executable, str(ROOT / "scripts" / "merge_observations.py"), "--base", str(d / "base.jsonl"), "--add", str(d / "add.jsonl"), "--profiles", str(d / "profiles.jsonl"), "--report", str(d / "r.json")]
            clobber = subprocess.run(base_command + ["--output", str(d / "existing.jsonl")], capture_output=True, text=True)
            self.assertNotEqual(clobber.returncode, 0)
            self.assertEqual((d / "existing.jsonl").read_text(encoding="utf-8"), "keep me")
            same_as_input = subprocess.run(base_command + ["--output", str(d / "base.jsonl")], capture_output=True, text=True)
            self.assertNotEqual(same_as_input.returncode, 0)
            ok = subprocess.run(base_command + ["--output", str(d / "merged.jsonl")], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(len((d / "merged.jsonl").read_text(encoding="utf-8").splitlines()), 2)


class WorkforceCacheTimestampTests(unittest.TestCase):
    def test_cache_hit_reports_download_time_and_makes_no_request(self):
        org = "923609016"
        profile = {
            "organisation_number": org,
            "evidence": {
                "registry": {"value": {}},
                "financial_history": {"value": {"pdfs": [{"year": "2025", "url": "https://data.brreg.no/example.pdf"}]}},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            pdf = cache / f"{org}-2025.pdf"
            pdf.write_bytes(minimal_pdf([f"Organisasjonsnummer {org}", "Antall Aarsverk i regnskapsaret 3.00"]))
            downloaded = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc).timestamp()
            os.utime(pdf, (downloaded, downloaded))
            with patch.object(workforce.urllib.request, "urlopen", side_effect=AssertionError("network must not be used on a cache hit")):
                observation, status = workforce.collect(profile, cache, ocr_pages=0, ocr_dpi=130)
        self.assertEqual(status["status"], "accepted", status)
        self.assertTrue(status["cache_hit"])
        self.assertEqual(observation["retrieved_at"], "2026-09-18T12:00:00Z")
        self.assertEqual(observation["metrics"]["workforce_value"], 3)
        self.assertEqual(validate_observation(observation), [])


if __name__ == "__main__":
    unittest.main()
