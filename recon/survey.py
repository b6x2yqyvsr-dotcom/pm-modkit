import zipfile, io, collections, sys, json
import UnityPy
ZIP='/Users/admin/Downloads/口蘑数据包(1).zip'
z=zipfile.ZipFile(ZIP)
names=sorted(n for n in z.namelist() if n.endswith('/__data'))
tot=collections.Counter(); per={}
for i,n in enumerate(names):
    bundle=n.split('/')[3]
    d=z.read(n)
    try:
        env=UnityPy.load(io.BytesIO(d))
        c=collections.Counter(o.type.name for o in env.objects)
        per[bundle]=dict(c); tot.update(c)
    except Exception as e:
        per[bundle]={'ERR':f'{type(e).__name__}: {e}'}
    if (i+1)%25==0: print('...',i+1,'/',len(names), flush=True)
print('TOTAL TYPES:', json.dumps(dict(tot.most_common()), indent=1))
json.dump(per, open('recon/bundle_types.json','w'), indent=1, ensure_ascii=False)
print('bundles scanned:', len(per))
