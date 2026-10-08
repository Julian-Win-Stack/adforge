"""Score a structured source (Firecrawl product format or Shopify .js) against photo and text truth."""
import json,re,sys,glob,os,collections,urllib.parse
sys.path.insert(0,'/Users/phyonyanwinn/Project/ProJect/adforge/docs/scraping-test/scripts')
from missing import normal
src=sys.argv[1]  # 'product' or 'shopjs'
ptruth=json.load(open('/Users/phyonyanwinn/Project/ProJect/adforge/docs/scraping-test/fix-test/outputs/photo_truth.json'))
ttruth=json.load(open('/Users/phyonyanwinn/Project/ProJect/adforge/docs/scraping-test/fix-test/outputs/text_truth.json'))
cls=json.load(open('/Users/phyonyanwinn/Project/ProJect/adforge/docs/scraping-test/fix-test/outputs/old-wrong-216-classified.json'))
wrong=[r for r in ttruth if r['old']=='product_info' and r['truth']=='noise']
assert len(wrong)==216
cat={id(ttruth[c['id']]):c['category'] for c in cls}
assert all(ttruth[c['id']]['old']=='product_info' and ttruth[c['id']]['truth']=='noise' for c in cls)
def base(u):
    p=urllib.parse.urlparse(u).path.rsplit('/',1)[-1].lower()
    p=re.sub(r'\._[a-z0-9_,]+_(?=\.)','',p)        # amazon ._AC_SL1500_.
    p=re.sub(r'_(\d+x\d*|\d*x\d+)(?=\.)','',p)     # shopify _1024x1024.
    p=re.sub(r'\.(jpe?g|png|webp|gif|avif)$','',p)
    return p
def load(k):
    f=f'/tmp/st/v2/{src}/{k}.json'
    if not os.path.exists(f): return None
    d=json.load(open(f))
    if src=='product':
        p=(d.get('data') or {}).get('product')
        if not p: return None
        imgs=[i['url'] for v in p.get('variants',[]) for i in v.get('images',[]) if i.get('url')]
        return {'images':imgs,'text':p.get('description') or '','title':p.get('title')}
    if src=='jsonld': return {'images':d['images'],'text':re.sub('<[^>]+>',' ',d['description']),'title':d['title']}
    imgs=d.get('images',[]); 
    return {'images':imgs,'text':re.sub('<[^>]+>',' ',d.get('description') or ''),'title':d.get('title')}
keys=sorted({r['key'] for r in ptruth}|{r['key'] for r in ttruth})
have=[k for k in keys if load(k)]
print(f"{src}: pages with output {len(have)} / {len(keys)}")
P=collections.Counter(); T=collections.Counter(); perpage=[]
for k in have:
    s=load(k); sb={base(u) for u in s['images']}
    rows=[r for r in ptruth if r['key']==k]
    tb={}
    for r in rows: tb.setdefault(base(r['url']),[]).append(r)
    kp=kn=unk=0
    for b in sb:
        if b in tb:
            labels={r['truth'] for r in tb[b]}
            if 'product' in labels: kp+=1
            else: kn+=1
        else: unk+=1
    prod_all={base(r['url']) for r in rows if r['truth']=='product'}
    prod_kept={base(r['url']) for r in rows if r['truth']=='product' and r['stage']=='kept'}
    P['kept product']+=kp; P['kept not']+=kn; P['returned but not in truth']+=unk
    P['truth product total']+=len(prod_all); P['truth product found']+=len(prod_all&sb)
    P['truth product (old kept stage) total']+=len(prod_kept); P['truth product (old kept stage) found']+=len(prod_kept&sb)
    perpage.append((k,len(sb),kp,kn,unk,len(prod_all&sb),len(prod_all)))
    # text
    txt=normal(s['text'])
    for r in ttruth:
        if r['key']!=k: continue
        n=normal(r['text']); hit=n in txt
        if not hit:
            w=re.sub(r'[^\w\s]','',n).split()
            if len(w)>=5: hit=' '.join(w[:5]) in re.sub(r'[^\w\s]','',txt)
        c=cat.get(id(r)) if r in wrong else None
        if r['truth']=='product_info': T['this-product sentences']+=1; T['this-product found']+=hit
        elif c=='other_product': T['other-product sentences']+=1; T['other-product leaked']+=hit
        elif c=='this_product': T['this-product (relabelled) sentences']+=1; T['this-product (relabelled) found']+=hit
        else: T['noise sentences']+=1; T['noise leaked']+=hit
print('PHOTOS',dict(P))
kp,kn=P['kept product'],P['kept not']
print(f"  photo precision (of returned images that are in truth) {kp/max(kp+kn,1):.0%}; recall of truth product photos {P['truth product found']}/{P['truth product total']} = {P['truth product found']/max(P['truth product total'],1):.0%}; of old-kept-stage {P['truth product (old kept stage) found']}/{P['truth product (old kept stage) total']}")
print('TEXT',dict(T))
print('pages with >=1 wrong photo:',sum(1 for p in perpage if p[3]>0),'; pages with 0 photos:',sum(1 for p in perpage if p[1]==0))
print('per page (key, returned, product, not, unknown, recall found/total):')
for p in perpage: print('  ',p)
json.dump(perpage,open(f'/tmp/st/v2/{src}-scored.json','w'))
