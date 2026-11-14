#!/usr/bin/env python3
"""Exercise production plan/gate CLIs with actual isolated Git revisions."""
import argparse,json,os,pathlib,shutil,subprocess,sys,hashlib
ROOT=pathlib.Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);repo=out/'repo';repo.mkdir();rows=[]
def git(*args):return subprocess.check_output(['git',*args],cwd=repo,text=True).strip()
def write(p,s):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
write(repo/'.gitignore','__pycache__/\n')
for f in ['control.py','selection.py']:write(repo/'tests/ci'/f,(ROOT/'tests/ci'/f).read_text())
for f in ['README.md','DEVELOPMENT.md','docs/distribution.md','code.go','tests/test.py','go.mod','editors/zed/snippets/yaml.json','.github/workflows/native.yml']:write(repo/f,'baseline\n')
git('init','-q');git('config','user.name','Selection behavior fixture');git('config','user.email','fixture@example.invalid');git('add','.');git('-c','commit.gpgsign=false','commit','-qm','Baseline');base=git('rev-parse','HEAD')
cases=[('readme',['README.md'],'M','pull_request','docs'),('development',['DEVELOPMENT.md'],'M','pull_request','docs'),('both',['README.md','DEVELOPMENT.md'],'M','pull_request','docs'),('merge-readme',['README.md'],'M','pull_request','docs'),('bad-whitespace',['README.md'],'M','pull_request','docs')]
for name,path in [('distribution','docs/distribution.md'),('code','code.go'),('tests','tests/test.py'),('dependency','go.mod'),('snippet','editors/zed/snippets/yaml.json'),('ci','.github/workflows/native.yml'),('unknown','unknown.txt')]:cases.append((name,[path],'M','pull_request','full'))
cases += [('mixed',['README.md','code.go'],'M','pull_request','full'),('deleted',['README.md'],'D','pull_request','full'),('renamed',['README.md'],'R','pull_request','full'),('empty',[],'M','pull_request','full'),('main',['README.md'],'M','push','full'),('release',['README.md'],'M','release','full'),('dispatch',['README.md'],'M','workflow_dispatch','full'),('missing-base',['README.md'],'M','pull_request','full'),('stale-event-head',['README.md'],'M','pull_request','error')]
def cli(label,args,env):
 cmd=[sys.executable,str(repo/'tests/ci/control.py'),*map(str,args)];r=subprocess.run(cmd,cwd=repo,env=env,capture_output=True);write(out/(label+'.command.json'),json.dumps({'command':cmd,'exitCode':r.returncode}));(out/(label+'.stdout')).write_bytes(r.stdout);(out/(label+'.stderr')).write_bytes(r.stderr);return r.returncode
for name,paths,status,event,expected in cases:
 git('reset','--hard',base)
 for path in paths:
  f=repo/path
  if status=='D':f.unlink()
  elif status=='R':f.rename(f.with_name('renamed.md'))
  else:write(f,'updated documentation\n')
 if name=='bad-whitespace':write(repo/'README.md','invalid trailing spaces  \n')
 git('add','-A');git('-c','commit.gpgsign=false','commit','--allow-empty','-qm',name);head=git('rev-parse','HEAD');event_head=head
 if name=='merge-readme':
  git('checkout','--detach',base);git('-c','commit.gpgsign=false','merge','--no-ff','-m','Synthetic PR merge',head);head=git('rev-parse','HEAD')
 if name=='stale-event-head':
  write(repo/'code.go','unexpected later source\n');git('add','code.go');git('-c','commit.gpgsign=false','commit','-qm','Unexpected later source');head=git('rev-parse','HEAD')
 evt=out/(name+'-event.json');write(evt,json.dumps({'pull_request':{'base':{'sha':'0'*40 if name=='missing-base' else base},'head':{'sha':event_head}}}));env=os.environ|{'GITHUB_EVENT_PATH':str(evt),'GITHUB_EVENT_NAME':event,'GITHUB_SHA':head,'GITHUB_RUN_ID':'selection-e2e','GITHUB_RUN_ATTEMPT':'1'}
 for workflow in ['native','zed','install']:
  label=name+'-'+workflow;plan=out/(label+'-plan.json');code=cli(label,['plan','--workflow',workflow,'--output',plan],env);value=json.loads(plan.read_text());rows.append({'case':label,'exitCode':code,'expectedSelection':expected,'actualSelection':value.get('selection'),'pass':code!=0 if expected=='error' else code==0 and value.get('selection')==expected})
 if name=='bad-whitespace':
  code=cli('bad-docs-check',['docs','--plan',plan,'--output',out/'bad-docs-evidence'],env);rows.append({'case':'bad-documentation-whitespace-refused','pass':code!=0})
  bad=out/'bad-docs-evidence';error_path=bad/'error.json';error=json.loads(error_path.read_text()) if error_path.is_file() else {};report=json.loads((bad/'docs.json').read_text()) if (bad/'docs.json').is_file() else {};stderr=(out/'bad-docs-check.stderr').read_text();raw=(bad/'stdout.log').read_text() if (bad/'stdout.log').is_file() else ''
  rows.append({'case':'bad-documentation-retains-actionable-failure','pass':code==1 and error.get('pass') is False and error.get('error')=='documentation whitespace check failed' and report.get('pass') is False and report.get('exitCode')!=0 and 'README.md:1:' in raw and (bad/'stderr.log').is_file() and 'Traceback' not in stderr and 'documentation whitespace check failed' in stderr})
 # Verify real docs evidence and rejection boundaries on one independently defined docs change.
 if name=='readme' and value.get('selection')=='docs':
  evidence=out/'docs-evidence';started=out/'started.json';cli('start',['start','--output',started],env);docs_code=cli('docs',['docs','--plan',plan,'--output',evidence],env);receipt=out/'receipts/docs.json';receipt_code=cli('receipt',['receipt','--plan',plan,'--lane','docs','--started',started,'--evidence',evidence,'--output',receipt],env);needs=out/'needs.json';states={job:{'result':'skipped' if job in value['excludedJobs'] else 'success'} for job in value['jobs']};write(needs,json.dumps(states));args=['gate','--plan',plan,'--receipts',out/'receipts','--needs',needs,'--output',out/'gate.json'];good=cli('docs-gate',args,env);rows.append({'case':'complete-docs-gate','pass':docs_code==receipt_code==good==0})
  original=plan.read_bytes()
  for field,bad in [('changedPathsDigest','0'*64),('selectedLanes',[]),('excludedJobs',[])]:
   altered=value|{field:bad};write(plan,json.dumps(altered));code=cli('tamper-'+field,args,env);rows.append({'case':'tamper-'+field,'pass':code!=0});plan.write_bytes(original)
  # Rebind byte digests too: source selection, not merely an old receipt digest, must reject a forged plan.
  payload=receipt.parent/'payload-docs/docs.json';saved_receipt=receipt.read_bytes();saved_payload=payload.read_bytes();altered=value|{'changedPathsDigest':'0'*64};write(plan,json.dumps(altered));pd=hashlib.sha256(plan.read_bytes()).hexdigest();report=json.loads(saved_payload);report['planDigest']=pd;write(payload,json.dumps(report));record=json.loads(saved_receipt);record['planDigest']=pd;record['evidence']['docs.json']=hashlib.sha256(payload.read_bytes()).hexdigest();write(receipt,json.dumps(record));code=cli('forged-plan-rebound-receipt',args,env);rows.append({'case':'source-plan-forgery-refused-after-digest-rebinding','pass':code!=0});plan.write_bytes(original);receipt.write_bytes(saved_receipt);payload.write_bytes(saved_payload)
  wrong_states=json.loads(json.dumps(states));wrong_states[value['excludedJobs'][0]]={'result':'success'};write(needs,json.dumps(wrong_states));code=cli('excluded-job-ran',args,env);rows.append({'case':'excluded-job-unexpectedly-ran-refused','pass':code!=0})
  states['docs']={'result':'skipped'};write(needs,json.dumps(states));code=cli('unexpected-skip',args,env);rows.append({'case':'selected-docs-skip-refused','pass':code!=0})
write(out/'results.json',json.dumps(rows,indent=2));write(out/'manifest.json',json.dumps({'command':sys.argv,'python':sys.version,'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'scope':'Real Git snapshots and production CLI subprocesses; no product evidence fabricated'},indent=2));raise SystemExit(0 if all(r['pass'] for r in rows) else 1)
