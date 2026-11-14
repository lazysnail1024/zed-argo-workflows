#!/usr/bin/env python3
"""Small same-run acceptance ledger. It never substitutes for consumer assertions."""
import argparse
import ast
import re
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import time
import sys

ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = ['linux/amd64', 'linux/arm64', 'darwin/amd64', 'darwin/arm64', 'windows/amd64']
LANES = {
 'native': [*['native-'+p.replace('/','-') for p in PLATFORMS], *['extended-'+str(n) for n in range(3)], *['resources-'+n for n in ['adversarial','load','index','queries']], *['mutations-'+n for n in ['core','audit','contracts','legacy','resources']]],
 'zed': ['rust-ubuntu-24.04','rust-macos-15','rust-windows-2025','package'],
 'install': ['install-ubuntu-24.04','install-macos-15','install-windows-2025'],
}
JOBS={'native':['prepare','ordinary','native','extended','resources','mutations','candidate'], 'zed':['rust','package'], 'install':['install']}

INSTALL_CASES = {
 'online-real-release':True, 'no-backup-rollback-refused':False,
 'idempotent-reinstall':True, 'upgrade-and-retain-backup':True,
 'explicit-rollback':True, 'rollback-is-reversible':True,
 'reject-mismatched-checksum':False, 'reject-corrupt-download':False,
 'reject-invalid-zip-even-when-hash-matches':False,
 'reject-wrong-adapter-identity':False, 'recover-after-activation-failure':False,
 'repair-after-failure':True, 'recover-missing-active-directory':True,
}

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def git(*args): return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
def save(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def identity():
 return {'repository':os.environ.get('GITHUB_REPOSITORY','lazysnail1024/zed-argo-workflows'), 'testedSHA':git('rev-parse','HEAD'), 'runID':os.environ.get('GITHUB_RUN_ID','local'), 'attempt':os.environ.get('GITHUB_RUN_ATTEMPT','1')}
def require(ok,message):
 if not ok: raise ValueError(message)
def plan(workflow):
 ident=identity(); require(os.environ.get('GITHUB_SHA',ident['testedSHA'])==ident['testedSHA'],'checkout differs from tested SHA')
 # Selection intentionally stays full until representative hosted acceptance is available.
 # Unknown, deleted, renamed and truncated diffs cannot narrow this contract.
 event={}
 if os.environ.get('GITHUB_EVENT_PATH'):
  event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
 pr=event.get('pull_request',{})
 return {'schemaVersion':1,'planVersion':1,'workflow':workflow,'identity':ident,'baseSHA':pr.get('base',{}).get('sha'), 'headSHA':pr.get('head',{}).get('sha',ident['testedSHA']), 'testedSHA':ident['testedSHA'],'changedPathsDigest':None,'selection':'full','selectionReason':'Conservative full rollout; hosted narrowing acceptance is not yet established','selectedLanes':LANES[workflow],'excludedLanes':[],'jobs':JOBS[workflow]}
def validate_plan(value):
 require(value.get('schemaVersion')==1 and value.get('planVersion')==1,'unsupported plan schema')
 require(value.get('identity')==identity(),'plan source/run/attempt mismatch; rerun the complete producer and consumer group')
 workflow=value['workflow']
 require(value.get('selectedLanes')==LANES[workflow] and value.get('jobs')==JOBS[workflow] and value.get('excludedLanes')==[], 'selection differs from trusted full contract')

def validate_evidence(workflow,lane,evidence):
 if workflow=='native':
  import evidence as original_evidence
  original_evidence.native(lane,evidence)
 if workflow=='install':
  report=json.loads((evidence/'report.json').read_text())
  require(report.get('passed') is True and report.get('installerDefaultsMatchSource') is True and len(report.get('cases',[]))>=10,'incomplete file-install report')
  require(report.get('executionIdentity')==identity(),'stale original installer execution')
  require(report.get('testSha256')==sha(ROOT/'editors/zed/tests/install_e2e.py'),'installer test source mismatch')
  installer=ROOT/'editors/zed'/('install.ps1' if lane.endswith('windows-2025') else 'install.sh')
  require(report.get('installerSha256')==sha(installer),'installer source mismatch')
  expected=INSTALL_CASES.copy()
  if lane.endswith('ubuntu-24.04'):expected['linux-home-default']=True
  if lane.endswith('windows-2025'):expected['powershell-7-compatibility']=True
  require(sorted(r['case'] for r in report['cases'])==sorted(expected),'missing or duplicate installer case')
  require(all(r['expectedSuccess'] is expected[r['case']] and (r['exitCode']==0)==expected[r['case']] for r in report['cases']),'incorrect installer outcome')
 if workflow=='zed':
  if lane.startswith('rust-'):
   log=(evidence/'tests.log').read_text()
   require(re.search(r'test result: ok\. [1-9][0-9]* passed; 0 failed',log) is not None,'missing successful installer tests')
  else:
   report=json.loads((evidence/'zed-adapter.json').read_text())
   require(report.get('sourceCommit')==identity()['testedSHA'] and report.get('kind')=='zed-adapter','wrong candidate package identity')

def receipt(args):
 value=json.loads(args.plan.read_text());validate_plan(value)
 require(args.lane in value['selectedLanes'],'unselected lane')
 files=[p for p in args.evidence.rglob('*') if p.is_file() and (p.suffix in ('.json','.log','.stdout','.stderr','.raw') or p.name in ('argo-workflows-ls','argo-workflows-ls.exe'))]
 if value['workflow']!='native':
  started=json.loads(args.started.read_text());require(started.get('identity')==identity(),'stale job launch identity')
  require(all(p.stat().st_mtime_ns>=started['startedNs'] for p in files),'report predates this execution')
 require(files,'missing lane evidence')
 validate_evidence(value['workflow'],args.lane,args.evidence)
 evidence={str(p.relative_to(args.evidence)):sha(p) for p in files}
 payload=args.output.parent/('payload-'+args.lane)
 require(not args.output.exists() and not payload.exists(),'refusing old lane receipt')
 for source in files:
  dest=payload/source.relative_to(args.evidence);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
 save(args.output,{'schemaVersion':1,'lane':args.lane,'identity':identity(),'planDigest':sha(args.plan),'evidence':evidence,'executionStart':json.loads(args.started.read_text()) if args.started else None,'pass':True})

def gate(args):
 value=json.loads(args.plan.read_text());validate_plan(value)
 needs=json.loads(args.needs.read_text()) if args.needs else json.loads(os.environ['RESULTS'])
 require(set(needs)==set(value['jobs']),'missing or unexpected dependency')
 require(all(v.get('result')=='success' for v in needs.values()),'selected dependency failed, cancelled or skipped')
 paths=[p for p in args.receipts.rglob('*.json') if not any(part.startswith('payload-') for part in p.parts)]
 rows=[json.loads(p.read_text()) for p in paths]
 require(sorted(r.get('lane','') for r in rows)==sorted(value['selectedLanes']),'missing or duplicate lane receipt')
 for row,path in zip(rows,paths):
  require(row.get('schemaVersion')==1,'unsupported receipt schema')
  require(row.get('identity')==identity(),'receipt source/run/attempt mismatch')
  require(row.get('planDigest')==sha(args.plan),'receipt plan mismatch')
  require(row.get('pass') is True and bool(row.get('evidence')),'missing successful evidence')
  for relative,digest in row['evidence'].items():
   target=path.parent/('payload-'+row['lane'])/relative
   require(not Path(relative).is_absolute() and '..' not in Path(relative).parts,'unsafe evidence path')
   require(target.is_file() and sha(target)==digest,'missing or changed consumer evidence')
  if value['workflow']!='native':
   require(row.get('executionStart',{}).get('identity')==identity(),'stale job execution receipt')
  validate_evidence(value['workflow'],row['lane'],path.parent/('payload-'+row['lane']))
 save(args.output,{'schemaVersion':1,'identity':identity(),'planDigest':sha(args.plan),'lanes':value['selectedLanes'],'pass':True})

def baseline(args):
 import run
 frozen=ast.parse(git('show','HEAD:tests/ci/run.py'))
 selections={node.targets[0].id:ast.literal_eval(node.value) for node in frozen.body if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('NATIVE','EXTENDED')}
 resource_source=git('show','HEAD:tests/ci/resources.py')
 resource_scripts=sorted(set(re.findall(r'"(wf-[a-z0-9-]+)"',resource_source)))
 files=['tests/ci/contracts.json','tests/ci/resources.py','tests/ci/run.py','.github/workflows/native.yml']
 save(args.output,{'schemaVersion':1,'sourceCommit':git('rev-parse','HEAD'),'sourceTree':git('rev-parse','HEAD^{tree}'),'lanes':LANES,'nativeConsumers':selections['NATIVE'],'extendedConsumers':selections['EXTENDED'],'mutations':{n:run.mutation_selection(n) for n in ['core','audit','contracts','legacy','resources']},'contracts':json.loads(git('show','HEAD:'+files[0])),'inputs':{f:hashlib.sha256(subprocess.check_output(['git','show','HEAD:'+f],cwd=ROOT)).hexdigest() for f in files},'frozenResourceSource':resource_source,'resourceConsumerSources':{name:git('show','HEAD:tests/e2e/'+name+'.py') for name in resource_scripts if (ROOT/'tests/e2e'/ (name+'.py')).is_file()},'timingSamples':[],'timingStatus':'not collected; hosted baseline matrix required','resourceContractSource':'tests/ci/resources.py; retained verbatim by digest'})

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
 s=sub.add_parser('plan');s.add_argument('--workflow',choices=LANES,required=True)
 s=sub.add_parser('baseline')
 s=sub.add_parser('start')
 s=sub.add_parser('receipt');s.add_argument('--plan',type=Path,required=True);s.add_argument('--lane',required=True);s.add_argument('--evidence',type=Path,required=True);s.add_argument('--started',type=Path)
 s=sub.add_parser('gate');s.add_argument('--plan',type=Path,required=True);s.add_argument('--receipts',type=Path,required=True);s.add_argument('--needs',type=Path)
 for s in sub.choices.values():s.add_argument('--output',type=Path,required=True)
 a=p.parse_args()
 try:
  if a.action=='start':save(a.output,{'schemaVersion':1,'identity':identity(),'startedNs':time.time_ns()})
  elif a.action=='plan':save(a.output,plan(a.workflow))
  else:globals()[a.action](a)
 except Exception as e:
  save(a.output,{'pass':False,'error':str(e)});print(str(e),file=sys.stderr);return 1
 return 0
if __name__=='__main__':raise SystemExit(main())
