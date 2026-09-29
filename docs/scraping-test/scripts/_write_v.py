import json, os
S='/tmp/st/sheets/100'; V='/tmp/st/verdicts/100'
over = {
 'amazon-02-maybelline-sky-high-mascara': {
  'K1':('wrongly_kept','brand lifestyle banner, no product'),
  'K4':('wrongly_kept','customer selfie, no mascara visible'),
  'K7':('wrongly_kept','face selfie, no product'),
  'K15':('wrongly_kept','plain selfie, no product'),
  'K22':('wrongly_kept','phone screenshot selfie with UI, no product'),
  'K25':('wrongly_kept','Lash Sensational Body mascara, different product'),
  'K26':('wrongly_kept','Lash Sensational Firework, different product'),
  'K30':('wrongly_kept','face close-up, no product'),
  'D8':('wrongly_dropped','Sky High before/after in Cosmic Black and True Brown shades'),
  'D22':('wrongly_dropped','Sky High in three new shades, same product other colours'),
  'D30':('wrongly_dropped','bottoms of Sky High tubes (Very Black, Burgundy Haze), same product other shades'),
 },
 'amazon-03-nature-made-vitamin-d3': {
  'K1':('wrongly_kept','text-only claim graphic'),
  'K2':('wrongly_kept','assorted pills, not identifiable as product'),
  'K4':('wrongly_kept','Vitamin D+K graphic, different product'),
  'K11':('wrongly_kept','text-only claim graphic'),
  'K12':('wrongly_kept','text/icon graphic, no product'),
  'K13':('wrongly_kept','stock lifestyle photo, no product'),
  'K18':('wrongly_kept','text/icon graphic, no product'),
  'K20':('wrongly_kept','text/icon graphic, no product'),
  'K21':('wrongly_kept','brand banner, no product'),
  'K23':('wrongly_kept','brand banner, no product'),
  'K24':('wrongly_kept','D3 tablets 400 ct, different format'),
 },
 'amazon-04-optimum-nutrition-gold-standard-whey': {
  'K5':('wrongly_kept','lifestyle banner, no product'),
  'K7':('wrongly_kept','lifestyle banner, no product'),
  'K8':('wrongly_kept','lifestyle banner, no product'),
  'K11':('wrongly_kept','tennis lifestyle with logo, no product'),
  'K12':('wrongly_kept','lifestyle banner, no product'),
  'K15':('wrongly_kept','strawberries claim graphic, no product'),
  'K16':('wrongly_kept','Vanilla flavour, sibling flavour'),
 },
 'retail-29-iherb-cgn-omega-3': {},
 'amazon-05-apple-airpods-pro-2': {
  'K1':('wrongly_kept','phone hearing-test screen, no AirPods'),
  'K5':('wrongly_kept','icon-only spec table, no product image'),
 },
 'amazon-08-lodge-cast-iron-skillet': {
  'K1':('wrongly_kept','round griddle/pizza pan with loop handles, different product'),
  'K4':('wrongly_kept','smooth unbranded pan, different product'),
  'K6':('wrongly_kept',"Lodge Baker's Skillet, different product"),
  'K16':('wrongly_kept','brand banner of assorted cookware'),
  'K22':('wrongly_kept','person in apron, no skillet'),
  'K28':('wrongly_kept','square grill pan, different product'),
  'K29':('wrongly_kept','grill/griddle with accessories, different product'),
  'K37':('wrongly_kept','3-piece skillet set, different listing'),
  'K43':('wrongly_kept','lifestyle banner, no skillet'),
 },
 'amazon-06-fire-tv-stick-4k-plus': {
  'K9':('wrongly_kept','4K vs HD graphic, no product'),
  'K32':('wrongly_kept','living-room lifestyle, device not visible'),
 },
 'amazon-07-stanley-quencher-40oz': {
  'K4':('wrongly_kept','replacement straw set listing'),
  'K8':('wrongly_kept','replacement lid and straw only'),
 },
 'amazon-09-greenies-dental-treats': {
  'K1':('wrongly_kept','dog with toy, no product'),
  'K7':('wrongly_kept','dog only, no product'),
  'K9':('wrongly_kept','text-only brand story graphic'),
  'K15':('wrongly_kept','dogs in car, no product'),
  'K17':('wrongly_kept','dog photo, no product'),
  'K18':('wrongly_kept','dog video thumbnail, no product'),
  'K20':('wrongly_kept','dog in grass, no product'),
  'K21':('wrongly_kept','slogan banner with dog, no product'),
  'D2':('wrongly_dropped','Greenies Original Petite 60 ct, same product other size'),
 },
 'other-01-mahalo-rare-indigo-balm': {
  'K1':('wrongly_kept','pump bottle serum, different product'),
  'K5':('wrongly_kept','Mahalo Balm and Hawaiian Nights serum, different products'),
  'K8':('wrongly_kept','green balm jar (The Unveil), different product'),
  'K11':('wrongly_kept','illustration, no product'),
  'K16':('wrongly_kept','The Unveil cleanser jar, different product'),
  'K17':('wrongly_kept','lifestyle with unidentifiable jar'),
 },
}
tot={'correct':0,'wrongly_kept':0,'wrongly_dropped':0}
for key,ov in over.items():
    d=json.load(open(f'{S}/{key}.json'))
    ids=[it['id'] for it in d['items']]
    for k in ov: assert k in ids,(key,k)
    verd={}
    for i in ids:
        v,n = ov.get(i,('correct',''))
        verd[i]={'verdict':v,'note':n}; tot[v]+=1
    json.dump({'key':key,'verdicts':verd},open(f'{V}/{key}.json','w'),indent=1)
    c=sum(1 for x in verd.values() if x['verdict']=='correct')
    print(key,len(ids),'correct',c,'wk',sum(1 for x in verd.values() if x['verdict']=='wrongly_kept'),'wd',sum(1 for x in verd.values() if x['verdict']=='wrongly_dropped'))
print(tot)
