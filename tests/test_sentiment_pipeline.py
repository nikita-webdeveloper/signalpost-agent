from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.sentiment import (
    LABELS,
    calculate_sentiment_metrics,
    classify_text_sentiment,
    evaluate_predictions,
    generate_sentiment_prediction,
    publishable_sentiment_item,
    sentiment_input_eligibility,
)


class SentimentPipelineTests(unittest.TestCase):
    def test_sentiment_classification_labels(self):
        pos_text = "Selskapet opplevde sterk vekst og leverte rekordhoyt overskudd etter a ha vunnet en stor kontrakt."
        label, version = classify_text_sentiment(pos_text)
        self.assertEqual(label, "positive")
        self.assertEqual(version, "deterministic_lexicon_v1")

        neg_text = "Selskapet melder om store tap, nedgang i omsetning og varsler oppsigelser etter svikt i markedet."
        label, version = classify_text_sentiment(neg_text)
        self.assertEqual(label, "negative")

        neutral_text = "Styret avholder ordinart styremote og publiserer statusrapport for andre kvartal."
        label, version = classify_text_sentiment(neutral_text)
        self.assertEqual(label, "neutral")

        mixed_text = "Selskapet opplevde solid omsetningsvekst, men okte driftskostnader forte til store tap og underskudd."
        label, version = classify_text_sentiment(mixed_text)
        self.assertEqual(label, "mixed")

    def test_independent_source_classes_eligibility(self):
        base_item = {
            "id": "item-1",
            "organisation_number": "938702675",
            "exact_entity": True,
            "source_url": "https://news.example/article-1",
            "retrieved_at": "2026-08-20T00:00:00Z",
            "content_sha256": "a" * 64,
            "evidence_span": "Selskapet signerte ny rammeavtale.",
            "identity_proof": [{"type": "exact_legal_name", "value": "AF GRUPPEN ASA"}],
        }

        permitted_classes = [
            "customer_review",
            "employee_review",
            "licensed_news",
            "public_news",
            "public_mention",
        ]

        for s_class in permitted_classes:
            with self.subTest(source_class=s_class):
                item = {**base_item, "source_class": s_class}
                eligible, reasons = sentiment_input_eligibility(item)
                self.assertTrue(eligible, f"Expected {s_class} to be eligible, reasons: {reasons}")

        # Company-owned marketing copy must be rejected
        unpermitted_classes = ["company_owned", "unverified_social", "internal_memo"]
        for s_class in unpermitted_classes:
            with self.subTest(unpermitted=s_class):
                item = {**base_item, "source_class": s_class}
                eligible, reasons = sentiment_input_eligibility(item)
                self.assertFalse(eligible)
                self.assertIn("source is not an allowed independent class", reasons)

    def test_generate_sentiment_prediction_preserves_all_provenance_fields(self):
        item = {
            "id": "pred-obs-101",
            "organisation_number": "938702675",
            "exact_entity": True,
            "source_class": "public_news",
            "source_url": "https://e24.no/bors/example",
            "retrieved_at": "2026-08-20T12:00:00Z",
            "effective_at": "2026-08-20T11:30:00Z",
            "content_sha256": "1" * 64,
            "evidence_span": "AF Gruppen vant kontrakt verdt 400 millioner kroner.",
            "identity_proof": [{"type": "exact_legal_name_match", "value": "AF GRUPPEN ASA"}],
        }
        pred = generate_sentiment_prediction(item)

        # Requirement 3 verification:
        self.assertEqual(pred["organisation_number"], "938702675")
        self.assertTrue(pred["exact_entity"])
        self.assertEqual(pred["identity_proof"], item["identity_proof"])
        self.assertEqual(pred["source_url"], item["source_url"])
        self.assertEqual(pred["retrieved_at"], item["retrieved_at"])
        self.assertEqual(pred["content_sha256"], item["content_sha256"])
        self.assertEqual(pred["evidence_span"], item["evidence_span"])
        self.assertEqual(pred["source_class"], "public_news")
        self.assertIn(pred["label"], LABELS)
        self.assertEqual(pred["sentiment_label"], pred["label"])
        self.assertEqual(pred["label"], "positive")
        self.assertTrue(pred["sentiment_model_version"])

    def test_review_explicit_star_rating_sentiment_prediction(self):
        review_item = {
            "id": "review-obs-202",
            "organisation_number": "938702675",
            "exact_entity": True,
            "source_class": "customer_review",
            "source_url": "https://maps.google.com/review/1",
            "retrieved_at": "2026-08-20T12:00:00Z",
            "content_sha256": "2" * 64,
            "evidence_span": "Veldig fornøyd med leveransen.",
            "identity_proof": [{"type": "exact_address", "value": "Innspurten 15"}],
            "metrics": {"rating": 5.0, "review_count": 1},
        }
        pred = generate_sentiment_prediction(review_item)
        self.assertEqual(pred["label"], "positive")
        self.assertEqual(pred["sentiment_model_version"], "explicit_star_rating_v1")

        low_rating_item = {**review_item, "metrics": {"rating": 1.0}}
        pred_low = generate_sentiment_prediction(low_rating_item)
        self.assertEqual(pred_low["label"], "negative")
        self.assertEqual(pred_low["sentiment_model_version"], "explicit_star_rating_v1")

    def test_rejection_when_identity_or_hash_missing(self):
        bad_item = {
            "id": "bad-1",
            "exact_entity": False,  # Not exact
            "source_class": "public_news",
            "source_url": "https://news.example/1",
            "retrieved_at": "2026-08-20T00:00:00Z",
            "content_sha256": None,  # Missing hash
            "evidence_span": "Selskapet vokser.",
        }
        with self.assertRaises(ValueError) as ctx:
            generate_sentiment_prediction(bad_item)
        self.assertIn("exact company identity is not verified", str(ctx.exception))
        self.assertIn("missing content_sha256", str(ctx.exception))

    def test_evaluator_metrics_calculation(self):
        gold = [
            {"id": "1", "label": "positive"},
            {"id": "2", "label": "neutral"},
            {"id": "3", "label": "negative"},
            {"id": "4", "label": "mixed"},
        ]
        predictions = [
            {"id": "1", "label": "positive", "exact_entity": True, "source_class": "public_news", "source_url": "https://s.example/1", "retrieved_at": "2026-08-20T00:00:00Z", "content_sha256": "a" * 64, "evidence_span": "pos"},
            {"id": "2", "label": "neutral", "exact_entity": True, "source_class": "public_news", "source_url": "https://s.example/2", "retrieved_at": "2026-08-20T00:00:00Z", "content_sha256": "b" * 64, "evidence_span": "neu"},
            {"id": "3", "label": "negative", "exact_entity": True, "source_class": "public_news", "source_url": "https://s.example/3", "retrieved_at": "2026-08-20T00:00:00Z", "content_sha256": "c" * 64, "evidence_span": "neg"},
            {"id": "4", "label": "mixed", "exact_entity": True, "source_class": "public_news", "source_url": "https://s.example/4", "retrieved_at": "2026-08-20T00:00:00Z", "content_sha256": "d" * 64, "evidence_span": "mix"},
        ]
        metrics = calculate_sentiment_metrics(gold, predictions)
        self.assertEqual(metrics["accuracy"], 1.0)
        self.assertEqual(metrics["macro_f1"], 1.0)
        self.assertEqual(metrics["wrong_entity_predictions"], 0)
        self.assertEqual(metrics["exact_entity_precision"], 1.0)
        self.assertEqual(metrics["evidence_support_rate"], 1.0)
        self.assertEqual(metrics["coverage"], 1.0)
        for label in LABELS:
            self.assertEqual(metrics["per_label"][label]["precision"], 1.0)
            self.assertEqual(metrics["per_label"][label]["recall"], 1.0)
            self.assertEqual(metrics["per_label"][label]["f1"], 1.0)

    def test_evaluate_sentiment_script_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            output_smoke = Path(directory) / "smoke-report.json"
            command_smoke = [
                sys.executable, str(ROOT / "scripts" / "evaluate_sentiment.py"),
                "--gold", str(ROOT / "tests" / "fixtures" / "sentiment-smoke-gold.jsonl"),
                "--predictions", str(ROOT / "tests" / "fixtures" / "sentiment-smoke-predictions.jsonl"),
                "--output", str(output_smoke),
                "--allow-smoke",
            ]
            subprocess.run(command_smoke, check=True, capture_output=True, text=True)
            report_smoke = json.loads(output_smoke.read_text(encoding="utf-8"))
            self.assertTrue(report_smoke["smoke_mode"])
            self.assertEqual(report_smoke["qualification_tier"], "local_smoke_qualification")
            self.assertFalse(report_smoke["qualification_passed"])
            self.assertEqual(report_smoke["accuracy"], 1.0)
            self.assertEqual(report_smoke["macro_f1"], 1.0)
            self.assertIn("positive", report_smoke["per_label"])

            # Held-out evaluation mode
            output_heldout = Path(directory) / "heldout-report.json"
            # Synthesize 300 balanced items for full gate test
            labels = ("positive", "neutral", "negative", "mixed")
            synth_gold = [{"id": str(i), "label": labels[i % 4]} for i in range(300)]
            synth_pred = [
                {
                    "id": str(i),
                    "label": labels[i % 4],
                    "exact_entity": True,
                    "source_class": "public_news",
                    "source_url": f"https://source{i % 5}.example/{i}",
                    "retrieved_at": "2026-08-20T00:00:00Z",
                    "content_sha256": hashlib.sha256(f"item-{i}".encode()).hexdigest(),
                    "evidence_span": f"Evidence span for item {i}",
                }
                for i in range(300)
            ]
            gold_file = Path(directory) / "synth_gold.jsonl"
            pred_file = Path(directory) / "synth_pred.jsonl"
            gold_file.write_text("".join(json.dumps(r) + "\n" for r in synth_gold), encoding="utf-8")
            pred_file.write_text("".join(json.dumps(r) + "\n" for r in synth_pred), encoding="utf-8")

            command_heldout = [
                sys.executable, str(ROOT / "scripts" / "evaluate_sentiment.py"),
                "--gold", str(gold_file),
                "--predictions", str(pred_file),
                "--output", str(output_heldout),
                "--held-out",
            ]
            subprocess.run(command_heldout, check=True, capture_output=True, text=True)
            report_heldout = json.loads(output_heldout.read_text(encoding="utf-8"))
            self.assertFalse(report_heldout["smoke_mode"])
            self.assertEqual(report_heldout["qualification_tier"], "held_out_qualification")
            self.assertTrue(report_heldout["qualification_passed"])
            self.assertEqual(report_heldout["accuracy"], 1.0)
            self.assertEqual(report_heldout["macro_f1"], 1.0)
            self.assertEqual(report_heldout["wrong_entity_predictions"], 0)


if __name__ == "__main__":
    unittest.main()
