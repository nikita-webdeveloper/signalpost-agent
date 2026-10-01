#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.research import answer_profile, screen_profiles  # noqa: E402
from norway_company_agent.workspace import empty_workspace, record_screen, save_workspace  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Signalpost research-agent QA and screening performance.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line.strip()]
    by_org = {str(row["organisation_number"]): row for row in rows}
    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))

    qa_results = []
    qa_supported_count = 0
    external_qa_passed = True

    # Support either single_company or qa_cases schema
    qa_cases = []
    if "single_company" in suite:
        qa_cases.append({
            "id": "single_company",
            **suite["single_company"],
        })
    elif "qa_cases" in suite:
        qa_cases.extend(suite["qa_cases"])

    for spec in qa_cases:
        org = str(spec["organisation_number"])
        if org not in by_org:
            # When evaluating a benchmark against a specific sample, skip unrepresented orgs
            continue
        profile = by_org[org]
        answer = answer_profile(profile, spec["question"])
        facts = answer.get("facts", [])
        facts_valid = all(
            bool(item.get("source_url") and item.get("retrieved_at") and item.get("content_sha256"))
            for item in facts
        )
        min_facts = spec.get("minimum_facts", 1)
        facts_count_ok = len(facts) >= min_facts
        required_claims = spec.get("required_claims", [])
        present_claims = {item.get("claim") for item in facts}
        required_ok = all(claim in present_claims for claim in required_claims) if required_claims else True
        supported = bool(facts_valid and facts_count_ok and required_ok)
        if supported:
            qa_supported_count += 1

        # External footprint QA check: social links or external items must be verified
        if any(term in spec["question"].casefold() for term in ("social", "website", "online", "footprint")):
            if not facts_valid:
                external_qa_passed = False

        qa_results.append({
            "id": spec.get("id"),
            "organisation_number": org,
            "question": spec["question"],
            "facts_count": len(facts),
            "minimum_facts": min_facts,
            "facts_valid": facts_valid,
            "required_claims_present": required_ok,
            "supported": supported,
        })

    single_supported = qa_supported_count == len(qa_cases) if qa_cases else True

    # Support either screens or screen_cases
    screen_specs = []
    if "screens" in suite:
        screen_specs = suite["screens"]
    elif "screen_cases" in suite:
        screen_specs = suite["screen_cases"]

    screen_results = []
    exact_screens = 0
    exact_plans = 0
    export_supported = True
    workspace = empty_workspace()

    for spec in screen_specs:
        query = spec["query"]
        result = screen_profiles(rows, query)
        actual_orgs = [item["organisation_number"] for item in result["results"]]
        plan = result.get("plan", {})
        plan_filters = [{k: v for k, v in f.items() if k != "evidence_module"} for f in plan.get("filters", [])]

        if "expected_organisation_numbers" in spec:
            exact = actual_orgs == spec["expected_organisation_numbers"]
        else:
            exact = True

        if "expected_filter_fields" in spec:
            plan_fields = [item["field"] for item in plan.get("filters", [])]
            plan_exact = plan_fields == spec["expected_filter_fields"]
        elif "oracle" in spec:
            oracle = spec["oracle"]
            filters_ok = plan_filters == oracle.get("filters", [])
            sort_ok = plan.get("sort") == oracle.get("sort")
            plan_exact = filters_ok and sort_ok
        else:
            plan_exact = True

        citations_complete = all(
            citation.get("source_url") and citation.get("retrieved_at") and citation.get("content_sha256")
            for item in result.get("results", []) for citation in item.get("citations", [])
        )
        exact_screens += int(exact)
        exact_plans += int(plan_exact)
        export_supported = export_supported and citations_complete
        workspace = record_screen(workspace, result, pin_organisations=actual_orgs[:1])
        screen_results.append({
            "query": query,
            "actual_count": len(actual_orgs),
            "exact_membership_and_order": exact,
            "exact_plan": plan_exact,
            "citations_complete": citations_complete,
        })

    save_workspace(args.workspace, workspace)
    saved_work = Path(args.workspace).exists() and len(workspace.get("history", [])) == len(screen_specs)

    # Support either unsupported or unsupported_cases
    unsupported_specs = []
    if "unsupported" in suite:
        unsupported_specs = [suite["unsupported"]]
    elif "unsupported_cases" in suite:
        unsupported_specs = suite["unsupported_cases"]

    unsupported_abstentions = 0
    for spec in unsupported_specs:
        res = screen_profiles(rows, spec["query"])
        must_abstain = spec.get("must_abstain", True)
        if res.get("abstained") is must_abstain:
            unsupported_abstentions += 1

    total_unsupported = len(unsupported_specs) or 1
    abstention_rate = unsupported_abstentions / total_unsupported if unsupported_specs else 1.0
    abstention = abstention_rate == 1.0

    screen_count = len(screen_specs) or 1
    screen_rate = exact_screens / screen_count
    plan_rate = exact_plans / screen_count

    points = {
        "cited_single_company_qa": 4.0 if single_supported else round(4.0 * (qa_supported_count / len(qa_cases)), 3) if qa_cases else 4.0,
        "cross_company_screening": round(3.0 * screen_rate, 3),
        "exact_inspectable_filters": round(2.0 * plan_rate, 3),
        "unsupported_abstention": 1.0 if abstention else 0.0,
        "saved_history": 1.0 if saved_work else 0.0,
        "provenance_export": 1.0 if export_supported else 0.0,
    }
    total_score = round(sum(points.values()), 3)
    qualification = bool(
        single_supported
        and screen_rate == 1.0
        and plan_rate == 1.0
        and abstention
        and saved_work
        and export_supported
        and external_qa_passed
    )

    report = {
        "corpus": suite.get("corpus", "Signalpost research-agent suite"),
        "profiles": len(rows),
        "questions": len(qa_cases) + len(screen_specs) + len(unsupported_specs),
        "single_company_supported": single_supported,
        "qa_results": qa_results,
        "screen_results": screen_results,
        "unsupported_abstention": abstention,
        "external_footprint_qa_passed": external_qa_passed,
        "saved_work": saved_work,
        "provenance_export": export_supported,
        "points": points,
        "score": total_score,
        "maximum": 12,
        "qualification_passed": qualification,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("screen_results", "qa_results")}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["qualification_passed"] else 1)


if __name__ == "__main__":
    main()

