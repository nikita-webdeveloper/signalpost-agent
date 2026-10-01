#!/usr/bin/env python3
import json
from pathlib import Path
from collections import Counter, defaultdict
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external_footprint import publishable_observation, validate_observation, PLATFORMS, SIGNAL_TYPES
from norway_company_agent.sentiment import classify_text_sentiment

out_dir = ROOT / "out"
profiles_path = out_dir / "profiles.jsonl"
profiles = [json.loads(l) for l in profiles_path.read_text(encoding="utf-8").splitlines() if l.strip()]
print(f"Loaded {len(profiles)} master profiles.")

# 1. Social links in profiles
social_by_platform = defaultdict(list)
for p in profiles:
    org = str(p["organisation_number"])
    web = (p.get("evidence") or {}).get("website") or {}
    val = web.get("value") or {}
    socials = val.get("social_links") or []
    for s in socials:
        plat = s.get("platform")
        url = s.get("url")
        handle = s.get("handle")
        social_by_platform[plat].append((org, url, handle))

print(f"\nSocial links found across master profiles:")
for plat, items in sorted(social_by_platform.items()):
    orgs = {x[0] for x in items}
    print(f"  {plat:15s}: {len(items):4d} links across {len(orgs):4d} companies")

# 2. Check if other profile files have more website / social links
other_profile_files = list(out_dir.glob("*profile*.jsonl"))
print(f"\nScanning {len(other_profile_files)} profile files for additional social links or website data...")
for pf in other_profile_files:
    if pf.name == "profiles.jsonl":
        continue
    p_rows = []
    try:
        for l in pf.read_text(encoding="utf-8").splitlines():
            if l.strip():
                p_rows.append(json.loads(l))
    except Exception:
        continue
    soc_count = sum(len((((p.get("evidence") or {}).get("website") or {}).get("value") or {}).get("social_links") or []) for p in p_rows if isinstance(p, dict))
    web_avail = sum(1 for p in p_rows if isinstance(p, dict) and ((p.get("evidence") or {}).get("website") or {}).get("status") == "available")
    print(f"  {pf.name:45s}: {len(p_rows):4d} rows, {web_avail:4d} web available, {soc_count:4d} social links")
