#!/usr/bin/env python3
"""Real binary transparent-proxy controls for lifecycle and root ownership."""
import argparse, hashlib, json, os, pathlib, subprocess, sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--binary',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);p.add_argument('--consumer-root',type=pathlib.Path,default=ROOT);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
proxy=ROOT/'tests/e2e/support/audit_proxy.py';rows=[]
for consumer,mode,expected in [('wf3-lsp','none',0),('wf3-lsp','bad-current',1),('wf3-lsp','old-version',0),('wf-equivalence','none',0),('wf-equivalence','drop-root',1),('wf-equivalence','wrong-root',1)]:
 dest=a.output/(consumer+'-'+mode);command=[sys.executable,str(a.consumer_root/'tests/e2e'/f'{consumer}.py'),str(proxy),str(dest)]
 result=subprocess.run(command,env=os.environ|{'WF_AUDIT_BINARY':str(a.binary.resolve()),'WF_AUDIT_FAULT':mode},capture_output=True,timeout=240)
 dest.mkdir(parents=True,exist_ok=True);(dest/'driver.stdout').write_bytes(result.stdout);(dest/'driver.stderr').write_bytes(result.stderr)
 report=json.loads((dest/'results.json').read_text());target=report[0] if consumer=='wf3-lsp' else next(r for r in report if r['name']=='root-isolation')
 designated=(target.get('assertions',{}).get('current_diagnostics_consistent') is False and 'exception' not in target['assertions']) if mode=='bad-current' else ('second root' in (target.get('error') or '') if mode=='drop-root' else 'root ownership' in (target.get('error') or '') if mode=='wrong-root' else True)
 rows.append({'consumer':consumer,'fault':mode,'command':command,'exitCode':result.returncode,'expectedExit':expected,'designatedAssertion':designated,'pass':result.returncode==expected and designated})
 (a.output/'results.json').write_text(json.dumps(rows,indent=2))
(a.output/'manifest.json').write_text(json.dumps({'binary':str(a.binary.resolve()),'binarySha256':hashlib.sha256(a.binary.read_bytes()).hexdigest(),'proxySha256':hashlib.sha256(proxy.read_bytes()).hexdigest(),'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'python':sys.version,'command':sys.argv},indent=2))
raise SystemExit(0 if all(r['pass'] for r in rows) else 1)
