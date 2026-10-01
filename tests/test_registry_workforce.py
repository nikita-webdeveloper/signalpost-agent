from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.external_footprint import publishable_observation  # noqa: E402
from scripts.build_registry_workforce_observations import observation  # noqa: E402
from scripts.merge_observations import merge  # noqa: E402

ORG = "985589003"
DIGEST = "a" * 64


def profile(**overrides) -> dict:
    live_value = {
        "organisation_number": ORG,
        "name": "EXAMPLE AS",
        "employees": 5,
        "bankrupt": False,
        "liquidating": False,
    }
    base = {
        "organisation_number": ORG,
        "name": "EXAMPLE AS",
        "employees": 5,
        "evidence": {
            "registry_live": {
                "status": "available",
                "source_url": f"https://data.brreg.no/enhetsregisteret/api/enheter/{ORG}",
                "retrieved_at": "2026-09-16T04:26:45.802785Z",
                "content_sha256": DIGEST,
                "value": live_value,
            }
        },
    }
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if key == "live_value":
            result["evidence"]["registry_live"]["value"].update(value)
        elif key == "live":
            result["evidence"]["registry_live"].update(value)
        else:
            result[key] = value
    return result


class RegistryWorkforceTests(unittest.TestCase):
    def test_accepts_real_registry_headcount_with_original_provenance(self) -> None:
        row, status = observation(profile())
        self.assertEqual(status, "accepted")
        assert row is not None
        self.assertTrue(publishable_observation(row))
        self.assertEqual(row["retrieved_at"], "2026-09-16T04:26:45.802785Z")
        self.assertEqual(row["content_sha256"], DIGEST)
        self.assertEqual(row["metrics"]["workforce_value"], 5)
        self.assertEqual(row["metrics"]["measure"], "registered_employees")
        self.assertEqual(row["acquisition_mode"], "official_api")

    def test_abstains_without_registered_count(self) -> None:
        self.assertEqual(observation(profile(employees=None, live_value={"employees": None}))[1], "no_registered_employee_count")

    def test_abstains_on_organisation_number_mismatch(self) -> None:
        self.assertEqual(observation(profile(live_value={"organisation_number": "123456789"}))[1], "organisation_number_mismatch")

    def test_abstains_when_source_url_is_for_another_company(self) -> None:
        live = {"source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/123456789"}
        self.assertEqual(observation(profile(live=live))[1], "source_url_not_for_this_organisation")

    def test_abstains_on_conflicting_profile_value(self) -> None:
        self.assertEqual(observation(profile(employees=7))[1], "conflicting_employee_counts")

    def test_abstains_for_bankrupt_or_liquidating(self) -> None:
        self.assertEqual(observation(profile(live_value={"bankrupt": True}))[1], "bankrupt_or_liquidating")
        self.assertEqual(observation(profile(live_value={"liquidating": True}))[1], "bankrupt_or_liquidating")

    def test_abstains_on_negative_or_non_integer_count(self) -> None:
        self.assertEqual(observation(profile(employees=None, live_value={"employees": -1}))[1], "invalid_employee_count")
        self.assertEqual(observation(profile(employees=None, live_value={"employees": "5"}))[1], "invalid_employee_count")

    def test_abstains_without_real_provenance(self) -> None:
        self.assertEqual(observation(profile(live={"content_sha256": None}))[1], "missing_provenance")
        self.assertEqual(observation(profile(live={"retrieved_at": None}))[1], "missing_provenance")

    def test_merge_does_not_duplicate_existing_registry_workforce(self) -> None:
        row, _ = observation(profile())
        assert row is not None
        merged, details = merge([row], [[row]], {ORG})
        self.assertEqual(len(merged), 1)
        self.assertEqual(details["counts"]["duplicate_id"], 1)


if __name__ == "__main__":
    unittest.main()
