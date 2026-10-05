"""Compare candidate facts with their actual inline-XBRL filing documents."""
import hashlib,json,os,re,time
from decimal import Decimal,InvalidOperation
from pathlib import Path
import httpx
from lxml import html

root=Path('/evidence')
query=json.loads((root/'finance-live-contact.json').read_text())
records=[]; documents={}
def name(element):
    return str(element.tag).split('}')[-1].split(':')[-1].lower()
def fetch(client,url):
    time.sleep(.3)
    with client.stream('GET',url) as response:
        response.raise_for_status(); content=bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content)>32*1024*1024:raise ValueError('document too large')
        return bytes(content)
with httpx.Client(headers={'User-Agent':os.environ['TRADING_SEC_USER_AGENT']},timeout=15,follow_redirects=False) as client:
    for item in query['queries']:
        if item['tool']!='trading_financials':continue
        result=item['result'];cik=int(result['cik'])
        submission=json.loads(fetch(client,f'https://data.sec.gov/submissions/CIK{cik:010d}.json'))
        recent=submission['filings']['recent']
        lookup=dict(zip(recent['accessionNumber'],recent['primaryDocument']))
        for period in result['periods']:
            for metric,fact in period['metrics'].items():
                if fact['value'] is None:continue
                accession=fact['accession']
                if accession not in documents:
                    doc=lookup[accession];assert re.fullmatch(r'[A-Za-z0-9_.-]+',doc)
                    url=f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-", "")}/{doc}'
                    body=fetch(client,url);tree=html.fromstring(body)
                    contexts={};units={}
                    for e in tree.iter():
                        if name(e)=='context':
                            if any(name(child) in ('explicitmember','typedmember') for child in e.iter()):continue
                            fields={name(child):''.join(child.itertext()).strip() for child in e.iter() if name(child) in ('startdate','enddate','instant')}
                            contexts[e.get('id')]=(fields.get('startdate'),fields.get('enddate',fields.get('instant')))
                        if name(e)=='unit':
                            units[e.get('id')]=[str(child.text).split(':')[-1] for child in e.iter() if name(child)=='measure']
                    facts=[]
                    for e in tree.iter():
                        if name(e)!='nonfraction' or units.get(e.get('unitref'))!=['USD']:continue
                        try:
                            value=Decimal(''.join(e.itertext()).replace(',','').strip())*(Decimal(10)**int(e.get('scale','0')))
                            if e.get('sign')=='-':value=-value
                        except (InvalidOperation,ValueError):continue
                        facts.append((e.get('name','').lower(),contexts.get(e.get('contextref')),value))
                    documents[accession]={'url':url,'sha256':hashlib.sha256(body).hexdigest(),'facts':facts}
                d=documents[accession]
                wanted=(fact['tag'].lower(),(fact['start'],fact['end']),Decimal(str(fact['value'])))
                assert wanted in d['facts'],f'{item["symbol"]} {metric} {fact["end"]} does not match filing'
                records.append(dict(symbol=item['symbol'],metric=metric,start=fact['start'],end=fact['end'],unit=fact['unit'],value=fact['value'],accession=accession,source_url=d['url'],matched=True))
receipt=dict(status='passed',matched_facts=len(records),documents={a:{k:v for k,v in d.items() if k!='facts'} for a,d in documents.items()},checks=records)
(root/'sec-filing-values.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({'status':'passed','matched_facts':len(records),'filings':len(documents)}))
