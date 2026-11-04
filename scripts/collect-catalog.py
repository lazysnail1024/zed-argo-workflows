#!/usr/bin/env python3
"""Collect official upstream bytes; never equates source collection with validation."""
import concurrent.futures,datetime,hashlib,json,pathlib,re,subprocess,urllib.request,time
root=pathlib.Path(__file__).resolve().parents[1]; provenance=root/'compat/upstream'; provenance.mkdir(parents=True,exist_ok=True)
cutoff='2026-11-02T23:59:59Z'
headers={'User-Agent':'zed-argo-workflows-local-development'}
def get(url):
 for attempt in range(4):
  try:
   with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=60) as r:return r.read()
  except Exception:
   if attempt==3:raise
   time.sleep(attempt+1)
releases=[];page=1
while True:
 url=f'https://api.github.com/repos/argoproj/argo-workflows/releases?per_page=100&page={page}'
 local=pathlib.Path(f'/tmp/wf-releases-{page}.json'); raw=local.read_bytes() if local.exists() else get(url)
 (provenance/f'releases-{page}.json').write_bytes(raw);items=json.loads(raw);releases.extend(items)
 if len(items)<100:break
 page+=1
raw=pathlib.Path('/tmp/wf-tags.txt').read_bytes();(provenance/'tags.txt').write_bytes(raw)
tags={}
for line in raw.decode().splitlines():
 commit,ref=line.split();name=ref.removeprefix('refs/tags/');tags[name]=commit
selected=[r for r in releases if re.fullmatch(r'v3\.\d+\.\d+',r['tag_name']) and not r['draft'] and not r['prerelease'] and r['published_at']<=cutoff]
selected.sort(key=lambda r:tuple(map(int,r['tag_name'][1:].split('.'))))
kinds={'Workflow':'workflows','WorkflowTemplate':'workflowtemplates','ClusterWorkflowTemplate':'clusterworkflowtemplates','CronWorkflow':'cronworkflows'}
blobdir=root/'schemas/raw';blobdir.mkdir(parents=True,exist_ok=True)
def collect(r):
 tag=r['tag_name'];commit=tags.get(tag+'^{}',tags.get(tag));assert commit,tag
 sources=[]
 for kind,name in kinds.items():
  path=f'manifests/base/crds/full/argoproj.io_{name}.yaml';url=f'https://raw.githubusercontent.com/argoproj/argo-workflows/{commit}/{path}'
  try:raw=get(url)
  except Exception:
   path=f'manifests/base/crds/argoproj.io_{name}.yaml';url=f'https://raw.githubusercontent.com/argoproj/argo-workflows/{commit}/{path}';raw=get(url)
  digest=hashlib.sha256(raw).hexdigest();local=blobdir/(digest+'.yaml');local.write_bytes(raw)
  sources.append({'kind':kind,'upstreamPath':path,'url':url,'path':str(local.relative_to(root)),'sha256':digest})
 print(tag,flush=True)
 return {'version':tag[1:],'tag':tag,'upstreamCommit':commit,'publishedAt':r['published_at'],'releaseUrl':r['html_url'],'sources':sources,'schemaSha256':None,'ruleset':None,'verified':False}
entries=[];errors=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
 futures={executor.submit(collect,r):r['tag_name'] for r in selected}
 for f in concurrent.futures.as_completed(futures):
  try:entries.append(f.result())
  except Exception as e:errors.append({'tag':futures[f],'error':str(e)})
entries.sort(key=lambda v:tuple(map(int,v['version'].split('.'))))
lock={'product':'argo-workflows','policy':'all stable 3.x releases published by cutoff','cutoff':cutoff,'sourceReleaseCount':len(selected),'collectionComplete':len(entries)==len(selected),'analysisReady':False,'defaultVersion':None,'license':{'upstream':'Apache-2.0','url':'https://github.com/argoproj/argo-workflows/blob/main/LICENSE'},'versions':entries,'errors':errors}
(root/'compat/catalog.lock.json').write_text(json.dumps(lock,indent=2)+'\n')
print(json.dumps({'collected':len(entries),'expected':len(selected),'errors':errors}),flush=True)
