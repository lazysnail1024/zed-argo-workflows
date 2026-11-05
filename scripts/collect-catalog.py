#!/usr/bin/env python3
"""Collect official upstream bytes; never equates source collection with validation."""
import concurrent.futures,datetime,hashlib,json,pathlib,re,subprocess,urllib.request,urllib.error,time
root=pathlib.Path(__file__).resolve().parents[1]; provenance=root/'compat/upstream'; provenance.mkdir(parents=True,exist_ok=True)
cutoff='2026-11-05T13:59:59Z'
headers={'User-Agent':'zed-argo-workflows-local-development'}
def get(url):
 for attempt in range(4):
  try:
   with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=60) as r:return r.read()
  except urllib.error.HTTPError as error:
   if error.code==404 or attempt==3:raise
   time.sleep(attempt+1)
  except Exception:
   if attempt==3:raise
   time.sleep(attempt+1)
releases=[];page=1
while True:
 url=f'https://api.github.com/repos/argoproj/argo-workflows/releases?per_page=100&page={page}'
 raw=get(url)
 (provenance/f'releases-{page}.json').write_bytes(raw);items=json.loads(raw);releases.extend(items)
 if len(items)<100:break
 page+=1
raw=subprocess.check_output(['git','ls-remote','--tags','https://github.com/argoproj/argo-workflows.git']);(provenance/'tags.txt').write_bytes(raw)
tags={}
for line in raw.decode().splitlines():
 commit,ref=line.split();name=ref.removeprefix('refs/tags/');tags[name]=commit
# Numeric final-version tags are authoritative even if an old GitHub release was
# marked prerelease or never created. Exclude rc/beta/alpha suffixes, not patches.
release_by_tag={r['tag_name']:r for r in releases if not r['draft']}
final_tags=[tag for tag in tags if re.fullmatch(r'v\d+\.\d+\.\d+',tag)]
latest=max(tuple(map(int,tag[1:].split('.'))) for tag in final_tags)
majors=[latest[0]-1,latest[0]]
selected=[]
for tag in final_tags:
 if int(tag[1:].split('.')[0]) not in majors:continue
 release=release_by_tag.get(tag,{})
 selected.append({'tag_name':tag,'published_at':release.get('published_at'),'html_url':release.get('html_url','https://github.com/argoproj/argo-workflows/tree/'+tag),'release_marked_prerelease':release.get('prerelease',False),'publication_source':'release' if release else 'tag'})
previous=json.loads((root/'compat/catalog.lock.json').read_text()) if (root/'compat/catalog.lock.json').exists() else {'versions':[]}
existing={v['version']:v for v in previous['versions']}
selected.sort(key=lambda r:tuple(map(int,r['tag_name'][1:].split('.'))))
kinds={'Workflow':'workflows','WorkflowTemplate':'workflowtemplates','ClusterWorkflowTemplate':'clusterworkflowtemplates','CronWorkflow':'cronworkflows','WorkflowEventBinding':'workfloweventbindings','WorkflowArtifactGCTask':'workflowartifactgctasks','WorkflowTaskResult':'workflowtaskresults','WorkflowTaskSet':'workflowtasksets'}
required_kinds={'Workflow','WorkflowTemplate','ClusterWorkflowTemplate','CronWorkflow','WorkflowEventBinding'}
blobdir=root/'schemas/raw';blobdir.mkdir(parents=True,exist_ok=True)
def collect(r):
 tag=r['tag_name'];commit=tags.get(tag+'^{}',tags.get(tag));assert commit,tag
 cached=existing.get(tag[1:])
 sources=[];absent=[]
 if cached and cached['upstreamCommit']!=commit:cached=None
 cached_sources={s['kind']:s for s in cached['sources']} if cached else {}
 cached_absent={s['kind']:s for s in cached.get('absentSources',[])} if cached else {}
 for kind,name in kinds.items():
  saved=cached_sources.get(kind)
  if saved and (root/saved['path']).is_file() and hashlib.sha256((root/saved['path']).read_bytes()).hexdigest()==saved['sha256']:
   sources.append(saved);continue
  paths=[f'manifests/base/crds/full/argoproj.io_{name}.yaml',f'manifests/base/crds/argoproj.io_{name}.yaml']
  missing={'kind':kind,'checks':[{'upstreamPath':path,'url':f'https://raw.githubusercontent.com/argoproj/argo-workflows/{commit}/{path}','status':404} for path in paths]}
  if kind not in required_kinds and cached_absent.get(kind)==missing:
   absent.append(missing);continue
  raw=None
  for path in paths:
   url=f'https://raw.githubusercontent.com/argoproj/argo-workflows/{commit}/{path}'
   try:
    raw=get(url);break
   except urllib.error.HTTPError as error:
    if error.code!=404:raise
  if raw is None:
   if kind in required_kinds:raise ValueError(f'missing required CRD {kind} at {commit}')
   absent.append(missing);continue
  digest=hashlib.sha256(raw).hexdigest();local=blobdir/(digest+'.yaml');local.write_bytes(raw)
  sources.append({'kind':kind,'upstreamPath':path,'url':url,'path':str(local.relative_to(root)),'sha256':digest})
 print(tag,flush=True)
 return {'version':tag[1:],'tag':tag,'upstreamCommit':commit,'publishedAt':r['published_at'],'releaseUrl':r['html_url'],'publicationSource':r['publication_source'],'releaseMarkedPrerelease':r['release_marked_prerelease'],'sources':sources,'absentSources':absent,'schemaSha256':None,'ruleset':None,'verified':False}
entries=[];errors=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
 futures={executor.submit(collect,r):r['tag_name'] for r in selected}
 for f in concurrent.futures.as_completed(futures):
  try:entries.append(f.result())
  except Exception as e:errors.append({'tag':futures[f],'error':str(e)})
entries.sort(key=lambda v:tuple(map(int,v['version'].split('.'))))
lock={'product':'argo-workflows','policy':'all official final-version tags in the latest two major versions observed at the source snapshot; prerelease suffixes excluded','supportedMajors':majors,'supportedKinds':list(kinds),'latestVersion':'.'.join(map(str,latest)),'cutoff':cutoff,'sourceVersionCount':len(selected),'sourceReleaseCount':sum(r['publication_source']=='release' for r in selected),'collectionComplete':len(entries)==len(selected),'analysisReady':False,'defaultVersion':None,'license':{'upstream':'Apache-2.0','url':'https://github.com/argoproj/argo-workflows/blob/main/LICENSE'},'versions':entries,'errors':errors}
(root/'compat/catalog.lock.json').write_text(json.dumps(lock,indent=2)+'\n')
print(json.dumps({'collected':len(entries),'expected':len(selected),'errors':errors}),flush=True)
