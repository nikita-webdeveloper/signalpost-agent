from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external_footprint import validate_observation
from norway_company_agent.news import (
    build_news_observation,
    fetch_news_article,
)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Permitted independent-news observation connector."
    )
    parser.add_argument(
        "--profiles",
        required=True,
        help="JSONL company profiles",
    )
    parser.add_argument(
        "--articles",
        required=True,
        help="JSONL article records containing organisation_number, article_url, and optional publisher/title",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output JSONL of accepted news observations",
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Output JSON summary report",
    )
    parser.add_argument(
        "--acquisition-mode",
        default="permitted_public_page",
        choices=[
            "official_api",
            "licensed_api",
            "company_authorized_export",
            "permitted_public_page",
        ],
    )
    parser.add_argument(
        "--rights-status",
        default="approved",
        choices=["approved", "unknown", "restricted"],
    )
    args = parser.parse_args()

    profiles = read_jsonl(Path(args.profiles))
    articles = read_jsonl(Path(args.articles))

    profiles_by_org = {
        str(profile.get("organisation_number")): profile
        for profile in profiles
        if str(profile.get("organisation_number") or "").strip()
    }

    observations: list[dict] = []
    accepted_count = 0
    quarantined_count = 0
    rejection_reasons: dict[str, int] = defaultdict(int)
    fetch_status_counts: dict[str, int] = defaultdict(int)

    for article in articles:
        org = str(
            article.get("organisation_number") or ""
        ).strip()

        profile = profiles_by_org.get(org)

        if profile is None:
            quarantined_count += 1
            rejection_reasons[
                "organisation_not_found_in_profiles"
            ] += 1
            continue

        article_url = str(
            article.get("article_url")
            or article.get("url")
            or ""
        ).strip()

        publisher = str(
            article.get("publisher") or ""
        ).strip()

        supplied_title = str(
            article.get("title") or ""
        ).strip()

        fetched, metrics = fetch_news_article(
            article_url
        )

        fetch_status = str(
            fetched.get("status") or "unknown"
        )
        fetch_status_counts[fetch_status] += 1

        if fetch_status != "available":
            quarantined_count += 1
            rejection_reasons[
                f"fetch_{fetch_status}"
            ] += 1
            continue

        title = supplied_title or str(
            fetched.get("title") or ""
        )

        observation = build_news_observation(
            profile,
            article_url=str(
                fetched.get("url") or article_url
            ),
            article_title=title,
            article_text=str(
                fetched.get("text") or ""
            ),
            publisher=publisher,
            acquisition_mode=args.acquisition_mode,
            rights_status=args.rights_status,
        )

        reasons = validate_observation(observation)

        if reasons:
            quarantined_count += 1

            for reason in reasons:
                rejection_reasons[
                    f"validation_failed: {reason}"
                ] += 1

            continue

        if not observation.get("publishable"):
            quarantined_count += 1

            for reason in observation.get(
                "reasons", []
            ):
                rejection_reasons[reason] += 1

            continue

        observations.append(observation)
        accepted_count += 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open(
        "w",
        encoding="utf-8",
    ) as handle:
        for observation in observations:
            handle.write(
                json.dumps(
                    observation,
                    ensure_ascii=False,
                )
                + "\n"
            )

    report = {
        "connector": "signalpost_permitted_news_connector_v1",
        "profiles_processed": len(profiles),
        "article_inputs": len(articles),
        "accepted_observations": accepted_count,
        "quarantined_inputs": quarantined_count,
        "fetch_status_counts": dict(
            fetch_status_counts
        ),
        "rejection_reasons": dict(
            rejection_reasons
        ),
        "acquisition_mode": args.acquisition_mode,
        "rights_status": args.rights_status,
        "claim_boundary": (
            "Only permitted publisher article URLs are "
            "publishable evidence. Google News RSS/index "
            "URLs are discovery-only and must not be "
            "stored as article evidence."
        ),
        "request_count": sum(
            1
            for _ in articles
        ),
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()