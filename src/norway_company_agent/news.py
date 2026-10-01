from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from typing import Any

from bs4 import BeautifulSoup
import trafilatura

from .news_identity import assess_news_identity
from .website import SAFE_OPENER, USER_AGENT, _robots_allowed, assert_public_url


APPROVED_ACQUISITION_MODES = {
    "official_api",
    "licensed_api",
    "company_authorized_export",
    "permitted_public_page",
}


def fetch_news_article(
    url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = 2_000_000,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Safely fetch one public HTML news article."""

    normalized = str(url or "").strip()

    if not normalized:
        return {
            "status": "not_found",
            "url": normalized,
            "error": "empty URL",
        }, {"requests": 0, "bytes": 0, "latencies_ms": []}

    try:
        assert_public_url(normalized)
    except ValueError as exc:
        return {
            "status": "blocked",
            "url": normalized,
            "error": str(exc),
        }, {"requests": 0, "bytes": 0, "latencies_ms": []}

    if not _robots_allowed(normalized, timeout):
        return {
            "status": "blocked",
            "url": normalized,
            "error": "robots.txt disallows this user agent",
        }, {"requests": 1, "bytes": 0, "latencies_ms": []}

    started = time.monotonic()

    request = urllib.request.Request(
        normalized,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    try:
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            elapsed = int(
                (time.monotonic() - started) * 1000
            )
            final_url = response.geturl()
            content_type = response.headers.get(
                "content-type",
                "",
            )

        assert_public_url(final_url)

        if len(raw) > max_bytes:
            return {
                "status": "blocked",
                "url": final_url,
                "error": "article exceeds byte limit",
            }, {
                "requests": 1,
                "bytes": len(raw),
                "latencies_ms": [elapsed],
            }

        if "html" not in content_type.lower():
            return {
                "status": "source_error",
                "url": final_url,
                "error": f"unsupported content type: {content_type}",
            }, {
                "requests": 1,
                "bytes": len(raw),
                "latencies_ms": [elapsed],
            }

        html = raw.decode(
            "utf-8",
            errors="replace",
        )

        soup = BeautifulSoup(
            html,
            "lxml",
        )

        title = (
            soup.title.get_text(
                " ",
                strip=True,
            )
            if soup.title
            else ""
        )

        text = (
            trafilatura.extract(
                html,
                url=final_url,
                include_links=False,
                include_tables=False,
                favor_precision=True,
            )
            or ""
        )

        return {
            "status": "available",
            "url": final_url,
            "title": title[:500],
            "text": text[:20000],
            "content_sha256": hashlib.sha256(
                raw
            ).hexdigest(),
        }, {
            "requests": 1,
            "bytes": len(raw),
            "latencies_ms": [elapsed],
        }

    except urllib.error.HTTPError as exc:
        elapsed = int(
            (time.monotonic() - started) * 1000
        )
        status = (
            "not_found"
            if exc.code in {404, 410}
            else "source_error"
        )

        return {
            "status": status,
            "url": normalized,
            "error": f"HTTP {exc.code}",
        }, {
            "requests": 1,
            "bytes": 0,
            "latencies_ms": [elapsed],
        }

    except urllib.error.URLError as exc:
        elapsed = int(
            (time.monotonic() - started) * 1000
        )

        return {
            "status": "source_error",
            "url": normalized,
            "error": f"URLError: {str(exc.reason)[:180]}",
        }, {
            "requests": 1,
            "bytes": 0,
            "latencies_ms": [elapsed],
        }

    except Exception as exc:
        elapsed = int(
            (time.monotonic() - started) * 1000
        )

        return {
            "status": "source_error",
            "url": normalized,
            "error": f"{type(exc).__name__}: {str(exc)[:180]}",
        }, {
            "requests": 1,
            "bytes": 0,
            "latencies_ms": [elapsed],
        }


def build_news_observation(
    profile: dict[str, Any],
    *,
    article_url: str,
    article_title: str,
    article_text: str,
    publisher: str,
    acquisition_mode: str,
    rights_status: str,
    retrieved_at: str | None = None,
    source_class: str = "public_news",
) -> dict[str, Any]:
    """Build a conservative independent-news observation."""

    identity = assess_news_identity(
        profile,
        article_text,
        article_title,
    )

    organisation_number = str(
        profile.get("organisation_number") or ""
    ).strip()

    raw_content = " ".join(
        [
            str(article_title or ""),
            str(article_text or ""),
        ]
    ).strip()

    content_sha256 = hashlib.sha256(
        raw_content.encode("utf-8")
    ).hexdigest()

    observation_id = hashlib.sha256(
        f"{organisation_number}|{article_url}|{content_sha256}".encode(
            "utf-8"
        )
    ).hexdigest()[:24]

    observation_retrieved_at = (
        retrieved_at
        or time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        )
    )

    approved_acquisition = (
        acquisition_mode in APPROVED_ACQUISITION_MODES
    )

    approved_rights = rights_status == "approved"

    publishable = (
        bool(organisation_number)
        and organisation_number.isdigit()
        and bool(article_url)
        and bool(article_text.strip())
        and approved_acquisition
        and approved_rights
        and identity.get("publishable") is True
    )

    status = "accepted" if publishable else "quarantined"

    reasons: list[str] = []

    if not organisation_number or not organisation_number.isdigit():
        reasons.append(
            "organisation number is missing or invalid"
        )

    if not approved_acquisition:
        reasons.append(
            "acquisition mode is not approved"
        )

    if not approved_rights:
        reasons.append(
            "rights status is not approved"
        )

    if identity.get("publishable") is not True:
        reasons.append(
            "article failed exact-company identity gate"
        )

    return {
        "id": f"news-mention-{observation_id}",
        "status": status,
        "publishable": publishable,
        "organisation_number": organisation_number,
        "platform": "news",
        "signal_type": "public_mention",
        "source_class": source_class,
        "source_url": article_url,
        "publisher": publisher,
        "title": article_title[:500],
        "retrieved_at": observation_retrieved_at,
        "effective_at": observation_retrieved_at,
        "evidence_span": article_text[:5000],
        "content_sha256": content_sha256,
        "acquisition_mode": acquisition_mode,
        "rights_status": rights_status,
        "exact_entity": identity.get("publishable") is True,
        "identity_proof": identity,
        "identity": identity,
        "reasons": reasons,
    }