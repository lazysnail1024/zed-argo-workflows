#!/usr/bin/env python3
"""Run the real consumer then challenge its production evidence verifier."""
import argparse, copy, hashlib, json, pathlib, shutil, subprocess, sys
from lifecycle_evidence_faults import FAULTS, corrupt
ROOT=pathlib.Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--binary',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
positive=a.output/'positive';command=[sys.executable,str(ROOT/'tests/e2e/wf3-lsp.py'),str(a.binary.resolve()),str(positive)];r=subprocess.run(command,capture_output=True);(a.output/'consumer.stdout').write_bytes(r.stdout);(a.output/'consumer.stderr').write_bytes(r.stderr)
if r.returncode:raise SystemExit('positive consumer failed')
controls=[('positive',None)]+[(f'missing-{f}',('delete',f)) for f in ['stdout.raw','stderr.raw','traffic.json','lifecycle.json','input-buffer.yaml','input-disk.yaml']]+[(f'wrong-{key}',('hash',key)) for key in ['expectedSha256','caseSha256','inputHashes']]+[('wrong-input-path',('path',None)),('unreaped',('lifecycle','reaped')),('undrained',('lifecycle','threadsDrained'))]
controls += [(name,('review',name)) for name in FAULTS]
rows=[]
for name,mutation in controls:
 dest=a.output/name if name!='positive' else positive
 if dest!=positive:shutil.copytree(positive,dest)
 if mutation:
  kind,key=mutation
  if kind=='review':corrupt(dest,key)
  elif kind=='delete':(dest/key).unlink()
  elif kind=='lifecycle':
   path=dest/'lifecycle.json';value=json.loads(path.read_text());value[key]=False;path.write_text(json.dumps(value))
  else:
   path=dest/'manifest.json';value=json.loads(path.read_text())
   if key=='inputHashes':value[key]={n:'0'*64 for n in value[key]}
   elif kind=='path':value['inputHashes']={'nonexistent.yaml':'0'*64}
   else:value[key]='0'*64
   path.write_text(json.dumps(value))
 code='import pathlib,run; print(run.consumer_results("wf3-lsp",pathlib.Path(__import__("sys").argv[1]),pathlib.Path(__import__("sys").argv[2])))'
 cmd=[sys.executable,'-c',code,str(dest.resolve()),str(a.binary.resolve())];result=subprocess.run(cmd,cwd=ROOT/'tests/ci',capture_output=True)
 (dest/'verifier.stdout').write_bytes(result.stdout);(dest/'verifier.stderr').write_bytes(result.stderr)
 rows.append({'name':name,'command':cmd,'exitCode':result.returncode,'pass':(result.returncode==0)==(mutation is None)})
(a.output/'results.json').write_text(json.dumps(rows,indent=2));(a.output/'manifest.json').write_text(json.dumps({'command':sys.argv,'consumerCommand':command,'binarySha256':hashlib.sha256(a.binary.read_bytes()).hexdigest(),'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()},indent=2))
raise SystemExit(0 if all(row['pass'] for row in rows) else 1)
