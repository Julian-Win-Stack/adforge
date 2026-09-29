import json,sys
d=json.load(open('/tmp/st/labels100.json'))
k=sys.argv[1]
print("PRODUCT:",d[k]['product'])
for l in d[k]['labels']:
    print(f"{l['n']} [{'P' if l['label']=='product_info' else 'N'}] {l['text']}")
