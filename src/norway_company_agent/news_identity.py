from __future__ import annotations

import re
from typing import Any

from .identity import _tokens


def assess_news_identity(
    profile: dict[str, Any],
    article_text: str,
    article_title: str = "",
) -> dict[str, Any]:
    """Conservative exact-company identity gate for independent news articles."""

    company_tokens = _tokens(profile.get("name"))
    text = " ".join([
        str(article_title or ""),
        str(article_text or ""),
    ])
    text_tokens = set(_tokens(text))

    org_number = re.sub(
        r"\D",
        "",
        str(profile.get("organisation_number") or ""),
    )
    compact_text = re.sub(r"\D", "", text)

    # Strongest proof: organisation number appears in article evidence.
    if org_number and org_number in compact_text:
        return {
            "status": "exact",
            "score": 1.0,
            "publishable": True,
            "method": "deterministic_news_identity_v1",
            "reasons": [
                "exact organisation number appears in article evidence"
            ],
            "matched_tokens": company_tokens,
        }

    if not company_tokens:
        return {
            "status": "related_or_uncertain",
            "score": 0.0,
            "publishable": False,
            "method": "deterministic_news_identity_v1",
            "reasons": [
                "company legal name has no usable identity tokens"
            ],
            "matched_tokens": [],
        }

    matched = sorted(set(company_tokens) & text_tokens)
    ratio = len(matched) / len(set(company_tokens))

    # Multi-token legal name completely represented in article.
    # Require that all company tokens appear together within a proximity window to prevent false positives.
    def _in_proximity(target_tokens: list[str], full_text_tokens: list[str], max_window: int = 14) -> bool:
        k = len(target_tokens)
        if not k or not full_text_tokens:
            return False
        # Exact contiguous sequence
        for i in range(len(full_text_tokens) - k + 1):
            if full_text_tokens[i:i + k] == target_tokens:
                return True
        # Within window
        req_set = set(target_tokens)
        window_size = max(max_window, k + 4)
        for i in range(len(full_text_tokens)):
            if req_set.issubset(set(full_text_tokens[i:i + window_size])):
                return True
        return False

    raw_tokens_list = _tokens(text)
    proximity_matched = _in_proximity(company_tokens, raw_tokens_list)

    if len(company_tokens) >= 2 and ratio == 1.0 and proximity_matched:
        return {
            "status": "exact",
            "score": 0.95,
            "publishable": True,
            "method": "deterministic_news_identity_v1",
            "reasons": [
                "all normalized legal-name tokens appear together in article evidence"
            ],
            "matched_tokens": matched,
        }

    # A single company name is not enough to publish automatically.
    if (
        len(company_tokens) == 1
        and company_tokens[0] in text_tokens
        and len(company_tokens[0]) >= 5
    ):
        return {
            "status": "review",
            "score": 0.8,
            "publishable": False,
            "method": "deterministic_news_identity_v1",
            "reasons": [
                "single company-name token appears but is insufficient "
                "for exact publication"
            ],
            "matched_tokens": matched,
        }

    return {
        "status": "related_or_uncertain",
        "score": round(ratio, 3),
        "publishable": False,
        "method": "deterministic_news_identity_v1",
        "reasons": [
            "article evidence does not contain sufficient exact "
            "legal-name identity"
        ],
        "matched_tokens": matched,
    }