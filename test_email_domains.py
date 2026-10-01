import json
import csv
import gzip
import re
from norway_company_agent.website import fetch_website
from norway_company_agent.identity import assess_website_identity

def main():
    wanted = {
        str(json.loads(x)["organisation_number"]): json.loads(x)
        for x in open("entry-companies.jsonl", encoding="utf-8")
        if x.strip()
    }

    f = gzip.open("brreg-enheter.csv", "rt", encoding="utf-8-sig", newline="")
    r = csv.DictReader(f)

    stop = {
        "as", "asa", "ab", "og", "the", "company", "group",
        "norge", "norway", "inc", "ltd", "holding", "services",
        "bygg", "multi", "master", "surgery", "systems", "soft"
    }

    candidates = []

    for row in r:
        org = row.get("organisasjonsnummer", "")
        email = row.get("epostadresse", "")

        if org in wanted and not wanted[org].get("website") and "@" in email:
            name = row.get("navn", "").strip()
            domain = email.rsplit("@", 1)[-1].strip().lower()
            tokens = re.findall(r"[a-z0-9]+", name.lower())

            if (
                domain
                and " " not in domain
                and any(
                    len(t) >= 4 and t not in stop and t in domain
                    for t in tokens
                )
            ):
                candidates.append((org, name, domain))

    f.close()

    seen = set()
    results = []

    for org, name, domain in candidates:
        if domain not in seen:
            seen.add(domain)
            results.append((org, name, domain))

    print("Testing candidates:", len(results))

    for org, name, domain in results:
        try:
            rec, metrics = fetch_website("https://" + domain, timeout=10)

            if rec.get("status") == "available":
                profile = {
                    "organisation_number": org,
                    "name": name,
                    "evidence": {"website": rec},
                }

                assessment = assess_website_identity(profile)

                print(
                    org, "|", name, "|", domain, "|",
                    assessment.get("status"), "|",
                    assessment.get("score")
                )
            else:
                print(
                    org, "|", name, "|", domain,
                    "| FETCH_" + str(rec.get("status"))
                )

        except Exception:
            import traceback
            print(org, "|", name, "|", domain, "| ERROR")
            traceback.print_exc()


if __name__ == "__main__":
    main()

