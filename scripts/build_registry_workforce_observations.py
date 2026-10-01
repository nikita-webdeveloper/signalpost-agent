#!/usr/bin/env python3
"""Turn registry-reported employee counts already fetched from Enhetsregisteret into
``workforce_snapshot`` observations.

Every profile carries a ``registry_live`` evidence record fetched from the official
Bronnoysund API (``data.brreg.no/enhetsregisteret/api/enheter/<orgnr>``) with its real
retrieval time and payload hash. When that record reports ``employees`` this is a real,
official workforce data point that was never emitted as an observation.

Guardrails (all abstain rather than guess):
- the live record must be ``available`` and its payload organisation number must equal the
  profile's organisation number, and the source URL must end in that number;
- ``employees`` must be a non-negative integer that agrees with the profile's top-level value;
- bankrupt / liquidating companies abstain (a registered headcount is likely stale);
- retrieval time and payload hash are copied from the real fetch, never generated here;
- the measure is labelled ``registered_employees`` so it can never be confused with the
  full-time-equivalent figures from annual reports.

Nothing is fetched by this script; it only re-reads evidence that is already in the profiles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external_footprint import validate_observation  # noqa: E402

STRATEGY = "registry_reported_employees"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def observation(profile: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Return (observation, status). status is ``accepted`` or the abstain reason."""
    org = str(profile.get("organisation_number") or "")
    live = (profile.get("evidence") or {}).get("registry_live") or {}
    value = live.get("value") or {}
    if live.get("status") != "available":
        return None, "registry_live_unavailable"
    if str(value.get("organisation_number") or "") != org or not org.isdigit():
        return None, "organisation_number_mismatch"
    source_url = str(live.get("source_url") or "")
    if not source_url.rstrip("/").endswith("/" + org):
        return None, "source_url_not_for_this_organisation"
    employees = value.get("employees")
    if employees is None:
        return None, "no_registered_employee_count"
    if isinstance(employees, bool) or not isinstance(employees, int) or employees < 0:
        return None, "invalid_employee_count"
    if profile.get("employees") is not None and profile.get("employees") != employees:
        return None, "conflicting_employee_counts"
    if value.get("bankrupt") or value.get("liquidating"):
        return None, "bankrupt_or_liquidating"
    digest = str(live.get("content_sha256") or "")
    retrieved_at = live.get("retrieved_at")
    if len(digest) != 64 or not retrieved_at:
        return None, "missing_provenance"
    row = {
        "id": "registry-workforce-" + hashlib.sha256(f"{org}|{digest}|employees".encode()).hexdigest()[:24],
        "organisation_number": org,
        "platform": "brreg",
        "signal_type": "workforce_snapshot",
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "content_sha256": digest,
        "exact_entity": True,
        "identity_proof": [
            {"type": "official_registry_url_organisation_number", "value": org},
            {"type": "registry_payload_organisation_number", "value": org},
        ],
        "acquisition_mode": "official_api",
        "rights_status": "approved",
        "source_class": "official_registry_live",
        "evidence_span": f"Enhetsregisteret reports antall ansatte = {employees} for organisation number {org}.",
        "metrics": {
            "workforce_value": employees,
            "measure": "registered_employees",
            "scope": "registry_reported",
            "interpretation": "Headcount as self-reported to the registry; not full-time equivalents and not a job-posting signal.",
        },
        "strategy": STRATEGY,
    }
    problems = validate_observation(row)
    if problems:
        return None, "failed_validation:" + ";".join(problems)
    return row, "accepted"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build workforce observations from registry-reported employee counts.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    profiles = read_jsonl(Path(args.profiles))
    rows: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    for profile in profiles:
        row, status = observation(profile)
        statuses[status] += 1
        if row:
            rows.append(row)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    report = {
        "connector": "registry_reported_employees_v1",
        "profiles": len(profiles),
        "observations": len(rows),
        "status_counts": dict(statuses),
        "claim_boundary": "Registry-reported headcount only; re-reads evidence already fetched from the official API and fetches nothing.",
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
