#!/usr/bin/env python3
"""Exercise the real aggregate CLI with deliberately incomplete evidence."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();OUT=args.output.resolve();OUT.mkdir(parents=True,exist_ok=False)
CONTROL = ROOT / 'tests/ci/control.py'
ENV = os.environ | {'PYTHONDONTWRITEBYTECODE': '1', 'GITHUB_SHA': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(), 'GITHUB_RUN_ID': '987654322', 'GITHUB_RUN_ATTEMPT': '3', 'GITHUB_REPOSITORY': 'lazysnail1024/zed-argo-workflows'}
steps = []
def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))
def run(args):
    command = [sys.executable, str(CONTROL), *map(str, args)]
    p = subprocess.run(command, cwd=ROOT, env=ENV, capture_output=True, text=True)
    steps.append({'command':command,'cwd':str(ROOT),'exitCode':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
    return p.returncode

run_source = (ROOT / 'tests/ci/run.py').read_text()
selections = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(run_source).body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ('NATIVE', 'EXTENDED')}
native = OUT / 'native-zero-case-evidence'
if native.exists(): shutil.rmtree(native)
plan = native / 'plan.json'
run(['plan','--workflow','native','--output',plan])
value = json.loads(plan.read_text())
for lane in value['selectedLanes']:
    group = lane.split('-')[0]
    names = list(selections['NATIVE']) if group == 'native' else list(selections['EXTENDED'][int(lane.rsplit('-', 1)[1])::3]) if group == 'extended' else ['no-resource-or-mutation-case-executed']
    report = {'identity':value['identity'],'runnerSha256':hashlib.sha256(run_source.encode()).hexdigest(),'pass':True,'tests':[{'name':name,'pass':True,'caseCount':0} for name in names]}
    evidence = native / 'inputs' / lane
    save(evidence / group / 'summary.json',report)
    run(['receipt','--plan',plan,'--lane',lane,'--evidence',evidence,'--output',native/'receipts'/f'{lane}.json'])
save(native/'needs.json',{job:{'result':'success'} for job in value['jobs']})
native_exit = run(['gate','--plan',plan,'--receipts',native/'receipts','--needs',native/'needs.json','--output',native/'gate.json'])

install = OUT / 'fresh-wrapper-old-install-reports'
if install.exists(): shutil.rmtree(install)
plan = install/'plan.json'
run(['plan','--workflow','install','--output',plan])
value = json.loads(plan.read_text())
for lane in value['selectedLanes']:
    previous = OUT/'old-fixtures'/lane/'report.json'
    fixture={'online-real-release':True,'no-backup-rollback-refused':False,'idempotent-reinstall':True,'upgrade-and-retain-backup':True,'explicit-rollback':True,'rollback-is-reversible':True,'reject-mismatched-checksum':False,'reject-corrupt-download':False,'reject-invalid-zip-even-when-hash-matches':False,'reject-wrong-adapter-identity':False,'recover-after-activation-failure':False,'repair-after-failure':True,'recover-missing-active-directory':True}
    if lane.endswith('ubuntu-24.04'):fixture['linux-home-default']=True
    if lane.endswith('windows-2025'):fixture['powershell-7-compatibility']=True
    save(previous,{'passed':True,'installerDefaultsMatchSource':True,'executionIdentity':value['identity']|{'attempt':'1'},'cases':[{'case':name,'exitCode':0 if ok else 1,'expectedSuccess':ok} for name,ok in fixture.items()]})
    archived = install/'previous-attempt'/lane/'report.json'
    archived.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(previous,archived)
    os.utime(archived,ns=(1_000_000_000,1_000_000_000))
    started = install/'started'/f'{lane}.json'
    run(['start','--output',started])
    evidence = install/'inputs'/lane
    evidence.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(archived,evidence/'report.json')
    run(['receipt','--plan',plan,'--lane',lane,'--evidence',evidence,'--started',started,'--output',install/'receipts'/f'{lane}.json'])
save(install/'needs.json',{job:{'result':'success'} for job in value['jobs']})
install_exit = run(['gate','--plan',plan,'--receipts',install/'receipts','--needs',install/'needs.json','--output',install/'gate.json'])

save(OUT/'report.json',{'purpose':'Verifier controls only; no product tests or installation ran','python':sys.version,'replay':[sys.executable,str(Path(__file__).resolve())],'controlSHA256':hashlib.sha256(CONTROL.read_bytes()).hexdigest(),'assertions':{'nativeZeroCasesMustBeRejected':native_exit!=0,'freshlyWrappedOldInstallReportsMustBeRejected':install_exit!=0},'steps':steps})
print(json.dumps({'nativeZeroCaseExit':native_exit,'freshWrapperOldInstallExit':install_exit}))

raise SystemExit(0 if native_exit != 0 and install_exit != 0 else 1)
