import json,os,sys,time,urllib.request,concurrent.futures as cf
key=os.environ['FIRECRAWL_API_KEY']
pages=json.load(open('docs/scraping-test/job-b/pages100.json'))
def one(item):
    k,u=item; out=f'/tmp/st/v2/product/{k}.json'
    if os.path.exists(out): return k,'cached'
    body=json.dumps({"url":u,"formats":["product"],"timeout":300000}).encode()
    req=urllib.request.Request('https://api.firecrawl.dev/v2/scrape',data=body,headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'})
    err='gave up after retries'
    for attempt in range(6):
        try:
            r=urllib.request.urlopen(req,timeout=330); d=json.loads(r.read())
            json.dump(d,open(out,'w')); return k,'ok'
        except urllib.error.HTTPError as e:
            txt=e.read()[:300].decode('utf8','replace')
            if e.code in (429,500,502,503): err=f'http {e.code}'; time.sleep(30*(attempt+1)); continue
            json.dump({'error':e.code,'body':txt},open(out,'w')); return k,f'http {e.code} {txt[:80]}'
        except Exception as e:
            err=str(e)[:100]
    json.dump({'error':err},open(out,'w')); return k,f'err {err}'
with cf.ThreadPoolExecutor(4) as ex:
    for k,s in ex.map(one,list(pages.items())): print(k,s,flush=True)
print('DONE',flush=True)
