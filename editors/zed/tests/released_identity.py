#!/usr/bin/env python3
"""Resolve a completed trusted adapter publication to its exact immutable release."""
import json, os, pathlib, re, subprocess, tomllib, urllib.parse, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[3]
repo=os.environ['GITHUB_REPOSITORY'];event=json.loads(pathlib.Path(os.environ['GITHUB_EVENT_PATH']).read_text());run=event['workflow_run']
def require(value, message):
 if not value:raise ValueError(message)
def api(path):
 request=urllib.request.Request('https://api.github.com/repos/'+repo+path,headers={'Accept':'application/vnd.github+json','Authorization':'Bearer '+os.environ['GH_TOKEN']})
 with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
require(run.get('repository',{}).get('full_name')==repo and run.get('head_repository',{}).get('full_name')==repo,'foreign release source')
actual=api('/actions/runs/'+str(int(run['id'])));workflow=api('/actions/workflows/zed-release.yml')
require(actual['workflow_id']==workflow['id'] and actual.get('path')=='.github/workflows/zed-release.yml','wrong release workflow')
require(actual.get('conclusion')=='success' and actual.get('status')=='completed','upstream did not succeed')
require(actual.get('event') in ('push','workflow_dispatch'),'untrusted release event')
require(actual.get('run_attempt')==run.get('run_attempt') and actual.get('head_sha')==run.get('head_sha'),'release attempt/source mismatch')
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();require(commit==actual['head_sha'],'checkout differs from release source')
version=tomllib.loads((ROOT/'editors/zed/extension.toml').read_text())['version'];tag='zed-v'+version
obj=api('/git/ref/tags/'+urllib.parse.quote(tag,safe=''))['object']
for _ in range(16):
 if obj['type']!='tag':break
 obj=api('/git/tags/'+obj['sha'])['object']
require(obj['type']=='commit' and obj['sha']==commit,'tag source mismatch')
release=api('/releases/tags/'+urllib.parse.quote(tag,safe=''));require(release.get('tag_name')==tag and release.get('draft') is False and release.get('published_at'),'release not published')
required={repo.split('/')[-1]+'-'+version+'.zip','SHA256SUMS','zed-adapter.json'}
for name in required:
 matches=[a for a in release['assets'] if a['name']==name]
 require(len(matches)==1 and matches[0]['state']=='uploaded' and matches[0]['size']>0,'missing or duplicate release asset: '+name)
output=pathlib.Path('artifacts/released-install');output.mkdir(parents=True,exist_ok=True)
(output/'release-identity.json').write_text(json.dumps({'schemaVersion':1,'repository':repo,'sourceCommit':commit,'version':version,'releaseID':release['id'],'runID':actual['id'],'attempt':actual['run_attempt'],'assets':release['assets']},indent=2))
with open(os.environ['GITHUB_OUTPUT'],'a') as stream:stream.write('version='+version+'\nsource='+commit+'\n')
