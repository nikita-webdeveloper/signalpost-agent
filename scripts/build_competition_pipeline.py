#!/usr/bin/env python3
"""Signalpost Norway External-Footprint Competition Pipeline.

Produces a fully verified, clean, reproducible merged external-observations dataset,
evaluates qualification and exact-entity fidelity, and executes the official competition scorer.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external_footprint import (
    publishable_observation,
    validate_observation,
    safe_read_jsonl,
)
from norway_company_agent.sentiment import generate_sentiment_prediction
from norway_company_agent.social_observations import social_observations_from_website
from scripts.extract_company_site_activity import observation as activity_obs
from scripts.extract_company_site_news import observation as news_obs
from scripts.merge_observations import merge


def build_pipeline(
    *,
    profiles_path: Path,
    baseline_path: Path,
    output_obs_path: Path,
    eval_report_path: Path,
    score_report_path: Path,
    ledger_path: Path,
    version_tag: str = "v7-final",
) -> dict[str, Any]:
    profiles = safe_read_jsonl(profiles_path)
    known_orgs = {str(p["organisation_number"]) for p in profiles}
    print(f"Loaded {len(profiles)} company profiles ({len(known_orgs)} unique org numbers).")

    baseline = safe_read_jsonl(baseline_path)
    print(f"Loaded {len(baseline)} baseline observations from {baseline_path.name}.")

    candidate_groups: list[list[dict[str, Any]]] = []

    # 1. Harvest social handles from all profile variants
    profile_sources = [
        ROOT / "out" / "profiles-email-fallback-v1.jsonl",
        ROOT / "out" / "profiles-breadth-untouched-v1.jsonl",
        ROOT / "out" / "step4-profiles.jsonl",
        ROOT / "out" / "profiles-review-pages-v1.jsonl",
        ROOT / "out" / "profiles.jsonl",
    ]

    social_candidates: list[dict[str, Any]] = []
    for ps in profile_sources:
        if not ps.exists():
            continue
        p_rows = safe_read_jsonl(ps)
        for p in p_rows:
            if isinstance(p, dict) and str(p.get("organisation_number")) in known_orgs:
                socs = social_observations_from_website(p)
                for s in socs:
                    if publishable_observation(s):
                        social_candidates.append(s)

    print(f"Discovered {len(social_candidates)} publishable social handle observations.")
    candidate_groups.append(social_candidates)

    # 2. Harvest company site activity metrics
    activity_candidates: list[dict[str, Any]] = []
    for ps in profile_sources:
        if not ps.exists():
            continue
        p_rows = safe_read_jsonl(ps)
        for p in p_rows:
            if isinstance(p, dict) and str(p.get("organisation_number")) in known_orgs:
                act = activity_obs(p)
                if act and publishable_observation(act):
                    activity_candidates.append(act)

    print(f"Discovered {len(activity_candidates)} publishable company-site activity observations.")
    candidate_groups.append(activity_candidates)

    # 3. Harvest company site news posts
    site_news_candidates: list[dict[str, Any]] = []
    for ps in profile_sources:
        if not ps.exists():
            continue
        p_rows = safe_read_jsonl(ps)
        for p in p_rows:
            if isinstance(p, dict) and str(p.get("organisation_number")) in known_orgs:
                sn = news_obs(p)
                if sn and publishable_observation(sn):
                    site_news_candidates.append(sn)

    print(f"Discovered {len(site_news_candidates)} publishable company-site news observations.")
    candidate_groups.append(site_news_candidates)

    # 4. Wikidata open-data observations
    wiki_path = ROOT / "out" / "external-observations-wikidata.jsonl"
    if wiki_path.exists():
        wiki_obs = safe_read_jsonl(wiki_path)
        valid_wiki = [w for w in wiki_obs if publishable_observation(w)]
        print(f"Discovered {len(valid_wiki)} publishable Wikidata observations.")
        candidate_groups.append(valid_wiki)

    # 5. Merge safely
    merged, merge_details = merge(baseline, candidate_groups, known_orgs)
    print(f"Merge result: {len(baseline)} base + {merge_details['counts'].get('added', 0)} new = {len(merged)} total observations.")

    # 6. Sentiment enrichment for any unlabelled independent sentiment observations
    sentiment_enriched = 0
    for obs in merged:
        if obs.get("source_class") in {"public_news", "customer_review", "employee_review", "licensed_news", "public_mention"} and not obs.get("sentiment_label"):
            try:
                pred = generate_sentiment_prediction(obs)
                obs["sentiment_label"] = pred["label"]
                obs["sentiment_model_version"] = pred["sentiment_model_version"]
                sentiment_enriched += 1
            except Exception:
                pass

    if sentiment_enriched:
        print(f"Enriched {sentiment_enriched} observations with deterministic sentiment.")

    # 7. Write versioned output
    output_obs_path.parent.mkdir(parents=True, exist_ok=True)
    with output_obs_path.open("w", encoding="utf-8") as f:
        for obs in merged:
            f.write(json.dumps(obs, ensure_ascii=False) + "\n")
    print(f"Wrote merged observations to {output_obs_path.name}.")

    # 8. Run evaluate_external_footprint.py
    eval_cmd = [
        sys.executable,
        str(ROOT / "scripts" / "evaluate_external_footprint.py"),
        "--profiles", str(profiles_path),
        "--observations", str(output_obs_path),
        "--labels", str(ROOT / "out" / "observation-audit-labels.jsonl"),
        "--output", str(eval_report_path),
    ]
    subprocess.run(eval_cmd, check=True)
    eval_data = json.loads(eval_report_path.read_text(encoding="utf-8"))

    # 9. Run score_competition_v3.py
    score_cmd = [
        sys.executable,
        str(ROOT / "scripts" / "score_competition_v3.py"),
        "--profiles", str(profiles_path),
        "--external-report", str(eval_report_path),
        "--batch-report", str(ROOT / "out" / "step4-batch-report.json"),
        "--resume-report", str(ROOT / "reports_v2" / "resume-report-v2.json"),
        "--refresh-report", str(ROOT / "reports_v2" / "refresh-report-v2.json"),
        "--research-report", str(ROOT / "reports_v2" / "research-report-v2.json"),
        "--sentiment-report", str(ROOT / "out" / "sentiment-report.json"),
        "--ux-report", str(ROOT / "reports_v2" / "ux-report-v2.json"),
        "--output", str(score_report_path),
    ]
    subprocess.run(score_cmd, check=True)
    score_data = json.loads(score_report_path.read_text(encoding="utf-8"))

    # 10. Ledger logging
    ledger_entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "version": version_tag,
        "source_added": "all_approved_harvest_sources",
        "observations_total": len(merged),
        "observations_added": merge_details["counts"].get("added", 0),
        "unique_companies_covered": sum(1 for c in eval_data["coverage"].values() if c > 0),
        "raw_score": score_data["raw_score"],
        "awardable_score": score_data["awardable_score"],
        "target_met": score_data["target_met"],
        "qualification_passed": score_data["qualification_passed"],
        "details_external": score_data["details"]["external"],
        "platform_counts": eval_data.get("platform_counts", {}),
    }

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ledger_entry, ensure_ascii=False) + "\n")

    print("\n" + "=" * 60)
    print(f"PIPELINE RUN COMPLETE: {version_tag}")
    print(f"Total Observations: {len(merged)} (+{merge_details['counts'].get('added', 0)} added)")
    print(f"External Footprint Intelligence: {score_data['category_scores']['external_footprint_intelligence']} / 55")
    print(f"Total Awardable Score: {score_data['awardable_score']} / {score_data['target']}")
    print(f"Qualification Passed: {score_data['qualification_passed']}")
    print("=" * 60)
    return {
        "eval": eval_data,
        "score": score_data,
        "merge": merge_details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full Signalpost external-footprint pipeline.")
    parser.add_argument("--profiles", default=str(ROOT / "out" / "profiles.jsonl"))
    parser.add_argument("--baseline", default=str(ROOT / "out" / "external-observations-breadth-v5-sentiment17.jsonl"))
    parser.add_argument("--output", default=str(ROOT / "out" / "external-observations-v7-final.jsonl"))
    parser.add_argument("--eval-report", default=str(ROOT / "out" / "external-report-v7-final.json"))
    parser.add_argument("--score-report", default=str(ROOT / "out" / "competition-score-v7-final.json"))
    parser.add_argument("--ledger", default=str(ROOT / "out" / "score-experiments.jsonl"))
    parser.add_argument("--version", default="v7-final")
    args = parser.parse_args()

    build_pipeline(
        profiles_path=Path(args.profiles),
        baseline_path=Path(args.baseline),
        output_obs_path=Path(args.output),
        eval_report_path=Path(args.eval_report),
        score_report_path=Path(args.score_report),
        ledger_path=Path(args.ledger),
        version_tag=args.version,
    )


if __name__ == "__main__":
    main()
