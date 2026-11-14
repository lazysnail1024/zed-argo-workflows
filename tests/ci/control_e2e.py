#!/usr/bin/env python3
"""Exercise real command-line planning and gate refusal controls; retain every exit."""
import argparse, copy, hashlib, json, os, pathlib, shutil, subprocess, sys, zipfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(); p.add_argument('--output', type=pathlib.Path, required=True); a=p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
env = os.environ | {'GITHUB_EVENT_NAME':'workflow_dispatch', 'GITHUB_SHA': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(), 'GITHUB_RUN_ID':'123', 'GITHUB_RUN_ATTEMPT':'2', 'GITHUB_REPOSITORY':'lazysnail1024/zed-argo-workflows'}
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
# Same real planner and execution identity under native and CRLF text writers.
wire_paths=[];wire_exits=[];wire_commands=[]
for writer in ('native','windows-text'):
 destination=a.output/('wire-'+writer+'.json')
 code="import sys,runpy;from pathlib import Path;original=Path.write_text;Path.write_text=lambda self,data,**kw: original(self,data,**(kw|{'newline':'\\r\\n'}));sys.argv=sys.argv[1:];sys.path.insert(0,str(Path(sys.argv[0]).parent));runpy.run_path(sys.argv[0],run_name='__main__')"
 command=[sys.executable,str(ROOT/'tests/ci/control.py'),'plan','--workflow','install','--output',str(destination)]
 if writer=='windows-text':command=[sys.executable,'-c',code,*command[1:]]
 result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True)
 (a.output/('wire-'+writer+'.stdout')).write_bytes(result.stdout);(a.output/('wire-'+writer+'.stderr')).write_bytes(result.stderr)
 wire_paths.append(destination);wire_exits.append(result.returncode);wire_commands.append(command)
rows.append({'case':'canonical-plan-wire-bytes','commands':wire_commands,'exitCodes':wire_exits,'pass':wire_exits==[0,0] and all(path.exists() for path in wire_paths) and wire_paths[0].read_bytes()==wire_paths[1].read_bytes() and b'\r' not in wire_paths[0].read_bytes()})
# Transfer an actual producer receipt and nested evidence through a ZIP, then
# consume the extracted bytes with the strict CLI gate. On POSIX, additionally
# exercise Windows relative-path serialization at the producer boundary.
needs.write_text(json.dumps({k:{'result':'success'} for k in value['jobs']}))
for flavor in ('host', 'windows-relative-paths'):
 folder=a.output/('archive-'+flavor);folder.mkdir()
 started=folder/'started.json';run(flavor+'-start',['start','--output',str(started)],0)
 evidence=folder/'input';evidence.mkdir()
 (evidence/'report.json').write_bytes(saved)
 nested=evidence/'nested'/'consumer';nested.mkdir(parents=True)
 (nested/'stdout.log').write_bytes(b'independent nested consumer evidence\n')
 produced=folder/'produced';produced.mkdir();lane=json.loads(original)['lane']
 command=[sys.executable,str(ROOT/'tests/ci/control.py'),'receipt','--plan',str(plan),'--lane',lane,'--evidence',str(evidence),'--started',str(started),'--output',str(produced/(lane+'.json'))]
 if flavor=='windows-relative-paths':
  shim="import sys,runpy;from pathlib import Path,PureWindowsPath;original=Path.relative_to;Path.relative_to=lambda self,*args,**kw: PureWindowsPath(original(self,*args,**kw).as_posix());sys.argv=sys.argv[1:];sys.path.insert(0,str(Path(sys.argv[0]).parent));runpy.run_path(sys.argv[0],run_name='__main__')"
  command=[sys.executable,'-c',shim,*command[1:]]
 result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True)
 (folder/'producer.stdout').write_bytes(result.stdout);(folder/'producer.stderr').write_bytes(result.stderr)
 archive=folder/'evidence.zip'
 with zipfile.ZipFile(archive,'w') as bundle:
  for source in produced.rglob('*'):
   if source.is_file():bundle.write(source,source.relative_to(produced).as_posix())
 extracted=folder/'extracted';shutil.copytree(receipts,extracted)
 with zipfile.ZipFile(archive) as bundle:bundle.extractall(extracted)
 gate_command=['gate','--plan',str(plan),'--receipts',str(extracted),'--needs',str(needs),'--output',str(folder/'gate.json')]
 run(flavor+'-archive-gate',gate_command,0)
 record=json.loads((produced/(lane+'.json')).read_text()) if result.returncode==0 else {}
 rows.append({'case':flavor+'-portable-nested-evidence','command':command,'exitCode':result.returncode,'archiveSha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'pass':result.returncode==0 and record.get('evidence',{}).get('nested/consumer/stdout.log')==hashlib.sha256((nested/'stdout.log').read_bytes()).hexdigest()})
 # Neither relocation nor canonical separators may hide payload corruption.
 (extracted/('payload-'+lane)/'nested'/'consumer'/'stdout.log').write_bytes(b'changed evidence\n')
 run(flavor+'-archive-corruption',gate_command,1)
# Isolated cross-platform input identity: real files, independent equality and
# change-sensitivity expectations. See FAILURE_MODES for the boundary rationale.
fingerprint_root=a.output/'fingerprint-input';(fingerprint_root/'vendor'/'group').mkdir(parents=True)
for name in ('go.mod','go.sum','vendor/modules.txt','contracts/toolkit.lock.json','tests/ci/contracts.json','vendor/Z.h','vendor/a.h','vendor/group.txt','vendor/group/a.h'):
 destination=fingerprint_root/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(('fixture '+name+'\n').encode())
fingerprint_results={}
for name in ('host','windows','bytes','added','deleted','renamed'):
 changed=fingerprint_root/'vendor'/'a.h';saved_input=changed.read_bytes()
 if name=='bytes':changed.write_bytes(saved_input+b'changed\n')
 if name=='added':(fingerprint_root/'vendor'/'new.h').write_bytes(b'new dependency\n')
 if name=='deleted':changed.unlink()
 if name=='renamed':changed.rename(fingerprint_root/'vendor'/'renamed.h')
 code="import sys,json;from pathlib import Path,PureWindowsPath;sys.path.insert(0,sys.argv[1]);import build_identity;build_identity.ROOT=Path(sys.argv[2]);"
 if name=='windows':
  code+="relative=Path.relative_to;Path.relative_to=lambda self,*args,**kw: PureWindowsPath(relative(self,*args,**kw).as_posix());Path.__lt__=lambda self,other: PureWindowsPath(self.as_posix())<PureWindowsPath(other.as_posix());"
 code+="print(json.dumps(build_identity.input_digest(),sort_keys=True))"
 command=[sys.executable,'-c',code,str(ROOT/'tests/ci'),str(fingerprint_root)]
 result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True)
 (a.output/('fingerprint-'+name+'.stdout')).write_bytes(result.stdout);(a.output/('fingerprint-'+name+'.stderr')).write_bytes(result.stderr)
 actual=json.loads(result.stdout) if result.returncode==0 else None;fingerprint_results[name]=actual
 expected=(actual==fingerprint_results['host']) if name in ('host','windows') else (actual!=fingerprint_results['host'])
 rows.append({'case':'input-fingerprint-'+name,'command':command,'exitCode':result.returncode,'pass':result.returncode==0 and expected})
 if name=='renamed':(fingerprint_root/'vendor'/'renamed.h').rename(changed)
 changed.write_bytes(saved_input)
 if name=='added':(fingerprint_root/'vendor'/'new.h').unlink()
(a.output/'results.json').write_text(json.dumps(rows,indent=2))
(a.output/'manifest.json').write_text(json.dumps({'command':sys.argv,'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'scenarioSha256':hashlib.sha256((ROOT/'tests/ci/scenarios/identity-gates.json').read_bytes()).hexdigest(),'identity':{k:env[k] for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_REPOSITORY')}},indent=2))
raise SystemExit(0 if all(r['pass'] for r in rows) else 1)
