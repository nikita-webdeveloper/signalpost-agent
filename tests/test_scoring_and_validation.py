#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external_footprint import (
    PUBLISHABLE_ACQUISITION_MODES,
    EXPERIMENTAL_ACQUISITION_MODES,
    INDEPENDENT_SENTIMENT_CLASSES,
    build_publishable_observation,
    publishable_observation,
    safe_read_jsonl,
    validate_observation,
)
from norway_company_agent.news import fetch_news_article, build_news_observation
from norway_company_agent.news_identity import assess_news_identity
from norway_company_agent.sentiment import (
    classify_text_sentiment,
    generate_sentiment_prediction,
    aggregate_company_sentiment,
    calculate_sentiment_metrics,
    evaluate_predictions,
)
from scripts.merge_observations import merge


class ScoringAndValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.valid_base_obs = {
            "id": "test-obs-1",
            "organisation_number": "912345678",
            "platform": "company_site",
            "signal_type": "profile_metrics",
            "source_url": "https://example.no/about",
            "retrieved_at": "2026-09-30T12:00:00Z",
            "content_sha256": "a" * 64,
            "exact_entity": True,
            "identity_proof": [{"type": "exact_test", "value": True}],
            "acquisition_mode": "permitted_public_page",
            "rights_status": "approved",
            "evidence_span": "Valid company site evidence span.",
        }

    def test_exact_entity_verification_required(self):
        """Observations without exact_entity or identity_proof must be rejected."""
        invalid_item = copy.deepcopy(self.valid_base_obs)
        invalid_item["exact_entity"] = False
        reasons = validate_observation(invalid_item)
        self.assertIn("exact legal entity is not verified", reasons)
        self.assertFalse(publishable_observation(invalid_item))

        no_proof = copy.deepcopy(self.valid_base_obs)
        no_proof["identity_proof"] = []
        self.assertIn("missing exact-entity proof", validate_observation(no_proof))

    def test_false_positive_company_name_rejection(self):
        """Unrelated words scattered across text must NOT pass exact entity gate."""
        profile = {
            "organisation_number": "991443320",
            "name": "NEW MAN'S LAND AS",
        }
        # Disparate paragraphs with 'new', 'man', 'land' in completely different contexts
        unrelated_text = (
            "Hvilket land kom FNs forste generalsekretar fra? "
            "Hovedkvarteret i New York var under oppussing. "
            "Forsamlingen var sterkt mannspreget i 1946."
        )
        assessment = assess_news_identity(profile, unrelated_text, "FN-quiz")
        self.assertFalse(assessment["publishable"])
        self.assertNotEqual(assessment["status"], "exact")

        # But a contiguous / proximity mention MUST pass
        genuine_text = "I gar meldte New Man's Land AS om rekordvekst og nye ansettelser i Oslo."
        genuine_assessment = assess_news_identity(profile, genuine_text, "Nyheter")
        self.assertTrue(genuine_assessment["publishable"])
        self.assertEqual(genuine_assessment["status"], "exact")

    def test_source_rights_and_acquisition_modes(self):
        """Experimental acquisition modes and unapproved rights must be rejected."""
        for exp_mode in EXPERIMENTAL_ACQUISITION_MODES:
            item = copy.deepcopy(self.valid_base_obs)
            item["acquisition_mode"] = exp_mode
            reasons = validate_observation(item)
            self.assertIn("acquisition mode is not approved for publication", reasons)
            self.assertFalse(publishable_observation(item))

        for pub_mode in PUBLISHABLE_ACQUISITION_MODES:
            item = copy.deepcopy(self.valid_base_obs)
            item["acquisition_mode"] = pub_mode
            item["rights_status"] = "approved"
            self.assertTrue(publishable_observation(item))

        # Rights review required must fail
        item = copy.deepcopy(self.valid_base_obs)
        item["rights_status"] = "review_required"
        self.assertIn("source rights are not approved", validate_observation(item))

    def test_news_direct_url_and_builder(self):
        """build_publishable_observation enforces required schema fields."""
        obs = build_publishable_observation(
            id="pub-obs-123",
            organisation_number="912345678",
            platform="news",
            signal_type="public_mention",
            source_url="https://e24.no/article/123",
            retrieved_at="2026-09-30T12:00:00Z",
            content_sha256="b" * 64,
            exact_entity=True,
            identity_proof=[{"type": "news_gate", "status": "exact"}],
            acquisition_mode="permitted_public_page",
            rights_status="approved",
            evidence_span="E24 reports strong profits for the company.",
            source_class="public_news",
            sentiment_label="positive",
            sentiment_model_version="deterministic_lexicon_v1",
        )
        self.assertEqual(obs["platform"], "news")
        self.assertTrue(publishable_observation(obs))

    def test_sentiment_classification_and_provenance(self):
        """Sentiment classifier must return valid deterministic labels and preserve provenance."""
        pos_label, pos_ver = classify_text_sentiment("Selskapet opplever sterk vekst og rekordhøyt overskudd.")
        self.assertEqual(pos_label, "positive")
        self.assertEqual(pos_ver, "deterministic_lexicon_v1")

        neg_label, neg_ver = classify_text_sentiment("Bedriften varsler kutt, oppsigelser og stort underskudd etter konkursbegjæring.")
        self.assertEqual(neg_label, "negative")

        mixed_label, _ = classify_text_sentiment("Selskapet har vekst i omsetning, men endte med et kraftig underskudd.")
        self.assertEqual(mixed_label, "mixed")

        item = {
            "id": "sent-item-1",
            "organisation_number": "912345678",
            "source_class": "public_news",
            "source_url": "https://e24.no/article/555",
            "retrieved_at": "2026-09-30T12:00:00Z",
            "content_sha256": "c" * 64,
            "exact_entity": True,
            "evidence_span": "Selskapet melder om solid overskudd og sterk vekst i markedet.",
        }
        pred = generate_sentiment_prediction(item)
        self.assertEqual(pred["label"], "positive")
        self.assertEqual(pred["sentiment_label"], "positive")
        self.assertEqual(pred["sentiment_model_version"], "deterministic_lexicon_v1")
        self.assertEqual(pred["content_sha256"], "c" * 64)

    def test_host_independence_aggregation(self):
        """Company-level sentiment rollup requires at least two independent source hosts."""
        item1 = {
            "id": "1", "organisation_number": "912345678", "exact_entity": True,
            "source_class": "public_news", "source_url": "https://e24.no/art1",
            "retrieved_at": "2026-09-30T12:00:00Z", "content_sha256": "1" * 64,
            "evidence_span": "Bra resultat og vekst.", "label": "positive",
        }
        item2_same_host = {
            "id": "2", "organisation_number": "912345678", "exact_entity": True,
            "source_class": "public_news", "source_url": "https://e24.no/art2",
            "retrieved_at": "2026-09-30T12:00:00Z", "content_sha256": "2" * 64,
            "evidence_span": "Overskudd og vekst.", "label": "positive",
        }
        item3_diff_host = {
            "id": "3", "organisation_number": "912345678", "exact_entity": True,
            "source_class": "public_news", "source_url": "https://dn.no/art3",
            "retrieved_at": "2026-09-30T12:00:00Z", "content_sha256": "3" * 64,
            "evidence_span": "Positiv utvikling og gevinst.", "label": "positive",
        }

        # Single host -> must abstain
        single_host_res = aggregate_company_sentiment([item1, item2_same_host])
        self.assertEqual(single_host_res["status"], "abstain")

        # Two distinct hosts -> available
        multi_host_res = aggregate_company_sentiment([item1, item3_diff_host])
        self.assertEqual(multi_host_res["status"], "available")
        self.assertEqual(multi_host_res["label"], "positive")

    def test_deterministic_deduplication(self):
        """Merging preserves base rows and deduplicates single-valued signals."""
        base = [self.valid_base_obs]
        dupe_same_id = copy.deepcopy(self.valid_base_obs)
        dupe_single_val = {
            **self.valid_base_obs,
            "id": "different-id",
            "strategy": self.valid_base_obs.get("strategy"),
        }
        known_orgs = {"912345678"}
        merged, stats = merge(base, [[dupe_same_id, dupe_single_val]], known_orgs)
        self.assertEqual(len(merged), 1)
        self.assertEqual(stats["counts"]["duplicate_id"], 1)
        self.assertEqual(stats["counts"]["duplicate_single_valued_signal"], 1)

    def test_safe_read_jsonl_malformed_handling(self):
        """safe_read_jsonl handles concatenated JSONs, empty lines, and malformed entries gracefully."""
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".jsonl") as tf:
            tf.write('{"id": "row1", "organisation_number": "912345678"}\n')
            tf.write('{"id": "row2", "organisation_number": "912345678"}{"id": "row3", "organisation_number": "912345678"}\n')
            tf.write('THIS IS INVALID JSON LINE\n')
            tf.write('{"id": "row4", "organisation_number": "912345678"}\n')
            tf_path = Path(tf.name)

        try:
            rows = safe_read_jsonl(tf_path, warn_on_error=False)
            self.assertEqual(len(rows), 4)
            ids = [r["id"] for r in rows]
            self.assertEqual(ids, ["row1", "row2", "row3", "row4"])
        finally:
            tf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
