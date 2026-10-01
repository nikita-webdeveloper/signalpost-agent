#!/usr/bin/env python3
"""Turn saved Wikidata SPARQL matches into exact-entity external observations.

Source: Wikidata Query Service (free, official public API; data is CC0).
Identity: Wikidata property P2333 (Norwegian organisation number) must equal the
company's organisation number AND the Wikidata label must agree with the registry name.
If the name does not agree, the match is quarantined for human review, never published.

This script does not make network calls. It reads the result file written by
scripts/query_wikidata.py (out/wikidata-matches.json).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Legal-form words that do not identify a company.
LEGAL_FORM_TOKENS = {"as", "asa", "sti", "sa", "ans", "da", "enk", "nuf", "ba", "iks", "kf", "fkf", "hf", "lll"}
ITEM_URI = re.compile(r"^https?://www\.wikidata\.org/entity/(Q\d+)$")


def name_tokens(value: str) -> set[str]:
    tokens = re.findall(r"[^\W_]+", value.casefold())
    return {token for token in tokens if token not in LEGAL_FORM_TOKENS}


def names_agree(label: str, registry_name: str) -> bool:
    """True when one name's meaningful words are contained in the other's."""
    a, b = name_tokens(label), name_tokens(registry_name)
    return bool(a) and bool(b) and (a <= b or b <= a)


def build(bindings: list[dict[str, Any]], profiles: list[dict[str, Any]], *, retrieved_at: str) -> tuple[list[dict], list[dict]]:
    names = {str(row["organisation_number"]): str(row["name"]) for row in profiles}
    observations: list[dict] = []
    quarantined: list[dict] = []
    seen: set[str] = set()
    for binding in bindings:
        org = str((binding.get("orgnr") or {}).get("value") or "").strip()
        uri = str((binding.get("item") or {}).get("value") or "")
        label = str((binding.get("itemLabel") or {}).get("value") or "")
        match = ITEM_URI.match(uri)
        if org not in names:
            quarantined.append({"organisation_number": org, "reason": "organisation number not in company list", "item": uri})
            continue
        if not match:
            quarantined.append({"organisation_number": org, "reason": "unrecognised Wikidata item URI", "item": uri})
            continue
        if not names_agree(label, names[org]):
            quarantined.append({"organisation_number": org, "reason": "wikidata label disagrees with registry name; needs human review", "item": uri, "wikidata_label": label, "registry_name": names[org]})
            continue
        item_id = match.group(1)
        key = f"{org}|{item_id}"
        if key in seen:
            continue
        seen.add(key)
        digest = hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        observations.append(
            {
                "id": f"wikidata-{org}-{item_id}",
                "organisation_number": org,
                "platform": "wikidata",
                "signal_type": "company_profile",
                "source_url": f"https://www.wikidata.org/wiki/{item_id}",
                "retrieved_at": retrieved_at,
                "content_sha256": digest,
                "exact_entity": True,
                "identity_proof": [
                    {"type": "wikidata_p2333_organisation_number", "value": org, "item": item_id},
                    {"type": "registry_name_agreement", "wikidata_label": label, "registry_name": names[org]},
                ],
                "acquisition_mode": "official_api",
                "rights_status": "approved",
                "source_class": "wikidata_open_data",
                "evidence_span": f"Wikidata item {item_id} ({label}) lists Norwegian organisation number {org} (property P2333).",
                "metrics": {"wikidata_item": item_id, "interpretation": "Presence of an open-data entity record; not popularity or sentiment."},
                "strategy": "wikidata_p2333_lookup",
            }
        )
    return observations, quarantined


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact-entity Wikidata observations from saved query results.")
    parser.add_argument("--matches", required=True, help="out/wikidata-matches.json from scripts/query_wikidata.py")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--retrieved-at", help="ISO time the query ran; default is the matches file's modification time")
    args = parser.parse_args()
    matches_path = Path(args.matches)
    bindings = json.loads(matches_path.read_text(encoding="utf-8"))
    profiles = [json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
    retrieved_at = args.retrieved_at or datetime.fromtimestamp(matches_path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    observations, quarantined = build(bindings, profiles, retrieved_at=retrieved_at)
    Path(args.output).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in observations), encoding="utf-8")
    report = {
        "connector": "wikidata_p2333_lookup_v1",
        "bindings_read": len(bindings),
        "observations": len(observations),
        "quarantined": quarantined,
        "retrieved_at": retrieved_at,
        "claim_boundary": "Exact organisation-number match plus agreeing name. Proves an open-data entity record exists; says nothing about popularity or sentiment.",
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
