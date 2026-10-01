import json
import json
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

INPUT = Path("out/email-domain-candidates.jsonl")
OUTPUT = Path("out/email-domain-checks.jsonl")

rows = [
    json.loads(line)
    for line in INPUT.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

def check(row):
    domain = row["domain"]

    results = []

    for scheme in ("https://", "http://"):
        url = scheme + domain + "/"

        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; research-check/1.0)"
                },
            )

            with urllib.request.urlopen(req, timeout=4) as response:
                body = response.read(200000)
                results.append({
                    "url": url,
                    "status": response.status,
                    "final_url": response.geturl(),
                    "content_type": response.headers.get("Content-Type"),
                    "bytes_sampled": len(body),
                    "body_sample": body.decode("utf-8", errors="ignore")[:20000],
                })

                if 200 <= response.status < 400:
                    break

        except urllib.error.HTTPError as e:
            results.append({
                "url": url,
                "status": e.code,
                "error": "HTTPError",
            })

        except Exception as e:
            results.append({
                "url": url,
                "status": None,
                "error": type(e).__name__,
            })

    return {
        **row,
        "checks": results,
    }

OUTPUT.parent.mkdir(parents=True, exist_ok=True)

completed = 0
results = []

with ThreadPoolExecutor(max_workers=16) as executor:
    futures = [executor.submit(check, row) for row in rows]

    for future in as_completed(futures):
        result = future.result()
        results.append(result)
        completed += 1
        print(f"DONE {completed}/{len(rows)} {result['domain']}", flush=True)

results.sort(key=lambda x: x["domain"])

with OUTPUT.open("w", encoding="utf-8") as f:
    for result in results:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")

print()
print(f"INPUT={len(rows)}")
print(f"OUTPUT={len(results)}")
print(f"WROTE={OUTPUT}")
