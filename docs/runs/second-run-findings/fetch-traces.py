# Pull every Langfuse observation of the given chat session ids into /tmp/traces2/<id>.json.
# Run with the repo .env loaded (LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY).
# The v2 API takes sessionId directly and pages with meta.cursor.
import os,json,urllib.request,base64,urllib.parse,sys
base=os.environ['LANGFUSE_BASE_URL'];auth=base64.b64encode(f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()).decode()
for sid in sys.argv[1:]:
    out=[];cursor=None
    while True:
        q={'sessionId':sid,'limit':'1000','fields':'core,basic,io,metadata,metrics,model,scores'}
        if cursor:q['cursor']=cursor
        r=urllib.request.Request(base+'/api/public/v2/observations?'+urllib.parse.urlencode(q),headers={'Authorization':'Basic '+auth})
        d=json.load(urllib.request.urlopen(r))
        out+=d['data'];cursor=(d.get('meta') or {}).get('cursor')
        if not cursor or not d['data']:break
    json.dump(out,open(f'/tmp/traces2/{sid}.json','w'))
    print(sid,len(out))
