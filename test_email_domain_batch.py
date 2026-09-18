from __future__ import annotations

import json
import sys
from collections import Counter

ROOT = __import__("pathlib").Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.email_domain_discovery import (  # noqa: E402
    discover_website_from_email_domain,
    extract_email_domain,
)


def main() -> None:
    rows = [
        json.loads(line)
        for line in (ROOT / "out" / "profiles.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    candidates = [
        profile
        for profile in rows
        if not profile.get("website") and extract_email_domain(profile)
    ]

    by_domain: dict[str, list[dict]] = {}
    for profile in candidates:
        domain = extract_email_domain(profile)
        if domain:
            by_domain.setdefault(domain, []).append(profile)

    counts = Counter()
    exact_matches = []
    domain_results = []

    for domain, profiles in sorted(by_domain.items()):
        profile = profiles[0]
        record, metrics = discover_website_from_email_domain(profile)
        assessment = (record.get("value") or {}).get("identity_assessment") or {}
        status = assessment.get("status") or record.get("status") or "unknown"
        counts[status] += 1

        result = {
            "domain": domain,
            "companies": [
                {
                    "organisation_number": p.get("organisation_number"),
                    "name": p.get("name"),
                }
                for p in profiles
            ],
            "status": status,
            "score": assessment.get("score"),
            "source_url": record.get("source_url"),
            "requests": metrics.get("requests", 0),
        }
        domain_results.append(result)

        if assessment.get("status") == "exact":
            exact_matches.append(result)

    print(json.dumps({
        "candidate_profiles": len(candidates),
        "unique_domains_tested": len(by_domain),
        "identity_counts": dict(counts),
        "exact_matches": exact_matches,
        "total_requests": sum(item["requests"] for item in domain_results),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
