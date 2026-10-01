import argparse, json, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profiles',required=True)
    parser.add_argument('--feed',action='append',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    profiles=[json.loads(x) for x in Path(args.profiles).read_text(encoding='utf-8').splitlines() if x.strip()]
    companies={str(p.get('organisation_number')):str(p.get('name') or '').strip() for p in profiles}
    candidates=[]
    for feed_url in args.feed:
        req=urllib.request.Request(feed_url,headers={'User-Agent':'Signalpost/1.0'})
        with urllib.request.urlopen(req,timeout=15) as r:
            root=ET.fromstring(r.read())
        for item in root.findall('.//item'):
            title=(item.findtext('title') or '').strip()
            link=(item.findtext('link') or '').strip()
            source=item.find('source')
            publisher=(source.text or '').strip() if source is not None else ''
            haystack=title.lower()
            for org,name in companies.items():
                if name and len(name)>=6 and name.lower() in haystack:
                    candidates.append({'organisation_number':org,'article_url':link,'publisher':publisher,'title':title,'discovery_feed':feed_url})
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in candidates),encoding='utf-8')
    print(json.dumps({'feeds':len(args.feed),'candidate_articles':len(candidates),'output':str(output)}))

if __name__=='__main__':
    main()
