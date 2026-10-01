#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.sentiment import calculate_sentiment_metrics, evaluate_predictions  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate sentiment predictions against frozen gold labels.")
    parser.add_argument("--gold", required=True, help="Path to gold sentiment JSONL file")
    parser.add_argument("--predictions", required=True, help="Path to predictions JSONL file")
    parser.add_argument("--output", required=True, help="Path to output JSON report")
    parser.add_argument("--minimum-items", type=int, default=300, help="Minimum gold items required for full gate (default: 300)")
    parser.add_argument("--allow-smoke", action="store_true", help="Allow running on smoke gold sets smaller than 300 items")
    parser.add_argument("--held-out", action="store_true", help="Designate evaluation as organizer-owned held-out evaluation")
    args = parser.parse_args()

    gold = read_jsonl(Path(args.gold))
    predictions = read_jsonl(Path(args.predictions))

    if args.allow_smoke and len(gold) < args.minimum_items:
        metrics = calculate_sentiment_metrics(gold, predictions)
        report = {
            "gold_items": metrics["gold_items"],
            "published_predictions": metrics["published_predictions"],
            "accuracy": metrics["accuracy"],
            "macro_f1": metrics["macro_f1"],
            "macro_f1_labels": metrics["macro_f1_labels"],
            "per_label": metrics["per_label"],
            "wrong_entity_predictions": metrics["wrong_entity_predictions"],
            "exact_entity_precision": metrics["exact_entity_precision"],
            "evidence_supported_predictions": metrics["evidence_supported_predictions"],
            "evidence_support_rate": metrics["evidence_support_rate"],
            "company_owned_predictions": metrics["company_owned_predictions"],
            "coverage": metrics["coverage"],
            "minimum_items_gate": False,
            "qualification_passed": False,
            "smoke_mode": True,
            "qualification_tier": "local_smoke_qualification",
            "qualification_note": f"Smoke evaluation on {len(gold)} items. Full qualification gate requires >= {args.minimum_items} held-out gold labels.",
        }
    else:
        report = evaluate_predictions(gold, predictions, minimum_items=args.minimum_items)
        report["smoke_mode"] = False
        report["qualification_tier"] = "held_out_qualification" if args.held_out else "production_qualification"

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
