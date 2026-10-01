#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.reviews import (  # noqa: E402
    GooglePlacesReviewConnector,
    WebsiteReviewConnector,
    deduplicate_reviews,
    validate_review_observation,
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Production permitted reviews and ratings connector.")
    parser.add_argument("--profiles", required=True, help="JSONL company profiles")
    parser.add_argument("--organisations", help="Optional text or JSON list of organisation numbers to process")
    parser.add_argument("--output", required=True, help="Output JSONL of review observations")
    parser.add_argument("--report", required=True, help="Output JSON summary report")
    parser.add_argument("--api-key-env", default="GOOGLE_PLACES_API_KEY", help="Environment variable for Google Places API key")
    args = parser.parse_args()

    profiles = read_jsonl(Path(args.profiles))
    if args.organisations:
        org_path = Path(args.organisations)
        if org_path.suffix == ".json":
            wanted_orgs = set(json.loads(org_path.read_text(encoding="utf-8")))
        else:
            wanted_orgs = {line.strip() for line in org_path.read_text(encoding="utf-8").splitlines() if line.strip()}
        profiles = [p for p in profiles if str(p.get("organisation_number")) in wanted_orgs]

    api_key = os.environ.get(args.api_key_env) or os.environ.get("PLACES_API_KEY")
    places_connector = GooglePlacesReviewConnector(api_key=api_key)
    site_connector = WebsiteReviewConnector()

    all_observations: list[dict] = []
    company_results: list[dict] = []
    status_counts: dict[str, int] = defaultdict(int)
    rejection_reasons: dict[str, int] = defaultdict(int)

    for profile in profiles:
        org = str(profile.get("organisation_number"))
        name = str(profile.get("name"))

        # 1. Google Places (official_api)
        places_res = places_connector.fetch_places_and_reviews(profile)
        status_counts[places_res["status"]] += 1

        company_obs: list[dict] = []
        if places_res["status"] == "available":
            company_obs.extend(places_res["observations"])
        elif places_res["status"] == "rejected":
            for rej in places_res.get("rejections", []):
                for reason in rej.get("reasons", []):
                    rejection_reasons[reason] += 1

        # 2. Verified company site reviews (permitted_public_page)
        site_obs = site_connector.extract_website_reviews(profile)
        company_obs.extend(site_obs)

        # Validate and deduplicate
        valid_obs = []
        for obs in company_obs:
            reasons = validate_review_observation(obs)
            if not reasons:
                valid_obs.append(obs)
            else:
                for r in reasons:
                    rejection_reasons[f"validation_failed: {r}"] += 1

        deduped = deduplicate_reviews(valid_obs)
        all_observations.extend(deduped)

        company_results.append({
            "organisation_number": org,
            "company_name": name,
            "google_places_status": places_res["status"],
            "google_places_reason": places_res.get("reason"),
            "website_reviews_found": len(site_obs),
            "published_review_observations": len(deduped),
        })

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.output).open("w", encoding="utf-8") as f:
        for obs in all_observations:
            f.write(json.dumps(obs, ensure_ascii=False) + "\n")

    report = {
        "connector": "signalpost_permitted_reviews_connector_v1",
        "profiles_processed": len(profiles),
        "places_api_configured": places_connector.is_available,
        "places_status_counts": dict(status_counts),
        "total_published_observations": len(all_observations),
        "review_summaries": sum(item.get("signal_type") == "review_summary" for item in all_observations),
        "individual_reviews": sum(item.get("signal_type") == "review" for item in all_observations),
        "companies_with_reviews": len({item["organisation_number"] for item in all_observations}),
        "rejection_reasons": dict(rejection_reasons),
        "claim_boundary": "Permitted reviews from official API and verified website structured data only; unauthorized scraping is prohibited.",
        "company_results_sample": company_results[:20],
    }

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "company_results_sample"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
