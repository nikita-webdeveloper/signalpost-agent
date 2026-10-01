#!/usr/bin/env python3
"""
Glue step between the crawl and the rest of the pipeline.

scripts/run_scrapy_websites.py writes its result to a NEW file (the 325-row
subset, with evidence.website now filled in / identity-gated). It does not
touch the master out/profiles.jsonl. This script folds those 325 updated
rows back into a full 1000-row profiles file so the master record stays
authoritative and every later step (extract_company_site_activity.py,
merge_observations.py, evaluate_external_footprint.py,
score_competition_v3.py) can just be pointed at one current profiles file.

Input:  out/profiles.jsonl (1000 rows, current master)
        out/breadth-untouched-crawl-profiles-v1.jsonl (325 rows, crawl output)
Output: out/profiles-breadth-untouched-v1.jsonl (1000 rows, updated master)

Refuses to run if the crawl output's row count doesn't match what Step 1
selected, or if it contains an organisation number outside the base file,
so a bad crawl file can't silently corrupt the master profile set.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    base_path = OUT / "profiles.jsonl"
    crawl_path = OUT / "breadth-untouched-crawl-profiles-v1.jsonl"
    out_path = OUT / "profiles-breadth-untouched-v1.jsonl"

    if out_path.exists():
        raise SystemExit(f"{out_path} already exists; choose a new name or delete it first.")

    base = read_jsonl(base_path)
    crawled = read_jsonl(crawl_path)
    base_orgs = {row["organisation_number"] for row in base}
    crawled_by_org = {row["organisation_number"]: row for row in crawled}

    unknown = set(crawled_by_org) - base_orgs
    if unknown:
        raise SystemExit(f"{len(unknown)} organisation numbers in the crawl file are not in profiles.jsonl: {sorted(unknown)[:5]}...")

    updated_rows = [crawled_by_org.get(row["organisation_number"], row) for row in base]
    write_jsonl(out_path, updated_rows)
    print(json.dumps({
        "base_rows": len(base),
        "rows_replaced_with_crawl_result": len(crawled_by_org),
        "output": str(out_path),
    }, indent=2))


if __name__ == "__main__":
    main()
