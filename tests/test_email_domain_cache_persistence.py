from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent import email_domain_discovery as ed  # noqa: E402


class EmailDomainCachePersistenceTests(unittest.TestCase):
    """Regression test for a bug where saving newly-fetched domains to
    out/email-domain-cache.json silently deleted every previously-cached
    domain that this process run never looked up (because loading skipped
    entries older than the reuse TTL, and saving overwrote the file from
    that truncated in-memory view instead of merging with what was on disk).
    """

    def setUp(self) -> None:
        self._original_cache_path = ed._CACHE_PATH
        self._original_cache = dict(ed._WEBSITE_DOMAIN_CACHE)
        self._original_loaded = ed._CACHE_LOADED

        self._tmp_path = ROOT / "out" / "_test_email_domain_cache.json"
        if self._tmp_path.exists():
            self._tmp_path.unlink()
        ed._CACHE_PATH = self._tmp_path
        ed._WEBSITE_DOMAIN_CACHE = {}
        ed._CACHE_LOADED = False

    def tearDown(self) -> None:
        ed._CACHE_PATH = self._original_cache_path
        ed._WEBSITE_DOMAIN_CACHE = self._original_cache
        ed._CACHE_LOADED = self._original_loaded
        if self._tmp_path.exists():
            self._tmp_path.unlink()
        tmp_suffix = self._tmp_path.with_suffix(self._tmp_path.suffix + ".tmp")
        if tmp_suffix.exists():
            tmp_suffix.unlink()

    def test_save_does_not_evict_stale_but_present_on_disk_entries(self):
        old_entry = {
            "website": {"status": "available", "value": {"final_url": "https://old-example.no"}},
            "metrics": {"requests": 1, "bytes": 10, "latencies_ms": [5]},
            "cached_at": time.time() - (ed._CACHE_TTL_SECONDS * 10),
        }
        self._tmp_path.parent.mkdir(parents=True, exist_ok=True)
        self._tmp_path.write_text(
            json.dumps({"schema_version": 1, "entries": {"old-example.no": old_entry}}),
            encoding="utf-8",
        )

        ed._load_domain_cache()
        self.assertIn("old-example.no", ed._WEBSITE_DOMAIN_CACHE)

        ed._WEBSITE_DOMAIN_CACHE["new-example.no"] = (
            {"status": "available", "value": {"final_url": "https://new-example.no"}},
            {"requests": 1, "bytes": 20, "latencies_ms": [7]},
            time.time(),
        )
        ed._save_domain_cache()

        on_disk = json.loads(self._tmp_path.read_text(encoding="utf-8"))["entries"]
        self.assertIn("new-example.no", on_disk, "newly-fetched domain should be persisted")
        self.assertIn(
            "old-example.no",
            on_disk,
            "a save must not silently delete a previously-cached domain it never touched",
        )

    def test_stale_entry_is_not_reused_without_a_fresh_fetch(self):
        self.assertFalse(ed._is_cache_entry_fresh(time.time() - (ed._CACHE_TTL_SECONDS * 2)))
        self.assertTrue(ed._is_cache_entry_fresh(time.time()))


if __name__ == "__main__":
    unittest.main()