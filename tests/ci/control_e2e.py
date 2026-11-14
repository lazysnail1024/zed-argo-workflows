#!/usr/bin/env python3
"""Exercise real command-line planning and gate refusal controls; retain every exit."""
import argparse, copy, hashlib, json, os, pathlib, subprocess, sys
ROOT = pathlib.Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(); p.add_argument('--output', type=pathlib.Path, required=True); a=p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
env = os.environ | {'GITHUB_SHA': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(), 'GITHUB_RUN_ID':'123', 'GITHUB_RUN_ATTEMPT':'2', 'GITHUB_REPOSITORY':'lazysnail1024/zed-argo-workflows'}
rows=[]
def run(name, args, expected):
 r=subprocess.run([sys.executable, str(ROOT/'tests/ci/control.py'), *args],cwd=ROOT,env=env,capture_output=True)
 (a.output/(name+'.stdout')).write_bytes(r.stdout); (a.output/(name+'.stderr')).write_bytes(r.stderr)
 rows.append({'case':name,'command':args,'exitCode':r.returncode,'expectedExit':expected,'pass':r.returncode==expected})
 (a.output/'results.json').write_text(json.dumps(rows,indent=2))
 return r
plan=a.output/'plan.json'; run('plan',['plan','--workflow','install','--output',str(plan)],0)
value=json.loads(plan.read_text()); receipts=a.output/'receipts'; receipts.mkdir()
for lane in value['selectedLanes']:
 f=receipts/(lane.replace('/','-')+'.json')
 payload=receipts/('payload-'+lane);payload.mkdir()
 expected={'online-real-release':True,'no-backup-rollback-refused':False,'idempotent-reinstall':True,'upgrade-and-retain-backup':True,'explicit-rollback':True,'rollback-is-reversible':True,'reject-mismatched-checksum':False,'reject-corrupt-download':False,'reject-invalid-zip-even-when-hash-matches':False,'reject-wrong-adapter-identity':False,'recover-after-activation-failure':False,'repair-after-failure':True,'recover-missing-active-directory':True}
 if lane.endswith('ubuntu-24.04'):expected['linux-home-default']=True
 if lane.endswith('windows-2025'):expected['powershell-7-compatibility']=True
 (payload/'report.json').write_text(json.dumps({'executionIdentity':value['identity'],'testSha256':hashlib.sha256((ROOT/'editors/zed/tests/install_e2e.py').read_bytes()).hexdigest(),'installerSha256':hashlib.sha256((ROOT/'editors/zed'/('install.ps1' if lane.endswith('windows-2025') else 'install.sh')).read_bytes()).hexdigest(),'passed':True,'installerDefaultsMatchSource':True,'cases':[{'case':name,'expectedSuccess':ok,'exitCode':0 if ok else 1} for name,ok in expected.items()]}));digest=hashlib.sha256((payload/'report.json').read_bytes()).hexdigest()
 f.write_text(json.dumps({'schemaVersion':1,'lane':lane,'identity':value['identity'],'planDigest':hashlib.sha256(plan.read_bytes()).hexdigest(),'executionStart':{'identity':value['identity']},'evidence':{'report.json':digest},'pass':True}))

needs=a.output/'needs.json'; needs.write_text(json.dumps({k:{'result':'success'} for k in value['jobs']}))
args=['gate','--plan',str(plan),'--receipts',str(receipts),'--needs',str(needs),'--output',str(a.output/'gate.json')]
run('complete',args,0)
first=next(receipts.glob("*.json")); original=first.read_bytes()
first.unlink();run('missing-lane',args,1);first.write_bytes(original)
extra=receipts/'duplicate.json';extra.write_bytes(original);run('duplicate-lane',args,1);extra.unlink()
for name,key,val in [('wrong-sha','testedSHA','0'*40),('wrong-attempt','attempt','1')]:
 record=json.loads(original);record['identity'][key]=val;first.write_text(json.dumps(record));run(name,args,1)
first.write_bytes(original)
for name,key,val in [('unknown-schema','schemaVersion',99),('missing-evidence','evidence',{})]:
 record=json.loads(original);record[key]=val;first.write_text(json.dumps(record));run(name,args,1)
first.write_bytes(original)
payload=receipts/('payload-'+json.loads(original)['lane'])/'report.json';saved=payload.read_bytes()
payload.write_text('{}');run('copied-or-corrupt-report',args,1);payload.write_bytes(saved)
for state in ['skipped','cancelled','failure']:
 states={k:{'result':'success'} for k in value['jobs']};states[value['jobs'][0]]={'result':state};needs.write_text(json.dumps(states));run(state,args,1)
# Exercise the deployed classifier as a subprocess with independent expected modes.
for name,changes,expected,complete in [
 ('docs',[{'status':'M','path':'README.md'}],'docs',True),
 ('mixed',[{'status':'M','path':'README.md'},{'status':'M','path':'internal/lsp/session.go'}],'full',True),
 ('snippets',[{'status':'M','path':'editors/zed/snippets/yaml.json'}],'full',True),
 ('headers',[{'status':'M','path':'vendor/parser.h'}],'full',True),
 ('unknown',[{'status':'M','path':'new-file'}],'full',True),
 ('rename',[{'status':'R','path':'README.md'}],'full',True),
 ('delete',[{'status':'D','path':'README.md'}],'full',True),
 ('truncated',[{'status':'M','path':'README.md'}],'full',False)]:
 source=a.output/('diff-'+name+'.json');source.write_text(json.dumps({'complete':complete,'changes':changes}));destination=a.output/('selection-'+name+'.json')
 cmd=[sys.executable,str(ROOT/'tests/ci/selection.py'),'--changes',str(source),'--output',str(destination)]
 result=subprocess.run(cmd,capture_output=True);(a.output/('selection-'+name+'.stdout')).write_bytes(result.stdout);(a.output/('selection-'+name+'.stderr')).write_bytes(result.stderr)
 actual=json.loads(destination.read_text()) if destination.exists() else {}
 rows.append({'case':'selection-'+name,'command':cmd,'exitCode':result.returncode,'pass':result.returncode==0 and actual.get('classification')==expected and actual.get('effectiveSelection')=='full'})
(a.output/'results.json').write_text(json.dumps(rows,indent=2))
(a.output/'manifest.json').write_text(json.dumps({'command':sys.argv,'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'scenarioSha256':hashlib.sha256((ROOT/'tests/ci/scenarios/identity-gates.json').read_bytes()).hexdigest(),'identity':{k:env[k] for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_REPOSITORY')}},indent=2))
raise SystemExit(0 if all(r['pass'] for r in rows) else 1)
