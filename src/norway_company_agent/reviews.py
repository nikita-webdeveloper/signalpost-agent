from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from .external_footprint import PUBLISHABLE_ACQUISITION_MODES, validate_observation

LEGAL_STOP_WORDS = {
    "as", "asa", "sa", "enk", "nuf", "da", "ans", "ba", "stiftelsen", "sameiet",
    "avd", "avdeling", "holding", "group", "gruppen",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_tokens(value: object) -> list[str]:
    text = str(value or "").casefold()
    text = text.translate(str.maketrans({"æ": "ae", "ø": "o", "å": "a"}))
    return re.findall(r"[a-z0-9]+", text)


def _meaningful_tokens(value: object) -> list[str]:
    return [t for t in _normalize_tokens(value) if t not in LEGAL_STOP_WORDS and (len(t) >= 2 or t.isdigit())]


def _normalized_phone(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("47") and len(digits) > 8:
        digits = digits[2:]
    return digits[-8:]


def _sanitize_url(url: object) -> str:
    """Sanitize URL by redacting sensitive query parameters like key, api_key, secret, token."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw)
        if not parsed.netloc:
            return raw
        q = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        redacted = False
        for secret_param in ("key", "api_key", "secret", "token"):
            if secret_param in q:
                q[secret_param] = ["REDACTED"]
                redacted = True
        if redacted:
            clean_query = urllib.parse.urlencode(q, doseq=True)
            return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, parsed.fragment))
        return raw
    except Exception:
        return "[REDACTED_URL]"


def _domain_from_url(url: object) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = (urllib.parse.urlparse(raw).hostname or "").casefold()
        return host.removeprefix("www.")
    except Exception:
        return ""


def assess_candidate_identity(
    profile: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Score exact company identity match for a place/review candidate against registry profile."""
    company_name = str(profile.get("name") or "")
    org_number = str(profile.get("organisation_number") or "").strip()
    if not org_number or not org_number.isdigit() or len(org_number) != 9:
        return {
            "accepted": False, "name_score": 0.0, "fuzzy_name_score": 0.0,
            "address_match": False, "postcode_match": False, "phone_match": False,
            "website_match": False, "corroborating_channels": 0,
            "rejection_reasons": ["missing_or_invalid_organisation_number"], "proof": [],
        }

    registry = ((profile.get("evidence") or {}).get("registry") or {}).get("value") or {}
    business_addr = registry.get("business_address") or registry.get("forretningsadresse.adresse") or {}
    if isinstance(business_addr, dict):
        street = str(business_addr.get("adresse") or business_addr.get("address") or "")
        postal_code = str(business_addr.get("postnummer") or business_addr.get("postcode") or "")
        city = str(business_addr.get("poststed") or business_addr.get("city") or "")
    else:
        street = str(business_addr)
        postal_code = str(registry.get("forretningsadresse.postnummer") or "")
        city = str(registry.get("forretningsadresse.poststed") or "")

    candidate_title = str(candidate.get("name") or candidate.get("title") or "")
    candidate_address = str(candidate.get("formatted_address") or candidate.get("address") or "")
    candidate_phone = _normalized_phone(candidate.get("formatted_phone_number") or candidate.get("phone") or "")
    candidate_website = str(candidate.get("website") or candidate.get("web_site") or "")
    target_tokens = set(_meaningful_tokens(company_name))
    candidate_tokens = set(_meaningful_tokens(candidate_title))
    overlap = len(target_tokens & candidate_tokens)
    name_score = overlap / len(target_tokens) if target_tokens else 0.0
    if target_tokens and target_tokens.issubset(candidate_tokens):
        name_score = 1.0
    target_str = " ".join(_meaningful_tokens(company_name))
    candidate_str = " ".join(_meaningful_tokens(candidate_title))
    fuzzy_name_score = difflib.SequenceMatcher(None, target_str, candidate_str).ratio() if target_str and candidate_str else 0.0
    street_tokens = set(_normalize_tokens(street))
    candidate_addr_tokens = set(_normalize_tokens(candidate_address))
    address_match = bool(street_tokens and street_tokens.issubset(candidate_addr_tokens))
    postcode_match = bool(postal_code and postal_code in candidate_addr_tokens)
    city_match = bool(city and set(_normalize_tokens(city)).issubset(candidate_addr_tokens))
    reg_phone = _normalized_phone(registry.get("telefon") or registry.get("mobil") or profile.get("phone"))
    phone_match = bool(len(reg_phone) >= 5 and reg_phone == candidate_phone)
    website_info = ((profile.get("evidence") or {}).get("website") or {}).get("value") or {}
    expected_site = website_info.get("final_url") or ((profile.get("evidence") or {}).get("website") or {}).get("source_url") or profile.get("website")
    expected_domain = _domain_from_url(expected_site)
    candidate_domain = _domain_from_url(candidate_website)
    website_match = bool(expected_domain and candidate_domain and (candidate_domain == expected_domain or candidate_domain.endswith("." + expected_domain)))
    corroborating_channels = sum((address_match, postcode_match and city_match, phone_match, website_match))
    accepted = bool(
        (name_score >= 0.99 and corroborating_channels >= 1)
        or (name_score >= 0.66 and corroborating_channels >= 2)
        or (website_match and fuzzy_name_score >= 0.85)
    )
    rejection_reasons: list[str] = []
    if not accepted:
        if name_score < 0.5 and not website_match:
            rejection_reasons.append("insufficient_name_overlap")
        if corroborating_channels == 0:
            rejection_reasons.append("no_corroborating_location_phone_or_domain")
        if not rejection_reasons:
            rejection_reasons.append("identity_proof_threshold_not_met")
    proof = [
        {"type": "company_name_token_score", "value": round(name_score, 4)},
        {"type": "fuzzy_name_score", "value": round(fuzzy_name_score, 4)},
        {"type": "address_match", "value": address_match},
        {"type": "postcode_match", "value": postcode_match},
        {"type": "phone_match", "value": phone_match},
        {"type": "website_domain_match", "value": website_match},
        {"type": "organisation_number", "value": org_number},
    ]
    return {
        "accepted": accepted, "name_score": round(name_score, 4), "fuzzy_name_score": round(fuzzy_name_score, 4),
        "address_match": address_match, "postcode_match": postcode_match, "phone_match": phone_match,
        "website_match": website_match, "corroborating_channels": corroborating_channels,
        "rejection_reasons": rejection_reasons, "proof": proof,
    }


def review_sentiment_from_rating(rating: float | int) -> str:
    """Map star rating (1-5) deterministically to sentiment label."""
    if rating >= 4.0:
        return "positive"
    if rating <= 2.0:
        return "negative"
    return "neutral"


class GooglePlacesReviewConnector:
    """Production-quality connector for permitted Google Places reviews and aggregate ratings.

    Uses official Google Places API when API key is available.
    Returns clear not_available / credential_required when key is absent.
    Enforces exact legal entity identity before publishing.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("GOOGLE_PLACES_API_KEY") or os.environ.get("PLACES_API_KEY")

    def __repr__(self) -> str:
        key_status = "configured" if self.is_available else "missing"
        return f"<GooglePlacesReviewConnector api_key={key_status}>"

    @property
    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def fetch_places_and_reviews(
        self,
        profile: dict[str, Any],
        *,
        client_fn: Any | None = None,
    ) -> dict[str, Any]:
        """Fetch place details and reviews for a company profile.

        If API credentials are unavailable, returns not_available state.
        If identity cannot be verified, rejects and records reasons.
        """
        org_number = str(profile.get("organisation_number") or "").strip()
        company_name = str(profile.get("name") or "").strip()

        if not org_number or not org_number.isdigit() or len(org_number) != 9 or not company_name:
            return {
                "status": "error",
                "reason": "missing_or_invalid_organisation_number_or_name",
                "observations": [],
                "rejections": [{"reasons": ["missing_or_invalid_organisation_number_or_name"]}],
            }

        if not self.is_available and client_fn is None:
            return {
                "status": "not_available",
                "reason": "credential_required: GOOGLE_PLACES_API_KEY is not configured",
                "observations": [],
                "rejections": [],
            }

        # If custom client function is provided (for testing or direct injection)
        if client_fn is not None:
            raw_result = client_fn(company_name, org_number)
            if isinstance(raw_result, dict) and "candidates" in raw_result:
                res_status = raw_result.get("status", "ok")
                if res_status != "ok":
                    return {
                        "status": res_status,
                        "reason": raw_result.get("reason") or f"client_fn_{res_status}",
                        "observations": [],
                        "rejections": [],
                    }
                candidates = raw_result.get("candidates") or []
            elif isinstance(raw_result, list):
                candidates = raw_result
            else:
                candidates = []
        else:
            api_resp = self._query_places_api(company_name, profile)
            if api_resp["status"] != "ok":
                return {
                    "status": api_resp["status"],
                    "reason": api_resp.get("reason"),
                    "observations": [],
                    "rejections": [],
                }
            candidates = api_resp.get("candidates", [])

        if not candidates:
            return {
                "status": "not_found",
                "reason": "no_place_candidates_found",
                "observations": [],
                "rejections": [],
            }

        # Assess candidates for exact company match
        assessed_candidates = []
        for candidate in candidates:
            assessment = assess_candidate_identity(profile, candidate)
            assessed_candidates.append({
                "candidate": candidate,
                "assessment": assessment,
            })

        matching = [item for item in assessed_candidates if item["assessment"]["accepted"]]

        if not matching:
            reasons = [r for item in assessed_candidates for r in item["assessment"]["rejection_reasons"]]
            return {
                "status": "rejected",
                "reason": f"exact_entity_not_verified: {', '.join(sorted(set(reasons)))}",
                "observations": [],
                "rejections": [{"candidate_name": c.get("candidate", {}).get("name"), "reasons": c["assessment"]["rejection_reasons"]} for c in assessed_candidates],
            }

        # Check ambiguity among matching candidates
        if len(matching) > 1:
            matching.sort(
                key=lambda m: (
                    m["assessment"]["corroborating_channels"],
                    m["assessment"]["name_score"],
                    m["assessment"]["fuzzy_name_score"],
                ),
                reverse=True,
            )
            top_ch = matching[0]["assessment"]["corroborating_channels"]
            sec_ch = matching[1]["assessment"]["corroborating_channels"]
            top_ns = matching[0]["assessment"]["name_score"]
            sec_ns = matching[1]["assessment"]["name_score"]
            if top_ch == sec_ch or top_ns == sec_ns:
                return {
                    "status": "rejected",
                    "reason": "ambiguous_multiple_candidates_matched",
                    "observations": [],
                    "rejections": [{"reasons": ["multiple_ambiguous_places_candidates"]}],
                }

        chosen = matching[0]
        place_data = chosen["candidate"]
        assessment = chosen["assessment"]

        # Fetch Place Details if live API key is configured and candidate has place_id but lacks reviews/website
        if self.is_available and client_fn is None and "reviews" not in place_data and place_data.get("place_id"):
            details = self._fetch_place_details(place_data["place_id"])
            if details:
                for k, v in details.items():
                    if v and k not in place_data:
                        place_data[k] = v
                if "reviews" in details:
                    place_data["reviews"] = details["reviews"]

        observations = self._build_observations(profile, place_data, assessment)
        if not observations:
            return {
                "status": "not_found",
                "reason": "missing_review_data: place has no valid ratings or reviews",
                "observations": [],
                "rejections": [],
            }

        return {
            "status": "available",
            "observations": observations,
            "place_id": place_data.get("place_id"),
            "rejections": [],
        }

    def _query_places_api(self, query: str, profile: dict[str, Any]) -> dict[str, Any]:
        """Query official Google Places Text Search API with credential protection and error handling."""
        if not self.api_key:
            return {
                "status": "not_available",
                "reason": "credential_required: GOOGLE_PLACES_API_KEY is not configured",
                "candidates": [],
            }
        import urllib.request
        import urllib.parse
        import urllib.error
        import socket

        municipality = str(profile.get("municipality") or "").strip()
        search_query = f"{query} {municipality} Norway".strip() if municipality else f"{query} Norway".strip()
        params = urllib.parse.urlencode({"query": search_query, "key": self.api_key})
        url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?{params}"

        req = urllib.request.Request(url, headers={"User-Agent": "SignalpostPOC/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw_bytes = resp.read()
                try:
                    data = json.loads(raw_bytes.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return {
                        "status": "error",
                        "reason": "malformed_api_response: invalid JSON response from Google Places API",
                        "candidates": [],
                    }

                api_status = data.get("status")
                if api_status == "OK":
                    return {"status": "ok", "candidates": data.get("results", [])}
                elif api_status == "ZERO_RESULTS":
                    return {"status": "not_found", "reason": "no_place_candidates_found", "candidates": []}
                elif api_status == "OVER_QUERY_LIMIT":
                    return {
                        "status": "rate_limited",
                        "reason": f"quota_exceeded: {data.get('error_message') or 'OVER_QUERY_LIMIT'}",
                        "candidates": [],
                    }
                elif api_status == "REQUEST_DENIED":
                    return {
                        "status": "error",
                        "reason": f"unauthorized_or_invalid_api_key: {data.get('error_message') or 'REQUEST_DENIED'}",
                        "candidates": [],
                    }
                elif api_status == "INVALID_REQUEST":
                    return {
                        "status": "error",
                        "reason": f"invalid_request: {data.get('error_message') or 'INVALID_REQUEST'}",
                        "candidates": [],
                    }
                else:
                    return {
                        "status": "error",
                        "reason": f"places_api_error: {api_status}",
                        "candidates": [],
                    }
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return {
                    "status": "error",
                    "reason": f"unauthorized_or_invalid_api_key: HTTP {exc.code}",
                    "candidates": [],
                }
            elif exc.code == 429:
                return {
                    "status": "rate_limited",
                    "reason": f"rate_limit_exceeded: HTTP {exc.code}",
                    "candidates": [],
                }
            elif exc.code >= 500:
                return {
                    "status": "error",
                    "reason": f"api_server_error: HTTP {exc.code}",
                    "candidates": [],
                }
            return {
                "status": "error",
                "reason": f"api_http_error: HTTP {exc.code}",
                "candidates": [],
            }
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            reason_str = str(getattr(exc, "reason", exc)).lower()
            if "timed out" in reason_str or "timeout" in reason_str:
                return {
                    "status": "error",
                    "reason": "api_timeout: request to Google Places API timed out",
                    "candidates": [],
                }
            return {
                "status": "error",
                "reason": f"api_connection_error: {type(exc).__name__}",
                "candidates": [],
            }
        except Exception as exc:
            return {
                "status": "error",
                "reason": f"places_api_exception: {type(exc).__name__}",
                "candidates": [],
            }

    def _fetch_place_details(self, place_id: str) -> dict[str, Any] | None:
        """Query official Google Places Place Details API for permitted review and contact information."""
        if not self.api_key or not place_id:
            return None
        import urllib.request
        import urllib.parse
        fields = "place_id,name,formatted_address,formatted_phone_number,website,url,rating,user_ratings_total,reviews"
        params = urllib.parse.urlencode({
            "place_id": place_id,
            "fields": fields,
            "key": self.api_key,
        })
        url = f"https://maps.googleapis.com/maps/api/place/details/json?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "SignalpostPOC/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "OK":
                    return data.get("result", {})
                return None
        except Exception:
            return None

    def _build_observations(
        self,
        profile: dict[str, Any],
        place: dict[str, Any],
        assessment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        org_number = str(profile["organisation_number"])
        place_id = str(place.get("place_id") or "")
        retrieved_at = _utc_now()

        raw_payload = json.dumps(place, ensure_ascii=False, sort_keys=True)
        content_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

        raw_url = str(place.get("url") or "")
        if raw_url and _domain_from_url(raw_url):
            source_url = raw_url
        elif place_id:
            source_url = f"https://maps.google.com/?cid={place_id}"
        else:
            source_url = f"https://maps.google.com/search?q={urllib.parse.quote(str(place.get('name') or ''))}"

        identity_proof = [
            {"type": "places_identity_assessment", "value": assessment["proof"]},
            {"type": "google_places_id", "value": place_id},
        ]

        observations: list[dict[str, Any]] = []

        # 1. review_summary observation
        rating = float(place.get("rating") or place.get("review_rating") or 0.0)
        review_count = int(place.get("user_ratings_total") or place.get("review_count") or 0)

        if 0 < rating <= 5.0 and review_count > 0:
            evidence_span = (
                f"Google Places aggregate rating {rating:.1f}/5 based on {review_count} customer reviews "
                f"for exact legal entity {profile.get('name')} ({org_number})."
            )
            summary_obs = {
                "id": f"gplaces-summary-{org_number}-{content_hash[:16]}",
                "organisation_number": org_number,
                "platform": "google_places",
                "signal_type": "review_summary",
                "source_url": source_url,
                "retrieved_at": retrieved_at,
                "content_sha256": content_hash,
                "exact_entity": True,
                "identity_proof": identity_proof,
                "acquisition_mode": "official_api",
                "rights_status": "approved",
                "source_class": "customer_review",
                "evidence_span": evidence_span,
                "effective_at": retrieved_at,
                "metrics": {
                    "rating": rating,
                    "review_count": review_count,
                    "rating_scale": 5,
                    "place_id": place_id,
                },
                "strategy": "places_rating_reviews",
            }
            observations.append(summary_obs)

        # 2. Individual review observations
        reviews = place.get("reviews") or place.get("user_reviews") or []
        for idx, rev in enumerate(reviews):
            rev_rating = float(rev.get("rating") or rev.get("rating_float") or 0.0)
            if not (0 < rev_rating <= 5.0):
                continue

            rev_text = str(rev.get("text") or rev.get("text_original") or "").strip()
            rev_author = str(rev.get("author_name") or rev.get("author_url") or f"reviewer-{idx}")
            rev_time = rev.get("time") or rev.get("published_at")
            if isinstance(rev_time, (int, float)):
                try:
                    rev_date_iso = datetime.fromtimestamp(rev_time, tz=timezone.utc).isoformat().replace("+00:00", "Z")
                except (OverflowError, OSError, ValueError):
                    rev_date_iso = retrieved_at
            elif isinstance(rev_time, str) and rev_time.strip():
                rev_date_iso = rev_time.strip()
            else:
                rev_date_iso = retrieved_at

            rev_raw = json.dumps({"author": rev_author, "text": rev_text, "rating": rev_rating, "time": rev_time}, sort_keys=True)
            rev_hash = hashlib.sha256(rev_raw.encode("utf-8")).hexdigest()

            evidence_text = rev_text if rev_text else f"Explicit customer rating: {rev_rating:.1f}/5"

            rev_author_url = str(rev.get("author_url") or "")
            if rev_author_url and _domain_from_url(rev_author_url):
                rev_source_url = rev_author_url
            else:
                rev_source_url = source_url

            review_obs = {
                "id": f"gplaces-review-{org_number}-{rev_hash[:16]}",
                "organisation_number": org_number,
                "platform": "google_places",
                "signal_type": "review",
                "source_url": rev_source_url,
                "retrieved_at": retrieved_at,
                "content_sha256": rev_hash,
                "exact_entity": True,
                "identity_proof": identity_proof,
                "acquisition_mode": "official_api",
                "rights_status": "approved",
                "source_class": "customer_review",
                "evidence_span": evidence_text[:1200],
                "effective_at": rev_date_iso,
                "reviewer_id": rev_author,
                "metrics": {
                    "rating": rev_rating,
                    "rating_scale": 5,
                    "place_id": place_id,
                },
                "sentiment_label": review_sentiment_from_rating(rev_rating),
                "sentiment_model_version": "explicit_star_rating_v1",
                "strategy": "places_rating_reviews",
            }
            observations.append(review_obs)

        return observations


class WebsiteReviewConnector:
    """Extracts customer reviews and aggregate ratings from a verified company website.

    Only accepts websites that have already passed the company identity gate.
    Uses permitted_public_page mode with approved rights status.
    """

    def extract_website_reviews(self, profile: dict[str, Any]) -> list[dict[str, Any]]:
        org_number = str(profile.get("organisation_number") or "")
        website_evidence = ((profile.get("evidence") or {}).get("website") or {})

        if website_evidence.get("status") != "available":
            return []

        website_value = website_evidence.get("value") or {}
        identity_assessment = website_value.get("identity_assessment") or {}
        if not identity_assessment.get("publishable"):
            return []

        source_url = str(website_evidence.get("source_url") or website_value.get("final_url") or "")
        content_hash = str(website_evidence.get("content_sha256") or website_value.get("content_sha256") or "")
        retrieved_at = str(website_evidence.get("retrieved_at") or _utc_now())

        if not source_url or len(content_hash) != 64:
            return []

        observations: list[dict[str, Any]] = []

        # Inspect structured data from the homepage and verified same-domain secondary pages.
        structured_orgs = []
        for org in website_value.get("structured_organisations") or []:
            if isinstance(org, dict):
                structured_orgs.append({**org, "_evidence_url": source_url, "_evidence_hash": content_hash})
        for page in website_value.get("pages") or []:
            if not isinstance(page, dict):
                continue
            page_url = str(page.get("url") or "")
            page_hash = str(page.get("content_sha256") or "")
            if not page_url or len(page_hash) != 64:
                continue
            for org in page.get("structured_organisations") or []:
                if isinstance(org, dict):
                    structured_orgs.append({**org, "_evidence_url": page_url, "_evidence_hash": page_hash})
        for org in structured_orgs:
            if not isinstance(org, dict):
                continue
            evidence_url = str(org.get("_evidence_url") or source_url)
            evidence_hash = str(org.get("_evidence_hash") or content_hash)

            # 1. AggregateRating -> review_summary
            rating_data = org.get("aggregateRating")
            if isinstance(rating_data, dict):
                try:
                    rating_val = float(rating_data.get("ratingValue") or 0)
                    rating_count = int(rating_data.get("reviewCount") or rating_data.get("ratingCount") or 0)
                    best_rating = float(rating_data.get("bestRating") or 5.0)
                    scale_factor = 5.0 / best_rating if best_rating and best_rating != 5.0 else 1.0
                    normalized_rating = round(rating_val * scale_factor, 2)

                    if 0 < normalized_rating <= 5.0 and rating_count > 0:
                        obs_id = hashlib.sha256(f"{org_number}|site_review_summary|{evidence_url}|{evidence_hash}".encode()).hexdigest()[:24]
                        observations.append({
                            "id": f"site-review-summary-{obs_id}",
                            "organisation_number": org_number,
                            "platform": "company_site",
                            "signal_type": "review_summary",
                            "source_url": evidence_url,
                            "retrieved_at": retrieved_at,
                            "content_sha256": evidence_hash,
                            "exact_entity": True,
                            "identity_proof": [
                                {"type": "verified_company_website", "value": source_url},
                                {"type": "website_structured_aggregate_rating", "value": org.get("name")},
                            ],
                            "acquisition_mode": "permitted_public_page",
                            "rights_status": "approved",
                            "source_class": "customer_review",
                            "evidence_span": f"Customer rating {normalized_rating:.1f}/5 based on {rating_count} reviews on verified company site.",
                            "effective_at": retrieved_at,
                            "metrics": {
                                "rating": normalized_rating,
                                "review_count": rating_count,
                                "rating_scale": 5,
                            },
                            "strategy": "places_rating_reviews",
                        })
                except (ValueError, TypeError):
                    pass

            # 2. Schema.org individual Review objects
            raw_reviews = org.get("review") or org.get("reviews") or []
            if isinstance(raw_reviews, dict):
                raw_reviews = [raw_reviews]
            if isinstance(raw_reviews, list):
                for idx, rev in enumerate(raw_reviews):
                    if not isinstance(rev, dict):
                        continue
                    try:
                        rev_rating_obj = rev.get("reviewRating")
                        if isinstance(rev_rating_obj, dict):
                            r_val = float(rev_rating_obj.get("ratingValue") or 0)
                            r_best = float(rev_rating_obj.get("bestRating") or 5.0)
                        else:
                            r_val = float(rev_rating_obj or rev.get("ratingValue") or 0)
                            r_best = 5.0
                        scale = 5.0 / r_best if r_best and r_best != 5.0 else 1.0
                        norm_rating = round(r_val * scale, 2)
                        if not (0 < norm_rating <= 5.0):
                            continue

                        author_obj = rev.get("author")
                        if isinstance(author_obj, dict):
                            author_name = str(author_obj.get("name") or f"reviewer-{idx}")
                        elif isinstance(author_obj, str) and author_obj.strip():
                            author_name = author_obj.strip()
                        else:
                            author_name = f"reviewer-{idx}"

                        rev_text = str(rev.get("reviewBody") or rev.get("description") or rev.get("text") or "").strip()
                        date_pub = str(rev.get("datePublished") or rev.get("dateCreated") or retrieved_at).strip()
                        rev_raw = json.dumps({"org": org_number, "author": author_name, "rating": norm_rating, "text": rev_text, "date": date_pub}, sort_keys=True)
                        rev_hash = hashlib.sha256(rev_raw.encode("utf-8")).hexdigest()
                        evidence_text = rev_text if rev_text else f"Customer rating: {norm_rating:.1f}/5 on verified company site."

                        observations.append({
                            "id": f"site-review-{rev_hash[:24]}",
                            "organisation_number": org_number,
                            "platform": "company_site",
                            "signal_type": "review",
                            "source_url": evidence_url,
                            "retrieved_at": retrieved_at,
                            "content_sha256": rev_hash,
                            "exact_entity": True,
                            "identity_proof": [
                                {"type": "verified_company_website", "value": source_url},
                                {"type": "website_structured_review", "value": str(org.get("name") or org_number)},
                            ],
                            "acquisition_mode": "permitted_public_page",
                            "rights_status": "approved",
                            "source_class": "customer_review",
                            "evidence_span": evidence_text[:1200],
                            "effective_at": date_pub,
                            "reviewer_id": author_name,
                            "metrics": {
                                "rating": norm_rating,
                                "rating_scale": 5,
                            },
                            "sentiment_label": review_sentiment_from_rating(norm_rating),
                            "sentiment_model_version": "explicit_star_rating_v1",
                            "strategy": "places_rating_reviews",
                        })
                    except (ValueError, TypeError):
                        continue

        return observations


class LicensedReviewConnector:
    """Connector for third-party licensed reviews (Glassdoor, Indeed, Trustpilot).

    Enforces that without valid partner/commercial API credentials or company-authorized export,
    it returns clean not_available status and NEVER attempts unauthorized scraping.
    """

    def __init__(self, provider: str, license_key: str | None = None) -> None:
        self.provider = provider
        self.license_key = license_key or os.environ.get(f"{provider.upper()}_API_KEY")

    def fetch_reviews(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not self.license_key:
            return {
                "status": "not_available",
                "reason": f"credential_required: licensed access for {self.provider} requires valid API key/contract",
                "observations": [],
            }
        return {
            "status": "not_available",
            "reason": f"credential_required: license active but provider integration is unconfigured",
            "observations": [],
        }


def deduplicate_reviews(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate review observations by organisation, platform, reviewer, and content hash."""
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in observations:
        key = (
            str(item.get("organisation_number")),
            str(item.get("platform")),
            str(item.get("reviewer_id") or item.get("signal_type")),
            str(item.get("content_sha256")),
        )
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def validate_review_observation(item: dict[str, Any]) -> list[str]:
    """Validate that a review observation meets all strict publication criteria."""
    reasons = validate_observation(item)
    if item.get("signal_type") not in {"review", "review_summary"}:
        reasons.append("signal_type must be review or review_summary")
    metrics = item.get("metrics") or {}
    rating = metrics.get("rating")
    if rating is not None:
        try:
            r = float(rating)
            if not (0 < r <= 5.0):
                reasons.append("rating must be between 0 and 5")
        except (ValueError, TypeError):
            reasons.append("rating must be numeric")
    if item.get("signal_type") == "review_summary":
        count = metrics.get("review_count")
        if count is not None and int(count) < 0:
            reasons.append("review_count cannot be negative")
    return sorted(set(reasons))


def publishable_review_observation(item: dict[str, Any]) -> bool:
    return not validate_review_observation(item)
