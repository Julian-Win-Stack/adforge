import json,sys
d=json.load(open('/tmp/st/labels100.json'))
k=sys.argv[1]; wrongs=json.loads(sys.argv[2])  # {"n":"why"}
ns=[str(l['n']) for l in d[k]['labels']]
checks={n:("wrong" if n in wrongs else "right") for n in ns}
bad=set(wrongs)-set(ns)
assert not bad, bad
out={"key":k,"checks":checks}
if wrongs: out["notes"]=wrongs
json.dump(out,open(f'/tmp/st/label-checks/100/{k}.json','w'),indent=1)
print(k,"written; wrongs:",len(wrongs),"of",len(ns))
