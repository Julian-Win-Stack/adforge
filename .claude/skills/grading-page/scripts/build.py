import json,os,re,hashlib,sys
S=os.path.dirname(os.path.abspath(__file__))
D=json.load(open(S+'/data.json'))
sums={}
for n in (1,2):
    p=f'{S}/sum-out-{n}.json'
    if os.path.exists(p): sums.update(json.load(open(p)))
mapp=json.load(open(S+'/media-map.json')) if os.path.exists(S+'/media-map.json') else {}
r7map=json.load(open(S+'/r7-map.json')) if os.path.exists(S+'/r7-map.json') else {}
# The Langfuse project's sessions address, ending in /sessions/ (LANGFUSE_BASE_URL + project id).
LANG=os.environ['LANGFUSE_SESSIONS_URL']
need=dict(assets={},files={})   # key -> reason
def name(k):
    return hashlib.sha1(k.encode()).hexdigest()[:14]+os.path.splitext(k)[1].lower()
def useA(k):
    if k: need['assets'][k]=1
    return k
def useF(k):
    if k: need['files'][k]=1
    return k
def cost_sorted(c): return sorted(c.items(),key=lambda x:-x[1])
def strip(r):
    r={k:v for k,v in (r or {}).items() if k not in('pictures','wording','models')}
    return r
def image_inputs(p,pictures,plabel):
    """What the image model was sent to draw picture `p`, in the order sent, from its own
    record; None when that record is missing."""
    im=(p or {}).get('image')
    if not im: return None
    # An edit's photos carry their lines; a photo without one takes its line from an
    # earlier picture of the scene that sent it.
    lines={x['file']:x['line'] for q in pictures for x in q.get('photos') or [] if x.get('line')}
    lines.update({x['file']:x['line'] for x in p.get('photos') or [] if x.get('line')})
    named={}
    src=next((q for q in pictures if q['pk']==p.get('edited_from')),None)
    if src: named[src['file']]=f"the picture being edited ({plabel.get(src['file'],'an earlier picture')})"
    if p.get('made_from'): named[p['made_from']['file']]=f"the starting picture it ends from ({plabel.get(p['made_from']['file'],'starting picture v'+str(p['made_from']['version']))})"
    ins=[dict(file=useA(k),label=f"{i}. "+(named.get(k) or lines.get(k) or 'no line recorded')) for i,k in enumerate(im['pictures'],1)]
    return dict(model=im['model'],provider=im['provider'],cost=im['cost'],prompt=im['prompt'],inputs=ins)
def video_inputs(c,se,plabel,vpos):
    """What the video model (H3) was sent for clip `c`, from its own call record (`make`), or
    from the clip's sent list when that record is missing."""
    mk=c.get('make') or {}
    h=mk.get('handoff') or {}
    if h:
        start,end,examples,refs=h.get('starting_picture'),h.get('end_picture'),h.get('example_pictures') or [],h.get('reference_videos') or []
    else:
        start=(se.get('starting_picture') or {}).get('file'); end=(se.get('end_picture') or {}).get('file')
        examples=[p.get('file') for p in se.get('pictures') or []] if se.get('pieces') else []
        if examples: start=end=None
        refs=[dict(file=p.get('file'),video=None,position=p.get('video'),start=p.get('start'),end=p.get('end')) for p in se.get('pieces') or []]
    pics=[]
    if start: pics.append(dict(file=useA(start),label='Starting picture: the first frame H3 films from ('+plabel.get(start,'not found among this scene\'s pictures')+')'))
    if end: pics.append(dict(file=useA(end),label='End picture: the last frame ('+plabel.get(end,'not found among this scene\'s pictures')+')'))
    for i,k in enumerate(examples,1): pics.append(dict(file=useA(k),label=f'Example picture {i} ('+plabel.get(k,'not a scene picture')+')'))
    return dict(recorded=bool(h),mode='reference' if examples else 'start_frame' if start else se.get('mode'),model=mk.get('model'),provider=mk.get('provider'),cost=mk.get('cost'),
                seconds=h.get('seconds'),enhancement=h.get('prompt_enhancement'),prompt=h.get('prompt'),pictures=pics,
                refs=[dict(file=useA(v.get('file')),video=v.get('position') or vpos.get(v.get('video')),start=v.get('start'),end=v.get('end'),trim=v.get('trim')) for v in refs])
grades={}
gd=S+'/r7grades/grades'
for fn in os.listdir(gd):
    d=json.load(open(os.path.join(gd,fn)))
    grades[fn[:-5]]=d.get('data',d)
ADS={};FIRST=[];ANS=[]
RED=0
for k,a in D['r8'].items():
    reds=[w for w in a['warnings'] if w.get('level')=='problem']
    wall=[dict(level=w.get('level'),kind=w.get('kind','?'),text=w.get('text','')) for w in a['warnings']]
    watch=[w['text'] for w in a['warnings'] if w.get('kind')=='check_it_yourself']
    # owner answers: every user message after the first, with the agent message before it
    msgs=a['messages']; ans=[]
    for i,m in enumerate(msgs):
        if m['role']=='user' and i>0:
            q=next((x['text'] for x in reversed(msgs[:i]) if x['role'] in('agent','notice') and '?' in x['text']),next((x['text'] for x in reversed(msgs[:i]) if x['role']!='user'),''))
            ans.append(dict(ad=k,q=q,a=m['text']))
    ANS+=ans
    scs=[];at=0.0;gav=[]
    vpos={v['file']:v['position'] for v in a['videos'] if v['file']}
    st=dict(reds=len(reds),gaveup=0,**{'pass':0,'fail':0,'unsure':0})
    ad=next((x for x in reversed(a['shared']) if x['kind']=='finished_ad'),None)
    for s in a['scenes']:
        kind=s.get('kind') or ('broll' if s.get('shows') else 'talking')
        cur=next((c for c in s['clips'] if c['pk']==s['current']),None)
        secs=cur['seconds'] if cur else (s['lines'][-1]['seconds'] if s['lines'] else 0)
        clips=[];prev=None
        toolby={t['id']:t for t in s['agent']}
        # Every starting and end picture by its file, named the way the page names it.
        plabel={p['file']:('End picture' if p.get('kind')=='end_picture' else 'Starting picture')+f" v{p['version']}" for p in s['pictures']}
        for c in s['clips']:
            r=c['ruling'] or {}; v=r.get('verdict'); is_b=bool(c['sent'])
            if v in st: st[v]+=1
            ft=c.get('fix_try') or {}
            tool=toolby.get(ft.get('tool_call'))
            sumo=sums.get(str(c['pk']),{})
            se=c['sent'] or {}
            sent=None
            if is_b:
                sent=dict(mode=se.get('mode'),colour=se.get('colour'),prompt=se.get('prompt'),recolours=se.get('recolours'),left_out=se.get('left_out'),strangers=se.get('strangers_left_out'),
                  video=video_inputs(c,se,plabel,vpos))
                if sent['video']['prompt']: sent['prompt']=sent['video']['prompt']
                useA(c['file'])
            if c['picture'] and is_b: useA(c['picture']['file'])
            form=(tool or {}).get('args',{}).get('form') if tool else None
            clips.append(dict(pk=c['pk'],broll=is_b,video=c['file'] if is_b else None,file=c['file'],seconds=c['seconds'],verdict=v,inAd=c['pk']==s['current'],checks=r.get('checks'),why=sumo.get('why'),changed=sumo.get('changed'),
              reason=(tool or {}).get('args',{}).get('reason') if tool else None,prev=(prev or {}).get('sent',{}).get('prompt') if prev and prev['sent'] else None,newPic=bool(prev and prev['picture'] and c['picture'] and prev['picture']['pk']!=c['picture']['pk']),
              sent=sent,make=c['make'],form=form,failures=[dict(check=f.get('check'),problems=f.get('problems'),fault=(f.get('answer') or {}).get('fault'),times=(f.get('answer') or {}).get('fault_frame_times')) for f in r.get('failures') or []],ruling=strip(r) if r else None,line=c.get('motion_prompt') or c.get('text') or ''))
            prev=c
        # starting pictures from the agent's own calls, with the check's answers
        sp=[];seen=set()
        pinfo={p['file']:p for p in s['pictures']}
        for t in s['agent']:
            if t['tool'] in('draw_starting_picture','edit_starting_picture','make_end_picture'):
                end=t['tool']=='make_end_picture'
                m=re.match(r'(?:Starting|End) picture (\d+): (\{.*)',t['result'] or '',re.S)
                chk=[];status=None;pk=None
                if m:
                    pk=m.group(1)
                    try:
                        j=json.JSONDecoder().raw_decode(m.group(2))[0]; status=j.get('status')
                        names=dict(product_same='Same product as the photos',physics='Physically possible')
                        chk=[dict(name=names.get(n,n.replace('_',' ')),**{'pass':bool(x.get('passes'))},says=x.get('says','')) for n,x in (j.get('answers') or {}).items()]
                    except Exception: pass
                # The result also shows the previous picture, so pick the one this call made.
                made=f"{'End' if end else 'Starting'} picture {pk}"
                file=next((x['key'] for x in t['pictures'] if x['label']==made),None) if pk else None
                if file: seen.add(file); useA(file)
                what='End picture' if end else 'Redrawn picture' if t['tool']=='edit_starting_picture' else 'Drawn picture'
                sp.append(dict(file=file,label=(plabel.get(file) or what)+(f' ({what.lower()})' if file in plabel else ''),checks=chk,status=status if status else (None if m else (t['result'] or '')[:300]),ask=t['args'],image=image_inputs(pinfo.get(file),s['pictures'],plabel)))
        for p in s['pictures']:
            if p['file'] not in seen:
                useA(p['file']); sp.append(dict(file=p['file'],label=plabel[p['file']]+(f": {p['change']}" if p['change'] else ''),checks=[],status=None,ask=None,image=image_inputs(p,s['pictures'],plabel)))
        picked=[]; pk=set()
        for t in s['agent']:
            if t['tool'] in('look_at_library',):
                for p in t['pictures']:
                    if p['key'] not in pk and 'Frames' not in (p['label'] or '')[:60] and p['label'] and p['label'].startswith('Library'):
                        pk.add(p['key']); picked.append(dict(file=useA(p['key']),label=p['label']))
        gaves=[f['gave_up'] for f in s['fixes'] if f['gave_up']]
        if gaves: st['gaveup']+=1; gav.append(dict(n=s['number'],text=gaves[-1]))
        run=None
        for f in s['fixes']:
            if f['runs']: run=f['runs'][0]
        calls=[dict(tool=t['tool'],head=t['fold'][:120] if t['fold'] else '',cost=t['cost'],said=t['said'],why=t['why'],args=t['args'],result=t['result'],bad=t['answer']=='cant',
                    pics=[dict(key=p['key'],label=p['label']) for p in t['pictures'] if (p['label'] or '').startswith(('Library','Starting')) and useA(p['key'])]) for t in s['agent']]
        scs.append(dict(n=s['number'],part=s['part'],kind=kind,kind_why=s.get('kind_why',''),broll=s['broll_kind'],line=s['line'],shows=s['shows'],gave=bool(gaves),gaves=gaves,run=run,questions=[w['text'] for w in a['warnings'] if w.get('kind')=='agent_asked' and f"scene {s['number']}," in w['text']],at=round(at,2),startPics=sp,picked=picked,clips=clips,agent=calls))
        at+=secs or 0
    # first list
    for s in scs:
        if s['gave']: FIRST.append(dict(ad=k,n=s['n'],anchor='s%d'%s['n'],cls='c-unsure',label='agent gave up',why=s['gaves'][0][:150]+'…',o=0))
        fails=[c for c in s['clips'] if c['verdict']=='fail']; uns=[c for c in s['clips'] if c['verdict']=='unsure']
        if fails and not s['gave']: FIRST.append(dict(ad=k,n=s['n'],anchor='s%d'%s['n'],cls='c-fail',label=f'judge failed {len(fails)} of {len(s["clips"])} tries',why=(fails[0]['why'] or '')[:170],o=1))
        if uns: FIRST.append(dict(ad=k,n=s['n'],anchor='s%d'%s['n'],cls='c-unsure',label=f'judge unsure on {len(uns)}',why=(uns[0]['why'] or '')[:170],o=2))
    # round 7
    rr=D['r7'].get(k); r7=None
    if rr and 'error' not in rr:
        folder=k+'-picker-fix' if k.startswith('n04') else k
        rs=[]
        rad=r7map.get(folder+'/ad.mp4')
        notes=[]
        g=grades.get(folder+'__whole-ad')
        if g: notes.append(dict(where='whole ad',grade=g.get('grade'),note=g.get('note')))
        for s in rr['scenes']:
            cl=[];ns=[]
            for doc,label in ((f'{folder}__scene-{s["number"]}','scene'),(f'{folder}__scene-{s["number"]}__kept','the clip in the ad')):
                g=grades.get(doc)
                if g: ns.append(dict(grade=g.get('grade'),note=g.get('note')))
            for j,c in enumerate(cc for cc in s['clips'] if cc['sent']):
                rid=c['remake']
                kk=f'{folder}/scene-{s["number"]}/'+('first.mp4' if not rid else f'remake-{rid}.mp4')
                g=grades.get(f'{folder}__scene-{s["number"]}__remake-{rid}') if rid else None
                if g: ns.append(dict(grade=g.get('grade'),note=g.get('note')))
                cl.append(dict(name='First clip' if not rid else f'Remake {rid}',file=r7map.get(kk),verdict=(c['ruling'] or {}).get('verdict'),kept=c['pk']==s['current']))
            rs.append(dict(n=s['number'],part=s['part'],line=s['line'],shows=s['shows'],clips=cl,notes=ns))
        r7=dict(name=rr['name'].replace('R7 ',''),session=rr['session'],ad=rad,scenes=rs,notes=notes)
    shared=[]
    for x in a['shared']:
        if x['kind'] in('portrait','before_picture','person_in_room','music'): shared.append(dict(kind=x['kind'],file=useF(x['file'])))
        elif x['kind']=='room_alone': shared.append(dict(kind=x['kind'],file=useA(x['file'])))
    lib=[dict(number=l['number'],file=useF(l['file']),label=l['label'],kind=l['kind'],side=l['side'],alone=l['alone'],colour=l['colour']) for l in a['library']]
    vids=[]
    Z=json.load(open(S+'/files.json'))
    for v in a['videos']:
        if v['file'] and Z.get(v['file'],0)<15e6: useA(v['file'])
        vids.append(dict(position=v['position'],url=v['url'],file=v['file'],duration=v['duration'],transcript=v['transcript'],watched=v['watched']))
    final=next((m['text'] for m in reversed(msgs) if m['role']=='agent' and m['text'].strip()),'')
    adn=None
    if ad and Z.get(ad['file'],0)>15.7e6: adn='This ad was 17.8 MB, over the page\'s 15 MB file limit, so this is a re-encoded copy. The original is in the app.'
    if ad: useA(ad['file'])
    ADS[k]=dict(cost=a['cost'],url=a['url'],trace=LANG+a['session'],session=a['session'],app=None,final=final,ad=ad['file'] if ad else None,adNote=adn,reds=[dict(kind=w.get('kind','?'),text=w.get('text','')) for w in reds],gaveup=gav,watch=watch,answers=ans,scenes=scs,stats=st,r7=r7,shared=shared,library=lib,videos=vids,costs=cost_sorted(a['costs']),warnings=[w for w in wall if not w['text'].startswith(('Making ','Remaking '))],chat=[dict(role=m['role'],text=m['text']) for m in msgs],
      producer=[dict(tool=t['tool'],head=t['fold'][:120] if t['fold'] else '',cost=t['cost'],said=t['said'],why=t['why'],args=t['args'],result=(t['result'] or '')[:8000],bad=t['answer']=='cant',pics=[]) for t in a['producer']],product=a['product'],colour=a['product_colour'],size=a['product_size'],behaviour=a['behaviour'],pageText=a['page_text'])
FIRST.sort(key=lambda x:x['o'])
for f in FIRST: f.pop('o')
media={}
for k in need['files']: media[k]=('m2/'+name(k)[:-4]+'.mp4') if k.endswith('.m4a') else 'm/'+name(k)
for k in need['assets']:
    if k in mapp and k not in need['files']: media[k]=mapp[k]
for r in ADS.values():
    if r['r7']:
        pass
lead="Every ad, every scene and every try, with what was sent to the image model and, separately, to the video model. Your words and taps save as you type. Summaries are made only from the app's records and marked as such; nothing here is a grade from me."
# r7 video urls are direct: expose them through media with their own url as key
for a in ADS.values():
    r=a['r7']
    if r:
        if r['ad']: media[r['ad']]=r['ad']
        for s in r['scenes']:
            for c in s['clips']:
                if c['file']: media[c['file']]=c['file']
out=dict(media=media,ads=ADS,first=FIRST,answers=ANS,lead=lead,total=sum(a['cost'] for a in ADS.values()))
json.dump(dict(assets=sorted(set(need['assets'])-set(need['files'])),files=sorted(need['files'])),open(S+'/need.json','w'))
js=open(S+'/page.js').read(); css=open(S+'/page.css').read()
blob=json.dumps(out,ensure_ascii=False).replace('</','<\\/')
html=f'''<title>Round 8 Grading</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600;700&display=swap">
<style>{css}</style>
<div class="shell"><nav class="rail" id="rail" aria-label="Ads"></nav><main id="view"></main></div>
<script type="application/json" id="data">{blob}</script>
<script>{js}</script>
'''
open(S+'/grading.html','w').write(html)
if len(sys.argv)>1:
    o2=dict(out);o2['media']={**{k:'media/'+name(k) for k in list(need['files'])+list(need['assets'])},**{k:v for k,v in media.items() if k.startswith('/_blob')}}
    b2=json.dumps(o2,ensure_ascii=False).replace('</','<\\/')
    open(S+'/test.html','w').write('<!doctype html><meta charset=utf8><meta name=viewport content="width=device-width,initial-scale=1">'+html.replace(blob,b2))
print('page MB',len(html.encode())/1e6,'assets',len(need['assets']),'files',len(need['files']),'hosted',len(media),'first',len(FIRST),'answers',len(ANS))
