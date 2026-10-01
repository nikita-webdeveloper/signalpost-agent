#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from collections import defaultdict, Counter
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external_footprint import publishable_observation, validate_observation
from scripts.merge_observations import merge
from scripts.extract_company_site_activity import observation as activity_obs
from scripts.extract_company_site_news import observation as news_obs
from norway_company_agent.social_observations import social_observations_from_website

out_dir = ROOT / "out"

def safe_read(p):
    if not p.exists():
        return []
    lines = []
    for l in p.read_text(encoding="utf-8").splitlines():
        l = l.strip()
        if not l:
            continue
        if l.startswith("{") and "}{" in l:
            for sp in l.replace("}{", "}\n{").splitlines():
                if sp.strip():
                    lines.append(sp.strip())
        else:
            lines.append(l)
    res = []
    for line in lines:
        try:
            res.append(json.loads(line))
        except Exception:
            pass
    return res

profiles = safe_read(out_dir / "profiles.jsonl")
known_orgs = {str(p["organisation_number"]) for p in profiles}

baseline = safe_read(out_dir / "external-observations-breadth-v5-sentiment17.jsonl")
print(f"Baseline observations: {len(baseline)}")

# Candidate sources
candidate_lists = []

# 1. Social observations from all known profile variants
profile_variants = [
    "profiles-email-fallback-v1.jsonl",
    "profiles-breadth-untouched-v1.jsonl",
    "step4-profiles.jsonl",
    "profiles-review-pages-v1.jsonl",
    "profiles.jsonl"
]

social_candidates = []
for pv in profile_variants:
    p_path = out_dir / pv
    if p_path.exists():
        p_rows = safe_read(p_path)
        for p in p_rows:
            if isinstance(p, dict) and "organisation_number" in p:
                socs = social_observations_from_website(p)
                for s in socs:
                    if publishable_observation(s):
                        social_candidates.append(s)

print(f"Gathered {len(social_candidates)} valid social candidates")
candidate_lists.append(social_candidates)

# 2. Activity observations from all known profile variants
activity_candidates = []
for pv in profile_variants:
    p_path = out_dir / pv
    if p_path.exists():
        p_rows = safe_read(p_path)
        for p in p_rows:
            if isinstance(p, dict) and "organisation_number" in p:
                act = activity_obs(p)
                if act and publishable_observation(act):
                    activity_candidates.append(act)

print(f"Gathered {len(activity_candidates)} valid site activity candidates")
candidate_lists.append(activity_candidates)

# 3. Site news from all known profile variants
site_news_candidates = []
for pv in profile_variants:
    p_path = out_dir / pv
    if p_path.exists():
        p_rows = safe_read(p_path)
        for p in p_rows:
            if isinstance(p, dict) and "organisation_number" in p:
                nws = news_obs(p)
                if nws and publishable_observation(nws):
                    site_news_candidates.append(nws)

print(f"Gathered {len(site_news_candidates)} valid site news candidates")
candidate_lists.append(site_news_candidates)

# 4. Wikidata observations
wiki_path = out_dir / "external-observations-wikidata.jsonl"
if wiki_path.exists():
    wiki_obs = safe_read(wiki_path)
    valid_wiki = [w for w in wiki_obs if publishable_observation(w)]
    print(f"Gathered {len(valid_wiki)} valid wikidata candidates")
    candidate_lists.append(valid_wiki)

# Perform safe merge
merged, details = merge(baseline, candidate_lists, known_orgs)
print(f"\nMerge summary:")
print(f"  Base rows: {len(baseline)}")
print(f"  Merged rows: {len(merged)}")
print(f"  Added rows: {details['counts'].get('added', 0)}")
print(f"  Stats: {details['counts']}")

out_file = out_dir / "external-observations-v7-merged.jsonl"
out_file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged), encoding="utf-8")
print(f"\nWrote {len(merged)} observations to {out_file.name}")
