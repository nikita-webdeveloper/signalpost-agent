from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any


def social_observations_from_website(profile: dict[str, Any]) -> list[dict[str, Any]]:
    website = ((profile.get("evidence") or {}).get("website") or {})
    value = website.get("value") or {}

    # We only use a successfully fetched company website.
    if website.get("status") != "available":
        return []

    # The website itself must already have passed the company-identity gate.
    website_assessment = value.get("identity_assessment") or {}
    if not website_assessment.get("publishable"):
        return []

    website_url = str(
        website.get("source_url")
        or value.get("final_url")
        or ""
    ).strip()

    website_hash = str(
        website.get("content_sha256")
        or value.get("content_sha256")
        or ""
    ).strip()

    organisation_number = str(
        profile.get("organisation_number") or ""
    ).strip()

    if not website_url or len(website_hash) != 64:
        return []

    if not organisation_number.isdigit():
        return []

    retrieved_at = str(website.get("retrieved_at") or "").strip()

    observations: list[dict[str, Any]] = []

    for item in value.get("social_link_assessments") or []:
        if not isinstance(item, dict):
            continue

        # Only keep social links that passed the existing identity check.
        if not item.get("publishable"):
            continue

        platform = str(item.get("platform") or "").strip()
        social_url = str(item.get("url") or "").strip()

        if not platform or not social_url:
            continue

        identity_score = item.get("identity_score")

        identity_proof = [
            {
                "type": "company_website_social_link",
                "value": website_url,
            },
            {
                "type": "social_handle_identity_assessment",
                "value": {
                    "score": identity_score,
                    "matched_tokens": item.get("matched_tokens") or [],
                    "reason": item.get("reason") or "",
                    "method": item.get("method") or "",
                },
            },
        ]

        observation_id = hashlib.sha256(
            f"{organisation_number}|{platform}|{social_url}|{website_hash}".encode(
                "utf-8"
            )
        ).hexdigest()[:24]

        observations.append(
            {
                "id": f"social-handle-{observation_id}",
                "organisation_number": organisation_number,
                "platform": platform,
                "signal_type": "profile_handle",
                "source_url": social_url,
                "retrieved_at": (
                    retrieved_at
                    or datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                ),
                "content_sha256": website_hash,
                "exact_entity": True,
                "identity_proof": identity_proof,
                "acquisition_mode": "permitted_public_page",
                "rights_status": "approved",
                "source_class": "company_social",
                "effective_at": retrieved_at or None,
                "metrics": {
                    "identity_score": identity_score,
                    "discovery_source_url": website_url,
                },
                "strategy": "verified_handle_extraction",
            }
        )

    return observations