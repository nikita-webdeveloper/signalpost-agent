import urllib.request
import urllib.parse
import json

profiles = [json.loads(line) for line in open('out/profiles.jsonl', encoding='utf-8')]
our_orgs = [p['organisation_number'] for p in profiles]

values_clause = ' '.join(f'"{org}"' for org in our_orgs)
q = f'''SELECT ?item ?itemLabel ?orgnr WHERE {{
  VALUES ?orgnr {{ {values_clause} }}
  ?item wdt:P2333 ?orgnr .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "nb,nn,no,en". }}
}}'''

url = 'https://query.wikidata.org/sparql'
data = urllib.parse.urlencode({'query': q, 'format': 'json'}).encode('utf-8')
req = urllib.request.Request(
    url,
    data=data,
    headers={
        'User-Agent': 'SignalpostBot/1.0 (https://builderr.ai; contact@builderr.ai)',
        'Accept': 'application/sparql-results+json, application/json',
    }
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        bindings = data.get('results', {}).get('bindings', [])
        print(f'Retrieved {len(bindings)} Wikidata matches for our 1000 companies!')
        for b in bindings:
            print(' ', b.get('orgnr', {}).get('value'), b.get('itemLabel', {}).get('value'), b.get('item', {}).get('value'))
        with open('out/wikidata-matches.json', 'w', encoding='utf-8') as f:
            json.dump(bindings, f, indent=2)
except Exception as e:
    print('Error:', e)
