#!/usr/bin/env python3
"""Merge observation JSONL files without overwriting anything.

- Base rows are kept exactly as they are.
- Added rows are accepted only if they pass the project's own validate_observation()
  gate, belong to a known company, and are not duplicates.
- Single-valued signals (one workforce snapshot, one profile per strategy per company)
  are never added twice for the same company.
- The output file must not already exist and must differ from every input.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external_footprint import validate_observation, safe_read_jsonl  # noqa: E402

SINGLE_VALUED_SIGNALS = {"workforce_snapshot", "company_profile", "profile_metrics"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return safe_read_jsonl(path, warn_on_error=True)


def merge(base: list[dict], additions: list[list[dict]], known_orgs: set[str]) -> tuple[list[dict], dict[str, Any]]:
    merged = list(base)
    seen_ids = {str(row.get("id")) for row in base}
    single_keys = {
        (str(row.get("organisation_number")), row.get("platform"), row.get("signal_type"), row.get("strategy"))
        for row in base
        if row.get("signal_type") in SINGLE_VALUED_SIGNALS
    }
    stats: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    for group in additions:
        for row in group:
            row_id = str(row.get("id"))
            org = str(row.get("organisation_number"))
            if row_id in seen_ids:
                stats["duplicate_id"] += 1
                continue
            if org not in known_orgs:
                stats["unknown_company"] += 1
                rejected.append({"id": row_id, "reasons": ["organisation number is not in the company list"]})
                continue
            reasons = validate_observation(row)
            if reasons:
                stats["failed_validation"] += 1
                rejected.append({"id": row_id, "reasons": reasons})
                continue
            key = (org, row.get("platform"), row.get("signal_type"), row.get("strategy"))
            if row.get("signal_type") in SINGLE_VALUED_SIGNALS and key in single_keys:
                stats["duplicate_single_valued_signal"] += 1
                continue
            merged.append(row)
            seen_ids.add(row_id)
            if row.get("signal_type") in SINGLE_VALUED_SIGNALS:
                single_keys.add(key)
            stats["added"] += 1
    return merged, {"counts": dict(stats), "rejected": rejected}


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely merge validated observations into a new file.")
    parser.add_argument("--base", required=True)
    parser.add_argument("--add", action="append", required=True, help="Observation JSONL to add (repeatable)")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    output = Path(args.output).resolve()
    inputs = {Path(args.base).resolve(), *(Path(p).resolve() for p in args.add)}
    if output in inputs:
        parser.error("--output must differ from every input file")
    if output.exists():
        parser.error(f"{output} already exists; refusing to overwrite. Choose a new file name.")

    base = read_jsonl(Path(args.base))
    additions = [read_jsonl(Path(path)) for path in args.add]
    known = {str(row["organisation_number"]) for row in read_jsonl(Path(args.profiles))}
    merged, details = merge(base, additions, known)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged), encoding="utf-8")
    report = {"base_rows": len(base), "merged_rows": len(merged), **details}
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rejected"}, indent=2), f"\nrejected: {len(details['rejected'])}")


if __name__ == "__main__":
    main()
