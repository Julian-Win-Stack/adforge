import json, os
from decimal import Decimal
from collections import Counter, defaultdict
from django.db.models import Sum
from chat.models import Session, Message
from jobs.models import *
from jobs.stale import current_clip
from agents.models import ToolCall
from gateway.models import ModelCall
from evals.tool_calls import said_before

MEDIA='/app/media'
# The image model calls that draw a scene's starting and end pictures.
IMAGE_PURPOSES=['draw_scene_picture','make_end_picture']
files=set()
def f(key):
    key=str(key or '')
    if key: files.add(key)
    return key or None
def dump(x): return json.loads(json.dumps(x,default=str))

def call_rec(t,said):
    mcs=list(t.model_calls.all())
    for p in t.pictures or []: f(p.get('key'))
    return dict(id=t.pk,agent=t.agent,tool=t.tool,args=dump(t.arguments),why=t.why,said=said.get(t.call_id,''),result=t.result,answer=t.answer,fold=t.fold_line,
      pictures=[dict(label=p.get('label'),key=p.get('key')) for p in (t.pictures or [])],
      cost=float(sum((m.cost_usd or Decimal(0)) for m in mcs)),at=t.created_at.isoformat(),done=t.finished_at.isoformat() if t.finished_at else None,
      calls=[dict(p=m.purpose,model=m.model,outcome=m.outcome,cost=float(m.cost_usd or 0),error=str(m.error or '')[:400]) for m in mcs])

def clip_rec(p,said,making,born):
    # `born`: when this clip's file first appeared. A try brought back later is a new row
    # with a new time, and must still match the H3 call that made it.
    sent=p.sent or {}
    for x in sent.get('pictures') or []: f(x.get('file'))
    for x in sent.get('pieces') or []: f(x.get('file'))
    f(p.file)
    r=p.ruling or {}
    # The video model's own record: its handoff is exactly what H3 was sent.
    mk=None
    prompt=sent.get('prompt')
    for m in making:
        h=m.handoff or {}
        if m.created_at<=born and ((prompt and h.get('prompt')==prompt) or (not prompt and m.step_id_==p.step_id)):
            if mk and mk['outcome']=='succeeded' and m.outcome!='succeeded': continue
            mk=dict(purpose=m.purpose,model=m.model,provider=m.provider,cost=float(m.cost_usd or 0),outcome=m.outcome,handoff=dump(h))
    if mk:
        h=mk['handoff']
        for k in [h.get('starting_picture'),h.get('end_picture'),*(h.get('example_pictures') or [])]: f(k)
        for v in h.get('reference_videos') or []: f(v.get('file'))
    at=None
    try: at=p.agent_try
    except Exception: pass
    tr=None
    if at:
        tr=dict(number=at.number,why=at.why,root_cause=at.root_cause,move=at.move,would_ask=at.would_ask_owner,picture_problems=dump(at.picture_problems),fix_check=dump(at.fix_check),
                new_picture=bool(at.new_picture),picture_prompt=at.picture_prompt,video_prompt=at.video_prompt,tool_call=at.tool_call_id,fix=at.agent_fix_id,passed=at.passed,
                row=dump(at.scene_row))
    pic=None
    if p.picture_id: pic=dict(pk=p.picture_id,file=f(p.picture.file))
    st=p.step
    return dict(pk=p.pk,version=p.version,remake=p.remake,file=str(p.file),seconds=p.seconds,change=p.change,motion_prompt=p.motion_prompt,picture=pic,
      made_from=p.made_from.version if p.made_from_id else None,created=p.created_at.isoformat(),
      step=dict(pk=st.pk,kind=st.kind,way=st.way,status=st.status,prompt=st.prompt,prompt_reason=st.prompt_reason,reason=st.reason,note=st.note,owner_note=st.owner_note,fix_goal=st.fix_goal) if st else None,
      sent=dump(sent),ruling=dump(r),make=mk,fix_try=tr,text=p.text)

def one(key,prefix):
    s=Session.objects.filter(name=f'{prefix} {key}').order_by('-created_at').first()
    if not s: return None
    j=Job.objects.filter(session=s).order_by('-created_at').first()
    said=said_before(s)
    calls=list(ToolCall.objects.filter(session=s).exclude(tool='take_turn').select_related('agent_fix__step__scene').prefetch_related('model_calls').order_by('created_at','id'))
    making=list(ModelCall.objects.filter(session=s,purpose__in=['make_broll_clip','make_talking_clip']).select_related('tool_call').order_by('created_at'))
    for m in making: m.step_id_=None
    # The image model's own records, by the picture each made: exactly what it was sent.
    image_calls={}
    for m in ModelCall.objects.filter(session=s,purpose__in=IMAGE_PURPOSES).order_by('created_at','id'):
        h=m.handoff or {}
        out=str((m.output or {}).get('file') or '')
        if not out: continue
        for k in h.get('pictures') or []: f(k)
        image_calls[out]=dict(purpose=m.purpose,model=m.model,provider=m.provider,cost=float(m.cost_usd or 0),prompt=h.get('prompt'),pictures=list(h.get('pictures') or []))
    scenes=[]
    fixes=defaultdict(list)
    for a in AgentFix.objects.filter(step__scene__job=j).select_related('step__scene').prefetch_related('scene_runs').order_by('created_at'): fixes[a.step.scene_id].append(a)
    for sc in Scene.objects.filter(job=j).order_by('number'):
        try: cur=current_clip(sc)
        except Exception as e: cur=None
        rows=list(ProducedItem.objects.filter(job=j,scene=sc,kind='clip').select_related('step','picture','made_from').order_by('created_at','id'))
        born={}
        for p in rows: born.setdefault(str(p.file),p.created_at)
        clips=[clip_rec(p,said,making,born[str(p.file)]) for p in rows]
        pics=[dict(pk=p.pk,kind=p.kind,version=p.version,file=f(p.file),by=p.step.kind if p.step_id else None,change=p.change,created=p.created_at.isoformat(),
                   made_from=dict(pk=p.made_from_id,version=p.made_from.version,file=str(p.made_from.file)) if p.made_from_id else None,
                   photos=[dict(file=str(x.get('file') or ''),line=x.get('line'),lib=x.get('library_number')) for x in (p.sent or {}).get('photos') or []],
                   edited_from=(p.sent or {}).get('edited_from'),image=image_calls.get(str(p.file)))
              for p in ProducedItem.objects.filter(job=j,scene=sc,kind__in=['starting_picture','end_picture']).select_related('step','made_from').order_by('created_at','id')]
        lines=[dict(version=p.version,seconds=p.seconds,text=p.text) for p in ProducedItem.objects.filter(job=j,scene=sc,kind='line_audio').order_by('created_at')]
        tr=[dict(version=p.version,text=p.text,words=None) for p in ProducedItem.objects.filter(job=j,scene=sc,kind='transcript').order_by('created_at')]
        fx=[dict(id=a.pk,is_scene_agent=a.is_scene_agent,door=a.door,note=a.note,goal=a.goal,gave_up=a.gave_up,ended_by_cap=a.ended_by_cap,passed=a.passed_id,kept=a.kept_id,picked=a.picked,said_to_camera=a.said_to_camera,
                 runs=[dict(number=r.number,instruction=r.instruction,quotes=dump(r.quotes),pictures=r.pictures,clips=r.clips,asked=r.asked,reason=r.reason) for r in a.scene_runs.all()]) for a in fixes[sc.pk]]
        ids={a['id'] for a in fx}
        ag=[call_rec(t,said) for t in calls if t.agent_fix_id in ids]
        qs=[dict(text=q.text if hasattr(q,'text') else '',wants=q.wants,answer=str(getattr(q,'answer','') or ''),) for q in OwnerQuestion.objects.filter(scene=sc)]
        scenes.append(dict(number=sc.number,part=sc.part,line=sc.line,shows=sc.shows,kind=getattr(sc,'kind',None) or ('broll' if sc.shows else 'talking'),kind_why=getattr(sc,'kind_why',''),overlay=sc.overlay,broll_kind=sc.broll_kind,person_shown=sc.person_shown,usage=sc.usage,needs=dump(sc.needs),status=sc.status,removed=str(sc.removed_at or ''),
           current=cur.pk if cur else None,clips=clips,pictures=pics,lines=lines,transcripts=tr,fixes=fx,agent=ag,questions=qs,scene_pictures=dump(sc.scene_pictures),piece=dump(sc.piece),place=dump(sc.place),fact_problems=dump(sc.fact_problems)))
    # the things shared across the ad
    shared=[dict(kind=p.kind,version=p.version,file=f(p.file) if p.kind!='voice' else str(p.file),seconds=p.seconds) for p in ProducedItem.objects.filter(job=j).filter(scene__isnull=True).order_by('created_at')]
    lib=[dict(number=l.number,file=f(l.file),kind=l.kind,side=l.side,alone=l.alone,colour=l.colour,target=l.target,label=l.label,source=dump(l.source),cut_out=dump(l.cut_out)) for l in LibraryItem.objects.filter(job=j).order_by('number')]
    vids=[dict(position=v.position,url=v.source_url,kind=v.kind,file=f(v.file),duration=v.duration,transcript=v.transcript,watched=dump(v.watched),thrown_out=dump(v.thrown_out)) for v in ProductVideo.objects.filter(job=j).order_by('position')]
    msgs=[dict(seq=m.seq,role=m.role,level=m.level,text=m.text,q=dump(m.owner_question) if m.owner_question_id else None,at=m.created_at.isoformat()) for m in Message.objects.filter(session=s).select_related('owner_question').order_by('seq') if m.text] if False else [dict(seq=m.seq,role=m.role,level=m.level,text=m.text,at=m.created_at.isoformat()) for m in Message.objects.filter(session=s).order_by('seq') if m.text]
    costs={r['purpose']:float(r['c'] or 0) for r in ModelCall.objects.filter(session=s).values('purpose').annotate(c=Sum('cost_usd'))}
    return dict(key=key,session=str(s.pk),name=s.name,created=s.created_at.isoformat(),url=j.product_url,job=j.pk,product=j.product_name,status=j.status,colours=dump(j.colours),product_colour=j.product_colour,product_size=j.product_size,
       behaviour=dump(j.product_behaviour),script_format=str(j.script_format),target_seconds=j.target_seconds,person=dict(gender=j.person_gender,looks=j.person_looks,voice=str(j.person_voice)),page_text=(j.page_text or '')[:6000],warnings=dump(j.warnings),
       cost=sum(costs.values()),costs=costs,trace=None,messages=msgs,scenes=scenes,shared=shared,library=lib,videos=vids,producer=[call_rec(t,said) for t in calls if t.agent!='scene_agent' ])

ads=json.load(open('/tmp/r8-ads.json'))
out={}
for k in ads: out[k]=one(k,'R8')
r7={}
for k in ads:
    try: r7[k]=one(k+' (picker fix)','R7') if k.startswith('n04') else one(k,'R7')
    except Exception as e: r7[k]=dict(error=repr(e)); print('R7ERR',k,repr(e))
json.dump(dict(r8=out,r7=r7),open('/tmp/r8-data.json','w'),ensure_ascii=False,default=str)
keep=set()
sizes={}
for k in files:
    p=os.path.join(MEDIA,k)
    sizes[k]=os.path.getsize(p) if os.path.exists(p) else -1
json.dump(sizes,open('/tmp/r8-files.json','w'))
print('OK',len(out),sum(1 for v in r7.values() if v and 'error' not in v),len(files),sum(v for v in sizes.values() if v>0)/1e6,'missing',sum(v<0 for v in sizes.values()))
