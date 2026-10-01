from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.external_footprint import (
    aggregate_footprint,
    publishable_observation,
    validate_observation,
)
from norway_company_agent.reviews import (
    GooglePlacesReviewConnector,
    LicensedReviewConnector,
    WebsiteReviewConnector,
    assess_candidate_identity,
    deduplicate_reviews,
    review_sentiment_from_rating,
    validate_review_observation,
)


class ReviewsConnectorTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "organisation_number": "938702675",
            "name": "AF GRUPPEN ASA",
            "phone": "22891100",
            "municipality": "OSLO",
            "evidence": {
                "registry": {
                    "value": {
                        "forretningsadresse.adresse": "Innspurten 15",
                        "forretningsadresse.postnummer": "0663",
                        "forretningsadresse.poststed": "Oslo",
                        "telefon": "22891100",
                    }
                },
                "website": {
                    "source_url": "https://afgruppen.no/",
                    "status": "available",
                    "content_sha256": "a" * 64,
                    "retrieved_at": "2026-08-20T00:00:00Z",
                    "value": {
                        "final_url": "https://afgruppen.no/",
                        "identity_assessment": {"publishable": True},
                    },
                },
            },
        }

    def test_valid_exact_company_match(self):
        candidate = {
            "name": "AF Gruppen ASA",
            "formatted_address": "Innspurten 15, 0663 Oslo, Norway",
            "formatted_phone_number": "+47 22 89 11 00",
            "website": "https://www.afgruppen.no",
            "place_id": "ChIJ1234567890",
            "rating": 4.5,
            "user_ratings_total": 42,
            "reviews": [
                {
                    "author_name": "Ola Nordmann",
                    "rating": 5.0,
                    "text": "Flott entreprenor med god oppfolging.",
                    "time": 1724000000,
                }
            ],
        }
        assessment = assess_candidate_identity(self.profile, candidate)
        self.assertTrue(assessment["accepted"])
        self.assertGreaterEqual(assessment["name_score"], 0.99)
        self.assertTrue(assessment["address_match"])
        self.assertTrue(assessment["phone_match"])
        self.assertTrue(assessment["website_match"])

        connector = GooglePlacesReviewConnector(api_key="test-api-key")
        result = connector.fetch_places_and_reviews(self.profile, client_fn=lambda _name, _org: [candidate])
        self.assertEqual(result["status"], "available")
        self.assertEqual(len(result["observations"]), 2)

        summary_obs = result["observations"][0]
        self.assertEqual(summary_obs["signal_type"], "review_summary")
        self.assertEqual(summary_obs["metrics"]["rating"], 4.5)
        self.assertEqual(summary_obs["metrics"]["review_count"], 42)
        self.assertEqual(summary_obs["acquisition_mode"], "official_api")
        self.assertEqual(summary_obs["rights_status"], "approved")
        self.assertTrue(summary_obs["exact_entity"])
        self.assertTrue(publishable_observation(summary_obs))

        review_obs = result["observations"][1]
        self.assertEqual(review_obs["signal_type"], "review")
        self.assertEqual(review_obs["metrics"]["rating"], 5.0)
        self.assertEqual(review_obs["sentiment_label"], "positive")
        self.assertEqual(review_obs["sentiment_model_version"], "explicit_star_rating_v1")
        self.assertTrue(publishable_observation(review_obs))

    def test_wrong_company_rejection(self):
        wrong_candidate = {
            "name": "AF Bygg Syd AS",
            "formatted_address": "Strandgaten 10, 4610 Kristiansand, Norway",
            "formatted_phone_number": "+47 38 00 00 00",
            "website": "https://other-af-bygg.no",
            "place_id": "ChIJ9999999999",
            "rating": 3.2,
            "user_ratings_total": 5,
        }
        assessment = assess_candidate_identity(self.profile, wrong_candidate)
        self.assertFalse(assessment["accepted"])
        self.assertIn("no_corroborating_location_phone_or_domain", assessment["rejection_reasons"])

        connector = GooglePlacesReviewConnector(api_key="test-key")
        result = connector.fetch_places_and_reviews(self.profile, client_fn=lambda _name, _org: [wrong_candidate])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(len(result["observations"]), 0)
        self.assertIn("exact_entity_not_verified", result["reason"])

    def test_missing_identity_proof(self):
        obs = {
            "id": "test-obs-1",
            "organisation_number": "938702675",
            "platform": "google_places",
            "signal_type": "review_summary",
            "source_url": "https://maps.google.com/test",
            "retrieved_at": "2026-08-20T00:00:00Z",
            "content_sha256": "b" * 64,
            "exact_entity": True,
            "identity_proof": [],  # Missing proof
            "acquisition_mode": "official_api",
            "rights_status": "approved",
            "source_class": "customer_review",
            "evidence_span": "Aggregate rating 4.5/5",
            "metrics": {"rating": 4.5, "review_count": 10},
        }
        reasons = validate_observation(obs)
        self.assertIn("missing exact-entity proof", reasons)
        self.assertFalse(publishable_observation(obs))

    def test_missing_evidence(self):
        # Missing evidence span
        obs_missing_span = {
            "id": "test-obs-2",
            "organisation_number": "938702675",
            "platform": "google_places",
            "signal_type": "review",
            "source_url": "https://maps.google.com/test",
            "retrieved_at": "2026-08-20T00:00:00Z",
            "content_sha256": "c" * 64,
            "exact_entity": True,
            "identity_proof": [{"type": "exact_match", "value": True}],
            "acquisition_mode": "official_api",
            "rights_status": "approved",
            "source_class": "customer_review",
            "evidence_span": "",  # Missing span
        }
        reasons = validate_observation(obs_missing_span)
        self.assertIn("missing evidence span", reasons)

        # Missing retrieval time
        obs_missing_time = {**obs_missing_span, "evidence_span": "Great company", "retrieved_at": None}
        self.assertIn("missing retrieval time", validate_observation(obs_missing_time))

    def test_invalid_url(self):
        obs = {
            "id": "test-obs-3",
            "organisation_number": "938702675",
            "platform": "google_places",
            "signal_type": "review_summary",
            "source_url": "not-a-url",
            "retrieved_at": "2026-08-20T00:00:00Z",
            "content_sha256": "d" * 64,
            "exact_entity": True,
            "identity_proof": [{"type": "match", "value": True}],
            "acquisition_mode": "official_api",
            "rights_status": "approved",
            "source_class": "customer_review",
            "evidence_span": "Rating 4/5",
        }
        reasons = validate_observation(obs)
        self.assertIn("missing or invalid source URL", reasons)

    def test_invalid_hash(self):
        obs = {
            "id": "test-obs-4",
            "organisation_number": "938702675",
            "platform": "google_places",
            "signal_type": "review_summary",
            "source_url": "https://maps.google.com/place",
            "retrieved_at": "2026-08-20T00:00:00Z",
            "content_sha256": "short-hash",  # Not 64 chars
            "exact_entity": True,
            "identity_proof": [{"type": "match", "value": True}],
            "acquisition_mode": "official_api",
            "rights_status": "approved",
            "source_class": "customer_review",
            "evidence_span": "Rating 4/5",
        }
        reasons = validate_observation(obs)
        self.assertIn("missing content hash", reasons)

    def test_unsupported_acquisition_mode(self):
        obs = {
            "id": "test-obs-5",
            "organisation_number": "938702675",
            "platform": "google_places",
            "signal_type": "review_summary",
            "source_url": "https://maps.google.com/place",
            "retrieved_at": "2026-08-20T00:00:00Z",
            "content_sha256": "e" * 64,
            "exact_entity": True,
            "identity_proof": [{"type": "match", "value": True}],
            "acquisition_mode": "unofficial_api_experiment",  # Not publishable
            "rights_status": "review_required",  # Not approved
            "source_class": "customer_review",
            "evidence_span": "Rating 4/5",
        }
        reasons = validate_observation(obs)
        self.assertIn("acquisition mode is not approved for publication", reasons)
        self.assertIn("source rights are not approved", reasons)

    def test_unavailable_api_credentials(self):
        connector = GooglePlacesReviewConnector(api_key=None)
        self.assertFalse(connector.is_available)
        result = connector.fetch_places_and_reviews(self.profile)
        self.assertEqual(result["status"], "not_available")
        self.assertIn("credential_required", result["reason"])
        self.assertEqual(len(result["observations"]), 0)

        # Licensed review connector test without credentials
        licensed = LicensedReviewConnector(provider="glassdoor", license_key=None)
        lic_result = licensed.fetch_reviews(self.profile)
        self.assertEqual(lic_result["status"], "not_available")
        self.assertIn("credential_required", lic_result["reason"])

    def test_valid_rating_review_ingestion_and_footprint_contribution(self):
        candidate = {
            "name": "AF Gruppen ASA",
            "formatted_address": "Innspurten 15, 0663 Oslo",
            "formatted_phone_number": "+47 22 89 11 00",
            "website": "https://afgruppen.no",
            "place_id": "ChIJ_af_gruppen",
            "rating": 4.2,
            "user_ratings_total": 15,
            "reviews": [
                {"author_name": f"Reviewer {i}", "rating": 4.0 if i % 2 == 0 else 5.0, "text": f"Review text {i}", "time": 1724000000 + i}
                for i in range(10)
            ],
        }
        connector = GooglePlacesReviewConnector(api_key="test-key")
        result = connector.fetch_places_and_reviews(self.profile, client_fn=lambda _name, _org: [candidate])
        observations = result["observations"]
        self.assertEqual(len(observations), 11)  # 1 summary + 10 individual

        footprint = aggregate_footprint(observations, as_of="2026-08-20T00:00:00Z")
        self.assertEqual(footprint["status"], "available")
        self.assertEqual(footprint["review_signal_count"], 11)
        self.assertIn("reviews", footprint)
        self.assertEqual(footprint["reviews"]["status"], "available")
        self.assertEqual(footprint["reviews"]["total_review_count"], 15)
        self.assertGreaterEqual(footprint["reviews"]["average_rating"], 4.0)
        # 10 independent reviews on Google Places -> sentiment status available
        self.assertEqual(footprint["sentiment"]["status"], "available")
        self.assertEqual(footprint["sentiment"]["items"], 10)

    def test_duplicate_handling(self):
        candidate = {
            "name": "AF Gruppen ASA",
            "formatted_address": "Innspurten 15, 0663 Oslo",
            "website": "https://afgruppen.no",
            "place_id": "ChIJ_af",
            "rating": 4.0,
            "user_ratings_total": 2,
            "reviews": [
                {"author_name": "Reviewer A", "rating": 5.0, "text": "Solid partner", "time": 1724000000},
            ],
        }
        connector = GooglePlacesReviewConnector(api_key="test-key")
        res1 = connector.fetch_places_and_reviews(self.profile, client_fn=lambda _name, _org: [candidate])
        res2 = connector.fetch_places_and_reviews(self.profile, client_fn=lambda _name, _org: [candidate])
        combined = res1["observations"] + res2["observations"]
        self.assertEqual(len(combined), 4)

        deduped = deduplicate_reviews(combined)
        self.assertEqual(len(deduped), 2)  # 1 summary + 1 review

    def test_website_structured_reviews_connector(self):
        profile_with_structured = {
            "organisation_number": "938702675",
            "name": "AF GRUPPEN ASA",
            "evidence": {
                "website": {
                    "source_url": "https://afgruppen.no/",
                    "status": "available",
                    "content_sha256": "f" * 64,
                    "retrieved_at": "2026-08-20T00:00:00Z",
                    "value": {
                        "final_url": "https://afgruppen.no/",
                        "identity_assessment": {"publishable": True},
                        "structured_organisations": [
                            {
                                "@type": "Organization",
                                "name": "AF Gruppen",
                                "aggregateRating": {
                                    "@type": "AggregateRating",
                                    "ratingValue": "4.6",
                                    "reviewCount": "85",
                                    "bestRating": "5",
                                },
                            }
                        ],
                    },
                }
            },
        }
        connector = WebsiteReviewConnector()
        obs = connector.extract_website_reviews(profile_with_structured)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["signal_type"], "review_summary")
        self.assertEqual(obs[0]["platform"], "company_site")
        self.assertEqual(obs[0]["acquisition_mode"], "permitted_public_page")
        self.assertEqual(obs[0]["rights_status"], "approved")
        self.assertEqual(obs[0]["metrics"]["rating"], 4.6)
        self.assertEqual(obs[0]["metrics"]["review_count"], 85)
        self.assertTrue(publishable_observation(obs[0]))

    def test_website_secondary_page_structured_review_preserves_source(self):
        profile = {
            "organisation_number": "938702675",
            "name": "AF GRUPPEN ASA",
            "evidence": {
                "website": {
                    "source_url": "https://afgruppen.no/",
                    "status": "available",
                    "content_sha256": "f" * 64,
                    "retrieved_at": "2026-08-20T00:00:00Z",
                    "value": {
                        "final_url": "https://afgruppen.no/",
                        "identity_assessment": {"publishable": True},
                        "structured_organisations": [],
                        "pages": [{
                            "url": "https://afgruppen.no/anmeldelser",
                            "content_sha256": "e" * 64,
                            "structured_organisations": [{
                                "@type": "LocalBusiness",
                                "name": "AF Gruppen",
                                "aggregateRating": {"ratingValue": "4.7", "reviewCount": "12"},
                            }],
                        }],
                    },
                }
            },
        }
        observations = WebsiteReviewConnector().extract_website_reviews(profile)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["source_url"], "https://afgruppen.no/anmeldelser")
        self.assertEqual(observations[0]["content_sha256"], "e" * 64)
        self.assertTrue(publishable_observation(observations[0]))

    def test_website_structured_individual_reviews_connector(self):
        profile_with_individual_reviews = {
            "organisation_number": "938702675",
            "name": "AF GRUPPEN ASA",
            "evidence": {
                "website": {
                    "source_url": "https://afgruppen.no/",
                    "status": "available",
                    "content_sha256": "f" * 64,
                    "retrieved_at": "2026-08-20T00:00:00Z",
                    "value": {
                        "final_url": "https://afgruppen.no/",
                        "identity_assessment": {"publishable": True},
                        "structured_organisations": [
                            {
                                "@type": "Organization",
                                "name": "AF Gruppen",
                                "review": [
                                    {
                                        "@type": "Review",
                                        "reviewRating": {"@type": "Rating", "ratingValue": 5},
                                        "author": {"@type": "Person", "name": "Kari Nordmann"},
                                        "reviewBody": "Utmerket entreprenør!",
                                        "datePublished": "2026-06-15",
                                    }
                                ],
                            }
                        ],
                    },
                }
            },
        }
        connector = WebsiteReviewConnector()
        obs = connector.extract_website_reviews(profile_with_individual_reviews)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["signal_type"], "review")
        self.assertEqual(obs[0]["platform"], "company_site")
        self.assertEqual(obs[0]["metrics"]["rating"], 5.0)
        self.assertEqual(obs[0]["sentiment_label"], "positive")
        self.assertEqual(obs[0]["reviewer_id"], "Kari Nordmann")
        self.assertTrue(publishable_observation(obs[0]))

    def test_ambiguous_multiple_candidates_rejected(self):
        cand1 = {
            "name": "AF Gruppen ASA",
            "formatted_address": "Innspurten 15, 0663 Oslo, Norway",
            "formatted_phone_number": "+47 22 89 11 00",
            "website": "https://afgruppen.no",
            "place_id": "ChIJ_branch_1",
            "rating": 4.5,
            "user_ratings_total": 20,
        }
        cand2 = {
            "name": "AF Gruppen ASA",
            "formatted_address": "Innspurten 15, 0663 Oslo, Norway",
            "formatted_phone_number": "+47 22 89 11 00",
            "website": "https://afgruppen.no",
            "place_id": "ChIJ_branch_2",
            "rating": 4.2,
            "user_ratings_total": 15,
        }
        connector = GooglePlacesReviewConnector(api_key="test-key")
        res = connector.fetch_places_and_reviews(self.profile, client_fn=lambda _name, _org: [cand1, cand2])
        self.assertEqual(res["status"], "rejected")
        self.assertEqual(res["reason"], "ambiguous_multiple_candidates_matched")
        self.assertEqual(len(res["observations"]), 0)

    def test_missing_or_invalid_organisation_number(self):
        bad_profile = {**self.profile, "organisation_number": "invalid_org"}
        connector = GooglePlacesReviewConnector(api_key="test-key")
        res = connector.fetch_places_and_reviews(bad_profile)
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["reason"], "missing_or_invalid_organisation_number_or_name")
        self.assertEqual(len(res["observations"]), 0)

    def test_places_api_live_credential_mock_success(self):
        from unittest.mock import MagicMock, patch

        textsearch_response = {
            "status": "OK",
            "results": [
                {
                    "name": "AF Gruppen ASA",
                    "formatted_address": "Innspurten 15, 0663 Oslo, Norway",
                    "place_id": "ChIJ_mock_123",
                    "rating": 4.5,
                    "user_ratings_total": 30,
                }
            ],
        }
        details_response = {
            "status": "OK",
            "result": {
                "name": "AF Gruppen ASA",
                "formatted_address": "Innspurten 15, 0663 Oslo, Norway",
                "formatted_phone_number": "+47 22 89 11 00",
                "website": "https://afgruppen.no",
                "place_id": "ChIJ_mock_123",
                "url": "https://maps.google.com/?cid=123456",
                "rating": 4.5,
                "user_ratings_total": 30,
                "reviews": [
                    {
                        "author_name": "Per Hansen",
                        "author_url": "https://www.google.com/maps/contrib/123",
                        "rating": 5.0,
                        "text": "Veldig profesjonelt byggeselskap.",
                        "time": 1724000000,
                    }
                ],
            },
        }

        mock_resp1 = MagicMock()
        mock_resp1.read.return_value = json.dumps(textsearch_response).encode("utf-8")
        mock_resp1.__enter__.return_value = mock_resp1

        mock_resp2 = MagicMock()
        mock_resp2.read.return_value = json.dumps(details_response).encode("utf-8")
        mock_resp2.__enter__.return_value = mock_resp2

        connector = GooglePlacesReviewConnector(api_key="secret-test-key-999")
        self.assertTrue(connector.is_available)

        with patch("urllib.request.urlopen", side_effect=[mock_resp1, mock_resp2]) as mock_urlopen:
            res = connector.fetch_places_and_reviews(self.profile)
            self.assertEqual(res["status"], "available")
            self.assertEqual(len(res["observations"]), 2)
            self.assertEqual(res["place_id"], "ChIJ_mock_123")
            self.assertTrue(all(publishable_observation(o) for o in res["observations"]))
            self.assertEqual(mock_urlopen.call_count, 2)

    def test_places_api_quota_exceeded(self):
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "status": "OVER_QUERY_LIMIT",
            "error_message": "You have exceeded your daily request quota.",
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        connector = GooglePlacesReviewConnector(api_key="test-key")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = connector.fetch_places_and_reviews(self.profile)
            self.assertEqual(res["status"], "rate_limited")
            self.assertIn("quota_exceeded", res["reason"])
            self.assertEqual(len(res["observations"]), 0)

    def test_places_api_unauthorized_key(self):
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "status": "REQUEST_DENIED",
            "error_message": "The provided API key is expired or invalid.",
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        connector = GooglePlacesReviewConnector(api_key="test-key")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = connector.fetch_places_and_reviews(self.profile)
            self.assertEqual(res["status"], "error")
            self.assertIn("unauthorized_or_invalid_api_key", res["reason"])
            self.assertEqual(len(res["observations"]), 0)

    def test_places_api_timeout_handling(self):
        import urllib.error
        from unittest.mock import patch

        connector = GooglePlacesReviewConnector(api_key="test-key")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection timed out")):
            res = connector.fetch_places_and_reviews(self.profile)
            self.assertEqual(res["status"], "error")
            self.assertIn("api_timeout", res["reason"])
            self.assertEqual(len(res["observations"]), 0)

    def test_places_api_malformed_json_response(self):
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html>502 Bad Gateway</html>"
        mock_resp.__enter__.return_value = mock_resp

        connector = GooglePlacesReviewConnector(api_key="test-key")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = connector.fetch_places_and_reviews(self.profile)
            self.assertEqual(res["status"], "error")
            self.assertIn("malformed_api_response", res["reason"])
            self.assertEqual(len(res["observations"]), 0)

    def test_places_api_zero_results(self):
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ZERO_RESULTS", "results": []}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        connector = GooglePlacesReviewConnector(api_key="test-key")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = connector.fetch_places_and_reviews(self.profile)
            self.assertEqual(res["status"], "not_found")
            self.assertEqual(res["reason"], "no_place_candidates_found")

    def test_place_with_missing_review_data(self):
        candidate_no_reviews = {
            "name": "AF Gruppen ASA",
            "formatted_address": "Innspurten 15, 0663 Oslo, Norway",
            "formatted_phone_number": "+47 22 89 11 00",
            "website": "https://afgruppen.no",
            "place_id": "ChIJ_empty_place",
            "rating": 0,
            "user_ratings_total": 0,
            "reviews": [],
        }
        connector = GooglePlacesReviewConnector(api_key="test-key")
        res = connector.fetch_places_and_reviews(self.profile, client_fn=lambda _name, _org: [candidate_no_reviews])
        self.assertEqual(res["status"], "not_found")
        self.assertIn("missing_review_data", res["reason"])
        self.assertEqual(len(res["observations"]), 0)

    def test_no_credential_leakage(self):
        secret_key = "secret-places-api-key-XYZ12345"
        connector = GooglePlacesReviewConnector(api_key=secret_key)
        self.assertNotIn(secret_key, repr(connector))
        self.assertEqual(repr(connector), "<GooglePlacesReviewConnector api_key=configured>")

        missing_connector = GooglePlacesReviewConnector(api_key=None)
        self.assertEqual(repr(missing_connector), "<GooglePlacesReviewConnector api_key=missing>")

        from norway_company_agent.reviews import _sanitize_url
        clean = _sanitize_url(f"https://maps.googleapis.com/maps/api/place/textsearch/json?query=test&key={secret_key}")
        self.assertNotIn(secret_key, clean)
        self.assertIn("REDACTED", clean)

    def test_research_answer_with_review_provenance(self):
        from norway_company_agent.research import answer_profile

        candidate = {
            "name": "AF Gruppen ASA",
            "formatted_address": "Innspurten 15, 0663 Oslo, Norway",
            "formatted_phone_number": "+47 22 89 11 00",
            "website": "https://afgruppen.no",
            "place_id": "ChIJ_af_provenance",
            "rating": 4.6,
            "user_ratings_total": 50,
            "reviews": [{"author_name": "Auditor", "rating": 5.0, "text": "Solid leverandør.", "time": 1724000000}],
        }
        connector = GooglePlacesReviewConnector(api_key="test-key")
        result = connector.fetch_places_and_reviews(self.profile, client_fn=lambda _name, _org: [candidate])
        observations = result["observations"]

        footprint = aggregate_footprint(observations)
        profile_with_footprint = {
            **self.profile,
            "external_footprint": footprint,
        }
        answer = answer_profile(profile_with_footprint, "What customer reviews and ratings are available?")
        self.assertTrue(len(answer["facts"]) >= 2)
        rating_fact = next(f for f in answer["facts"] if f["claim"] == "Average customer rating")
        self.assertEqual(rating_fact["value"], 4.8)
        self.assertTrue(bool(rating_fact["source_url"]))
        self.assertTrue(bool(rating_fact["retrieved_at"]))
        self.assertEqual(len(rating_fact["content_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()

