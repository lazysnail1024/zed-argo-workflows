#!/usr/bin/env python3
"""Challenge complete downloaded same-attempt receipts with the real required gate.

Requires the original plan, needs results and every selected lane payload. Never
restamps execution identity or synthesizes missing lanes. Only the Linux lane is
rearchived; all other downloaded lanes remain byte-identical in the gate input.
"""
import argparse, hashlib, json, os, pathlib, shutil, subprocess, sys, time, zipfile
from lifecycle_evidence_faults import FAULTS, corrupt
ROOT=pathlib.Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--plan',type=pathlib.Path,required=True);p.add_argument('--needs',type=pathlib.Path);p.add_argument('--receipts',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();a.output=a.output.resolve();a.output.mkdir(parents=True,exist_ok=False)
if a.needs is None:
 a.needs=a.output/'needs.json';a.needs.write_text(os.environ['RESULTS'])
plan=json.loads(a.plan.read_text());ident=plan['identity'];env=os.environ|{'GITHUB_REPOSITORY':ident['repository'],'GITHUB_SHA':ident['testedSHA'],'GITHUB_RUN_ID':ident['runID'],'GITHUB_RUN_ATTEMPT':ident['attempt']}
rows=[];metrics={'archiveMethod':'ZIP_STORED','scope':'Uncompressed local transport roundtrip of the original downloaded Linux native lane. Other 16 lanes remain exact downloaded bytes. Not hosted upload/download, compression, job or critical-path timing.','phases':[]}
def save():
 (a.output/'results.json').write_text(json.dumps(rows,indent=2));(a.output/'metrics.json').write_text(json.dumps(metrics,indent=2))
def phase(name,status):
 metrics['phases'].append({'name':name,'status':status,'time':time.time()});save()
def run(name,arguments,success,reason=None,output=None):
 dest=a.output/'controls'/name;dest.mkdir(parents=True,exist_ok=True);cmd=[sys.executable,str(ROOT/'tests/ci/control.py'),*map(str,arguments),'--output',str(output or dest/'result.json')];phase(name,'started');start=time.monotonic();r=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True);(dest/'stdout.log').write_bytes(r.stdout);(dest/'stderr.log').write_bytes(r.stderr)
 stderr=r.stderr.decode(errors='replace');rows.append({'name':name,'command':cmd,'exitCode':r.returncode,'expectedSuccess':success,'seconds':time.monotonic()-start,'reason':stderr,'requiredReason':reason,'pass':(r.returncode==0)==success and (reason is None or reason in stderr)});phase(name,'completed');return rows[-1]['pass']
def gate(name,directory,success,reason=None):return run(name,['gate','--plan',a.plan.resolve(),'--needs',a.needs.resolve(),'--receipts',directory],success,reason)
if not gate('original-downloaded-positive',a.receipts.resolve(),True):raise SystemExit('Original complete gate failed; controls not run')
original_receipt=next(a.receipts.rglob('native-linux-amd64.json'));original_payload=original_receipt.parent/'payload-native-linux-amd64'
published=a.output/'published';published_receipt=published/'native-linux-amd64.json'
if not run('original-payload-receipt',['receipt','--plan',a.plan.resolve(),'--lane','native-linux-amd64','--evidence',original_payload.resolve()],True,output=published_receipt):raise SystemExit('Original receipt regeneration failed')
archive=a.output/'linux-lane.zip';phase('archive','started');start=time.monotonic()
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as z:
 for f in sorted(published.rglob('*')):
  if f.is_file():z.write(f,f.relative_to(published))
metrics['archiveSeconds']=time.monotonic()-start;metrics['archiveBytes']=archive.stat().st_size;metrics['payloadBytes']=sum(f.stat().st_size for f in published.rglob('*') if f.is_file())
with archive.open('rb') as stream:metrics['archiveSha256']=hashlib.file_digest(stream,'sha256').hexdigest()
# Put the intentional fault first without changing the production verifier.
# Complete positive gates still validate every lane and every payload byte.
phase('archive','completed');phase('extract','started');start=time.monotonic();download=a.output/'downloaded';target_parent=download
with zipfile.ZipFile(archive) as z:z.extractall(target_parent)
metrics['extractSeconds']=time.monotonic()-start;phase('extract','completed');phase('link-other-lanes','started')
# Hardlinks preserve the large, read-only non-target lanes without a second copy.
for f in a.receipts.rglob('*'):
 if f.is_file() and f!=original_receipt and original_payload not in f.parents:
  dest=download/'other-lanes'/f.relative_to(a.receipts);dest.parent.mkdir(parents=True,exist_ok=True);os.link(f,dest)
phase('link-other-lanes','completed')
receipt=target_parent/'native-linux-amd64.json';payload=target_parent/'payload-native-linux-amd64';child=payload/'native/wf3-lsp'
receipt_order=[path for path in download.rglob('*.json') if not any(part.startswith('payload-') for part in path.parts)]
metrics['python']=sys.version;metrics['receiptOrder']=[path.relative_to(download).as_posix() for path in receipt_order];save()
if not receipt_order or receipt_order[0]!=receipt:raise SystemExit('Roundtrip receipt traversal did not put the target Linux lane first')
if not gate('rearchived-positive',download,True):raise SystemExit('Rearchived positive failed')
for filename in ('stdout.raw','stderr.raw','traffic.json','lifecycle.json','input-buffer.yaml','input-disk.yaml'):
 path=child/filename;raw=path.read_bytes();path.unlink()
 run('before-receipt-missing-'+filename,['receipt','--plan',a.plan.resolve(),'--lane','native-linux-amd64','--evidence',payload],False,'missing required child material')
 gate('after-download-missing-'+filename,download,False,'missing or changed consumer evidence');path.write_bytes(raw)
for key in ('expectedSha256','caseSha256','inputHashes','inputPath','lifecycle'):
 path=child/('lifecycle.json' if key=='lifecycle' else 'manifest.json');raw=path.read_bytes();v=json.loads(raw)
 if key=='lifecycle':v['reaped']=False;reason='incomplete child lifecycle'
 elif key=='inputPath':v['inputHashes']={'missing.yaml':'0'*64};reason='wrong input path/hash inventory'
 elif key=='inputHashes':v[key]={name:'0'*64 for name in v[key]};reason='wrong input path/hash inventory'
 else:v[key]='0'*64;reason='wrong '+key
 path.write_text(json.dumps(v));run('before-receipt-wrong-'+key,['receipt','--plan',a.plan.resolve(),'--lane','native-linux-amd64','--evidence',payload],False,reason)
 # A new outer digest cannot legitimize semantically wrong child evidence.
 rr=receipt.read_bytes();value=json.loads(rr);relative=path.relative_to(payload).as_posix();value['evidence'][relative]=hashlib.sha256(path.read_bytes()).hexdigest();receipt.write_text(json.dumps(value));gate('rehashed-download-wrong-'+key,download,False,reason);receipt.write_bytes(rr);path.write_bytes(raw)
for fault in FAULTS:
 paths=[child/name for name in ('traffic.json','diagnostic-phases.json','stdout.raw')];originals={p:p.read_bytes() for p in paths};rr=receipt.read_bytes();corrupt(child,fault)
 run('before-receipt-'+fault,['receipt','--plan',a.plan.resolve(),'--lane','native-linux-amd64','--evidence',payload],False,'wf3-lsp: lifecycle evidence')
 value=json.loads(rr)
 for path in paths:value['evidence'][path.relative_to(payload).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
 receipt.write_text(json.dumps(value));gate('rehashed-download-'+fault,download,False,'wf3-lsp: lifecycle evidence');receipt.write_bytes(rr)
 for path,raw in originals.items():path.write_bytes(raw)
gate('restored-positive',download,True)
(a.output/'manifest.json').write_text(json.dumps({'command':sys.argv,'identity':ident,'python':sys.version,'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()},indent=2));save()
# The original download and one archive retain all positive bytes. Avoid uploading
# duplicate hardlinked payloads with the small control reports.
shutil.rmtree(download);shutil.rmtree(published)
raise SystemExit(0 if all(r['pass'] for r in rows) else 1)
