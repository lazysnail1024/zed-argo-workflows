#!/usr/bin/env python3
"""Synthetic P02 orchestration through unchanged production verifier CLIs."""
import argparse,hashlib,json,os,pathlib,subprocess,sys,time
from fixtures import save,load,native,simple
ROOT=pathlib.Path.cwd()
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def frozen():
 expected=load(ROOT/'p02/frozen-inputs.json')
 for name,value in expected['inputs'].items():
  if digest(ROOT/name)!=value:raise ValueError('Frozen verifier/input changed: '+name)
def cli(out,label,*args):
 out.mkdir(parents=True,exist_ok=True);cmd=[sys.executable,str(ROOT/'tests/ci/control.py'),*map(str,args)];r=subprocess.run(cmd,capture_output=True);(out/(label+'.stdout')).write_bytes(r.stdout);(out/(label+'.stderr')).write_bytes(r.stderr);save(out/(label+'.command.json'),{'command':cmd,'exitCode':r.returncode});return r.returncode
def prepare(out,mode):
 frozen();scenario=load(ROOT/'p02/scenario.json')['scenario'];effective='success' if mode=='manual-green' else 'success' if mode=='cancel-upstream' else scenario
 save(out/'scenario.json',{'scenario':scenario,'effectiveScenario':effective,'mode':mode,'diagnosticFixture':True})
 for workflow in ['native','zed','install']:
  if cli(out/'commands','plan-'+workflow,'plan','--workflow',workflow,'--output',out/workflow/'plan.json'):return 1
 if os.environ.get('GITHUB_OUTPUT'):
  with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('scenario='+effective+'\n')
 return 0
def fixtures(out):
 frozen();scenario=load(out/'scenario.json')['effectiveScenario'];count=0
 for workflow in ['native','zed','install']:
  plan=out/workflow/'plan.json';value=load(plan)
  for lane in value['selectedLanes']:
   evidence=out/workflow/'inputs'/lane;started=out/workflow/'starts'/(lane+'.json')
   if cli(out/'commands','start-'+lane,'start','--output',started):return 1
   if workflow=='native':native(lane,evidence,value['identity'])
   else:simple(workflow,lane,evidence,value['identity'])
   if cli(out/'commands','receipt-'+lane,'receipt','--plan',plan,'--lane',lane,'--evidence',evidence,'--started',started,'--output',out/workflow/'receipts'/(lane+'.json')):return 1
   count+=1
  first=out/workflow/'receipts'/(value['selectedLanes'][0]+'.json')
  if scenario=='missing-report':first.unlink()
  if scenario=='wrong-sha':r=load(first);r['identity']['testedSHA']='0'*40;save(first,r)
 save(out/'fixture-audit.json',{'diagnosticFixture':True,'scope':'Synthetic receipt shapes only; no platform product, resource or mutation execution','productExecutions':0,'receiptCount':count,'workflows':['native','zed','install']})
 return 0
def gate(inputs,out,workflow):
 frozen();out.mkdir(parents=True,exist_ok=True);actual=json.loads(os.environ['P02_NEEDS_JSON']);save(out/'actual-needs.json',actual)
 value=load(inputs/workflow/'plan.json');upstream=actual.get('upstream',{}).get('result','missing');prepare=actual.get('prepare',{}).get('result','missing');states={job:{'result':upstream if prepare=='success' else prepare} for job in value['jobs']};save(out/'projected-needs.json',states)
 return cli(out,'gate-'+workflow,'gate','--plan',inputs/workflow/'plan.json','--receipts',inputs/workflow/'receipts','--needs',out/'projected-needs.json','--output',out/'gate.json')
p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','fixtures','gate','finish','wait-cancel']);p.add_argument('--output',type=pathlib.Path,required=True);p.add_argument('--inputs',type=pathlib.Path);p.add_argument('--workflow',choices=['native','zed','install']);p.add_argument('--mode',default='scenario');p.add_argument('--seconds',type=int,default=600);a=p.parse_args();out=a.output.resolve()
if a.action=='prepare':code=prepare(out,a.mode)
elif a.action=='fixtures':code=fixtures(out)
elif a.action=='gate':code=gate(a.inputs.resolve(),out,a.workflow)
elif a.action=='finish':code=11 if load(out/'scenario.json')['effectiveScenario'] in ['upstream-failure','manual-green'] else 0
else:print('P02 cancellation ready',flush=True);time.sleep(a.seconds);code=12
raise SystemExit(code)
