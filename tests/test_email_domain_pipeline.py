from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.email_domain_discovery import discover_website_from_email_domain  # noqa: E402
from scripts.run_scrapy_websites import apply_email_domain_fallback  # noqa: E402


class EmailDomainPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        profiles_path = ROOT / "out" / "profiles.jsonl"
        cls.profiles = {
            str(profile["organisation_number"]): profile
            for profile in (
                json.loads(line)
                for line in profiles_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }

    def test_uncertain_email_domain_is_retained_but_not_promoted(self):
        record, _ = discover_website_from_email_domain(self.profiles["921117817"])
        assessment = (record.get("value") or {}).get("identity_assessment") or {}

        self.assertEqual(record["status"], "available")
        self.assertEqual(record["source_type"], "registry_email_domain_company_website")
        self.assertEqual(assessment["status"], "related_or_uncertain")
        self.assertEqual(len(record["content_sha256"]), 64)

    def test_exact_email_domain_is_publishable(self):
        record, _ = discover_website_from_email_domain(self.profiles["985589003"])
        assessment = (record.get("value") or {}).get("identity_assessment") or {}

        self.assertEqual(record["status"], "available")
        self.assertEqual(assessment["status"], "exact")
        self.assertTrue(assessment["publishable"])

    def test_materializer_promotes_only_exact_identity(self):
        uncertain = json.loads(json.dumps(self.profiles["921117817"]))
        exact = json.loads(json.dumps(self.profiles["985589003"]))

        uncertain_record, _ = apply_email_domain_fallback(uncertain)
        exact_record, _ = apply_email_domain_fallback(exact)

        self.assertEqual(uncertain_record["status"], "available")
        self.assertEqual(uncertain["website"], "")
        self.assertEqual(exact["website"], exact_record["value"]["final_url"])


if __name__ == "__main__":
    unittest.main()