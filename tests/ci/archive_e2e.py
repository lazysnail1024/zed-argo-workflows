#!/usr/bin/env python3
"""Archive a real complete native lane, then revalidate the copied raw evidence."""
import argparse,hashlib,json,pathlib,subprocess,sys
p=argparse.ArgumentParser();p.add_argument('--evidence',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);p.add_argument('--validator-root',type=pathlib.Path,default=pathlib.Path(__file__).resolve().parents[2]);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);root=a.validator_root.resolve();steps=[]
def run(name,command):
 r=subprocess.run(list(map(str,command)),cwd=root,capture_output=True);(a.output/(name+'.stdout.log')).write_bytes(r.stdout);(a.output/(name+'.stderr.log')).write_bytes(r.stderr);steps.append({'name':name,'command':list(map(str,command)),'cwd':str(root),'exitCode':r.returncode,'pass':r.returncode==0});(a.output/'report.json').write_text(json.dumps({'scope':'real native lane archive and read-only verifier; no additional product/platform execution','steps':steps,'passed':all(step['pass'] for step in steps)},indent=2));return r.returncode
plan=a.output/'plan.json';receipt=a.output/'receipts/native-linux-amd64.json';control=root/'tests/ci/control.py'
run('plan',[sys.executable,control,'plan','--workflow','native','--output',plan])
if run('receipt',[sys.executable,control,'receipt','--plan',plan,'--lane','native-linux-amd64','--evidence',a.evidence.resolve(),'--output',receipt])==0:
 code='import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import control;control.validate_evidence("native","native-linux-amd64",Path(sys.argv[2]))'
 run('archived-payload',[sys.executable,'-c',code,root/'tests/ci',receipt.parent/'payload-native-linux-amd64'])
(a.output/'manifest.json').write_text(json.dumps({'python':sys.version,'command':sys.argv,'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'originalSummarySha256':hashlib.sha256((a.evidence/'native/summary.json').read_bytes()).hexdigest()},indent=2))
raise SystemExit(0 if len(steps)==3 and all(step['pass'] for step in steps) else 1)
