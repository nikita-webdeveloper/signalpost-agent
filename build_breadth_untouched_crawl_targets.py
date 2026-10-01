#!/usr/bin/env python3
"""
Step 1 of the breadth-recovery run.

Reads the existing analysis file out/breadth-one-platform-targets-v1.jsonl and
selects the companies that have exactly one qualifying platform AND have never
had a website candidate put through the identity gate.

There is no literal `attempted_second_platform_crawl` field anywhere in this
repo. The operational stand-in used here -- and the one that reproduces the
325 figure -- is `website_identity_status == "not_found"` on the one-platform
analysis row. That status means no website for the company has ever been
fetched and identity-checked, i.e. a second-platform attempt was never made.

Output: out/breadth-untouched-crawl-targets-v1.jsonl
  - one row per selected company, in the SAME schema as out/profiles.jsonl
    (pulled from profiles.jsonl by organisation_number), so this file can be
    passed directly as --input to scripts/run_scrapy_websites.py.

Also writes a short diagnostic report to stdout and to
out/breadth-untouched-crawl-targets-v1-report.json, because most of these 325
companies do not have any real website candidate to crawl (see explanation
below) and that needs to be visible before anyone spends time running a
crawl.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"

REGISTRY_PLACEHOLDER = "https://data.brreg.no/enhetsregisteret/api/enheter"

GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "live.no", "msn.com", "yahoo.com", "yahoo.no", "yahoo.fr", "icloud.com",
    "me.com", "mac.com", "proton.me", "protonmail.com",
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    targets = read_jsonl(OUT / "breadth-one-platform-targets-v1.jsonl")
    profiles = read_jsonl(OUT / "profiles.jsonl")
    profile_by_org = {p["organisation_number"]: p for p in profiles}

    selected = [t for t in targets if t.get("website_identity_status") == "not_found"]

    missing = [t["organisation_number"] for t in selected if t["organisation_number"] not in profile_by_org]
    if missing:
        raise SystemExit(f"{len(missing)} selected organisation numbers are not in profiles.jsonl: {missing[:5]}...")

    out_rows = [profile_by_org[t["organisation_number"]] for t in selected]
    out_path = OUT / "breadth-untouched-crawl-targets-v1.jsonl"
    write_jsonl(out_path, out_rows)

    no_site_at_all = sum(1 for t in selected if t.get("website") == REGISTRY_PLACEHOLDER)
    has_email = [t for t in selected if t.get("email_domain")]
    non_generic_email = [t for t in has_email if t["email_domain"].lower() not in GENERIC_EMAIL_DOMAINS]
    orgnrs_with_candidate = sorted(t["organisation_number"] for t in non_generic_email)

    orgnr_list_path = OUT / "breadth-untouched-crawl-orgnrs-with-email-candidate-v1.txt"
    orgnr_list_path.write_text("\n".join(orgnrs_with_candidate) + "\n", encoding="utf-8")

    report = {
        "candidate_rows_selected": len(selected),
        "rows_with_no_real_website_candidate_at_all": no_site_at_all,
        "rows_with_any_email_domain": len(has_email),
        "rows_with_plausible_non_generic_email_domain": len(non_generic_email),
        "note": (
            "run_scrapy_websites.py only crawls a row if profiles.jsonl already has a non-empty "
            "'website' field. None of these 325 rows have one -- the field is empty in profiles.jsonl "
            "and the analysis file's 'website' value is just the BRREG bulk-download API URL, not a "
            "company site. So the Scrapy spider itself will not fetch anything for this batch. The "
            "only mechanism in run_scrapy_websites.py that can still do something is its automatic "
            "email-domain fallback (discover_website_from_email_domain), which tries https://<domain> "
            "for the registry-listed contact email's domain and runs it through the normal identity "
            "gate. That only has a domain to try for the "
            f"{len(non_generic_email)} companies listed in "
            "breadth-untouched-crawl-orgnrs-with-email-candidate-v1.txt. Expect most of the remaining "
            f"{len(selected) - len(non_generic_email)} companies to end this run still unmatched, "
            "through no fault of the command -- there is nothing for it to fetch."
        ),
    }
    (OUT / "breadth-untouched-crawl-targets-v1-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nwrote {len(out_rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
