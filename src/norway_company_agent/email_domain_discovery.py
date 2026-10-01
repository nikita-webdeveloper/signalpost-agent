from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any

from .identity import apply_website_identity_gate
from .website import fetch_website

GENERIC_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "msn.com",
    "yahoo.com",
    "yahoo.no",
    "icloud.com",
    "me.com",
    "mac.com",
    "proton.me",
    "protonmail.com",
    "yahoo.fr",
}

# Cache the raw website fetch by domain.
# Identity matching is still performed separately for every company.
_CACHE_PATH = Path("out/email-domain-cache.json")

_CACHE_TTL_SECONDS = 24 * 60 * 60
# Value is (website, metrics, cached_at). cached_at is retained in memory so
# that freshness can still be decided per-lookup at use time -- it must NOT
# be used to decide whether an on-disk entry gets *loaded*, or a save from a
# process that only touched a handful of domains would silently evict every
# other legitimately-cached domain (see regression: a save wiped the cache
# down to only the domains fetched in that one run).
_WEBSITE_DOMAIN_CACHE: dict[str, tuple[dict[str, Any], dict[str, Any], float]] = {}
_CACHE_LOADED = False


def _read_cache_entries_from_disk() -> dict[str, dict[str, Any]]:
    if not _CACHE_PATH.exists():
        return {}
    try:
        body = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    entries = body.get("entries", {})
    if not isinstance(entries, dict):
        return {}
    return entries


def _load_domain_cache() -> None:
    global _CACHE_LOADED
    if _CACHE_LOADED:
        return
    _CACHE_LOADED = True
    entries = _read_cache_entries_from_disk()
    for domain, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        website = entry.get("website")
        metrics = entry.get("metrics")
        cached_at = entry.get("cached_at")
        if not isinstance(cached_at, (int, float)):
            continue
        # Load every entry regardless of age so a later save never drops it.
        # TTL is only consulted at lookup time (see discover_website_from_email_domain)
        # to decide whether a *specific* lookup may reuse it without a fresh fetch.
        if isinstance(website, dict) and isinstance(metrics, dict):
            _WEBSITE_DOMAIN_CACHE[str(domain)] = (website, metrics, float(cached_at))


def _is_cache_entry_fresh(cached_at: float) -> bool:
    return time.time() - cached_at <= _CACHE_TTL_SECONDS


def _save_domain_cache() -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Merge with whatever is currently on disk rather than overwriting wholesale.
    # This protects legitimately-cached domains that this process never looked
    # up (and therefore never loaded into memory) from being wiped out.
    on_disk = _read_cache_entries_from_disk()
    merged: dict[str, dict[str, Any]] = dict(on_disk) if isinstance(on_disk, dict) else {}
    for domain, (website, metrics, cached_at) in _WEBSITE_DOMAIN_CACHE.items():
        merged[domain] = {"website": website, "metrics": metrics, "cached_at": cached_at}
    body = {
        "schema_version": 1,
        "entries": merged,
    }
    temporary = _CACHE_PATH.with_suffix(_CACHE_PATH.suffix + ".tmp")
    text = json.dumps(body, ensure_ascii=False, indent=2) + chr(10)
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(_CACHE_PATH)


def extract_email_domain(profile: dict[str, Any]) -> str | None:
    registry = profile.get("evidence", {}).get("registry", {})
    value = registry.get("value") or {}
    email = str(value.get("epostadresse") or "").strip().casefold()

    if not email or "@" not in email:
        return None

    local, domain = email.rsplit("@", 1)
    domain = domain.strip().rstrip(".")

    if not local or not domain:
        return None

    if domain in GENERIC_EMAIL_DOMAINS:
        return None

    if len(domain) > 253 or "." not in domain:
        return None

    if not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
        domain,
    ):
        return None

    return domain


def discover_website_from_email_domain(
    profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    domain = extract_email_domain(profile)

    if not domain:
        return (
            {
                "status": "not_found",
                "source_type": "registry_email_domain_company_website",
                "source_class": "registry_email_domain_company_website",
                "source_url": "",
                "retrieved_at": "",
                "value": None,
                "note": (
                    "no eligible company email domain available "
                    "in BRREG registry evidence"
                ),
            },
            {
                "requests": 0,
                "bytes": 0,
                "latencies_ms": [],
            },
        )

    _load_domain_cache()
    candidate_url = f"https://{domain}"

    cached_entry = _WEBSITE_DOMAIN_CACHE.get(domain)
    if cached_entry is not None and _is_cache_entry_fresh(cached_entry[2]):
        cached_website, cached_metrics, _cached_at = cached_entry
        website_record = copy.deepcopy(cached_website)
        metrics = {
            "requests": 0,
            "bytes": 0,
            "latencies_ms": [],
        }
    else:
        website_record, metrics = fetch_website(candidate_url)

        _WEBSITE_DOMAIN_CACHE[domain] = (
            copy.deepcopy(website_record),
            copy.deepcopy(metrics),
            time.time(),
        )
        _save_domain_cache()

    gated = apply_website_identity_gate(
        profile,
        copy.deepcopy(website_record),
    )

    website_record = gated["website"]

    value = website_record.get("value") or {}

    if website_record.get("status") == "available":
        value["discovery_method"] = "registry_email_domain"
        value["discovery_domain"] = domain

        website_record["source_type"] = (
            "registry_email_domain_company_website"
        )

        website_record["source_class"] = (
            "registry_email_domain_company_website"
        )

        website_record["value"] = value

    return website_record, metrics