import json
from pathlib import Path

FEED=Path("nav-test.json")
OUT=Path("out/nav-observations.jsonl")

def norm(s):
    return " ".join((s or "").upper().replace("\\u00a0", " ").split())

d=json.loads(FEED.read_text(encoding="utf-8"))
rows=d.get("items",[])
profiles={}
for line in Path("out/profiles.jsonl").read_text(encoding="utf-8").splitlines():
    if line.strip():
        r=json.loads(line)
        profiles[norm(r.get("name"))]=r

obs=[]
for item in rows:
    e=item.get("_feed_entry") or {}
    employer=norm(e.get("businessName"))
    profile=profiles.get(employer)
    if not profile:
        continue
    obs.append({
        "organisation_number":profile["organisation_number"],
        "name":profile["name"],
        "platform":"job_board",
        "signal_type":"job_posting",
        "source_url":"https://pam-stilling-feed.nav.no/api/v1/feed",
        "source_class":"official_api",
        "acquisition_mode":"official_api",
        "title":item.get("title"),
        "observed_at":item.get("date_modified") or e.get("sistEndret"),
        "employer_name":e.get("businessName"),
        "nav_uuid":e.get("uuid"),
        "status":e.get("status"),
    })

OUT.parent.mkdir(parents=True,exist_ok=True)
with OUT.open("w",encoding="utf-8") as f:
    for x in obs:
        f.write(json.dumps(x,ensure_ascii=False)+"\
")
print("NAV_ITEMS=",len(rows))
print("NAV_EXACT_NAME_MATCHES=",len(obs))
print("WROTE=",OUT)
