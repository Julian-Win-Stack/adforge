import json, sys, os, re
NAMES = {
"30e55fb7":"00-graza-frizzle-smoke-test","9007e285":"21-snoo-needs-js","7c048689":"22-necessaire-category-page",
"f810c0be":"23-stanley-quencher-first","33ca8bc1":"24-life-fitness-treadmill","b1daed9c":"01-naturium-serum-first",
"01a05283":"23-stanley-quencher-redo","a0754894":"01-naturium-serum-redo","bb1500f2":"02-merit-flush-balm",
"67352137":"03-vital-proteins-collagen","06b1f8ad":"04-mack-weldon-sweatshirt","6c9c5065":"05-steve-madden-bag",
"30cc012f":"06-anker-power-bank","c9232a32":"07-great-jones-dutch-baby","c7feaf30":"08-mollys-suds-cleaner",
"d14a8fe7":"09-momofuku-chili-crunch","cff47d8c":"10-maxbone-tether-toy","6c5ab725":"11-beardbrand-cologne",
"039f4b2f":"12-smartish-gripmunk-case","6b9826af":"13-gorjana-studs","a63ac7d8":"14-branch-swivel-chair",
"cff029cb":"15-necessaire-body-ritual-kit","b7535102":"16-mushie-teething-ring","43c0d0dd":"17-brickell-beard-oil",
"59716b2e":"18-starface-hydro-stars","7cfcd96d":"19-titan-foam-mat","6609e69d":"20-supergoop-sunscreen",
"9ad87e95":"01-naturium-serum-3rd-try"}
def J(x):
    if isinstance(x,str):
        try: return json.loads(x)
        except Exception: return x
    return x
def trunc(s,n):
    s=s if isinstance(s,str) else json.dumps(s,ensure_ascii=False)
    return s if len(s)<=n else s[:n]+f"…[+{len(s)-n} chars]"
def strip_media(x):
    return re.sub(r"@@@langfuseMedia:type=([^|]+)\|id=[^|]+\|source=bytes@@@", r"<media \1>", x)
def render(path,out):
    obs=json.load(open(path))
    byid={o['id']:o for o in obs}
    kids={}
    for o in obs:
        p=o.get('parentObservationId')
        kids.setdefault(p if p in byid else None,[]).append(o)
    for k in kids: kids[k].sort(key=lambda o:o['startTime'])
    lines=[]
    def depth(o):
        d=0; p=o.get('parentObservationId')
        while p and p in byid: d+=1; p=byid[p].get('parentObservationId')
        return d
    prev_conv_len={}
    def emit(o):
        d=depth(o); ind='  '*d
        t=o['startTime'][11:19]; lat=o.get('latency')
        md={k:v for k,v in (o.get('metadata') or {}).items() if not k.startswith(('scope.','resourceAttributes.'))}
        hdr=f"{ind}[{t}] {o['type']} {o['name']}"
        if lat: hdr+=f" ({lat:.1f}s)"
        if o.get('model'): hdr+=f" model={o['model']}"
        if o.get('level') and o['level']!='DEFAULT': hdr+=f" **{o['level']}**"
        if o.get('statusMessage'): hdr+=f" status={o['statusMessage']!r}"
        lines.append(hdr)
        if md: lines.append(f"{ind}  meta: {trunc(md,600)}")
        i=J(o.get('input')); out_=J(o.get('output'))
        name=o['name']
        if o['type']=='GENERATION' and name in ('produce','probe'):
            h=(i or {}).get('handoff',{}) if isinstance(i,dict) else {}
            conv=h.get('conversation',[]) if isinstance(h,dict) else []
            n=prev_conv_len.get('c',0)
            new=conv[n:] if len(conv)>=n else conv
            prev_conv_len['c']=len(conv)
            for c in new:
                lines.append(f"{ind}  ›conv[{c.get('kind')}/{c.get('by','')}]: {trunc(strip_media(json.dumps({k:v for k,v in c.items() if k not in ('kind','by')},ensure_ascii=False)),1500)}")
            shown=(i or {}).get('shown') if isinstance(i,dict) else None
            if shown: lines.append(f"{ind}  shown: {strip_media(json.dumps(shown))}")
            if isinstance(out_,dict):
                o2=out_.get('output',out_)
                lines.append(f"{ind}  SAYS: {trunc(json.dumps(o2.get('says') if isinstance(o2,dict) else o2,ensure_ascii=False),1500)}")
                if isinstance(o2,dict) and o2.get('calls'):
                    for c in o2['calls']: lines.append(f"{ind}  CALL {c.get('tool')} {trunc(json.dumps(c.get('arguments'),ensure_ascii=False),800)}")
            else: lines.append(f"{ind}  out: {trunc(strip_media(json.dumps(out_)),800)}")
        elif o['type']=='TOOL':
            lines.append(f"{ind}  args: {trunc(json.dumps(i,ensure_ascii=False),800)}")
            lines.append(f"{ind}  result: {trunc(strip_media(json.dumps(out_,ensure_ascii=False)),2500)}")
        elif o['type']=='GENERATION':
            h=(i or {}).get('handoff',i) if isinstance(i,dict) else i
            if isinstance(h,dict):
                h2={}
                for k,v in h.items():
                    if k in ('page_text',): h2[k]=trunc(v,300)
                    elif k=='conversation': h2[k]=f"<{len(v)} entries>"
                    else: h2[k]=v
                lines.append(f"{ind}  in: {trunc(strip_media(json.dumps(h2,ensure_ascii=False)),2500)}")
            else: lines.append(f"{ind}  in: {trunc(strip_media(json.dumps(h,ensure_ascii=False)),1500)}")
            shown=(i or {}).get('shown') if isinstance(i,dict) else None
            if shown: lines.append(f"{ind}  shown: {strip_media(json.dumps(shown))}")
            lines.append(f"{ind}  out: {trunc(strip_media(json.dumps(out_,ensure_ascii=False)),3000)}")
        else:
            if i: lines.append(f"{ind}  in: {trunc(strip_media(json.dumps(i,ensure_ascii=False)),1200)}")
            if out_: lines.append(f"{ind}  out: {trunc(strip_media(json.dumps(out_,ensure_ascii=False)),1500)}")
        for k in kids.get(o['id'],[]): emit(k)
    for r in kids.get(None,[]): emit(r)
    open(out,'w').write("\n".join(lines))
    return len(lines)
if __name__=="__main__":
  for f in sorted(os.listdir('/tmp/traces')):
    if not f.endswith('.json') or f=='all.json': continue
    key=f[:8]; name=NAMES.get(key, f[:-5])
    n=render(f'/tmp/traces/{f}', f'/tmp/traces/rendered/{name}.md')
    print(name, n)
