#!/usr/bin/env python3
"""Compile each isolated deletion, then run the same independent real LSP client."""
import pathlib, tempfile, shutil, subprocess, os, json, hashlib, sys
root=pathlib.Path(__file__).resolve().parents[2]
out=pathlib.Path(os.environ.get('WF_ABLATION_OUT',root/'artifacts/wf-quickfixes-ablation')).resolve();out.mkdir(parents=True,exist_ok=True)
env=os.environ|{'GOROOT':'/tmp/zed-argo-go-mod/golang.org/toolchain@v0.0.1-go1.27.3.linux-amd64','GOCACHE':'/tmp/wf-quickfixes-ablation-cache','GOWORK':'off','CGO_ENABLED':'1'}
variants=[
 ('pin-document-boundary','internal/analysis/pin.go','at = start','at = len(text)',['pin-root-document-end','pin-root-document-end-crlf']),
 ('new-error','internal/analysis/edits.go','if counts[key] < 0 {','if false && counts[key] < 0 {',['reference-local-candidates','reference-quoted-crlf']),
 ('stale-scope','internal/lsp/edits.go','if err != nil || !opened || s.epoch != action.epoch || b.snapshot.Revision() != action.revision || fingerprint(w) != action.fingerprint {','if false && (err != nil || !opened || s.epoch != action.epoch || b.snapshot.Revision() != action.revision || fingerprint(w) != action.fingerprint) {',['reference-local-candidates','enum-candidates']),
 ('single-literal','internal/analysis/quickfixes.go','if n.Kind() != yamlview.Scalar || raw == "" || strings.ContainsAny(raw, "\\r\\n") || strings.ContainsAny(raw[:1], "|>&*!") {','if n.Kind() != yamlview.Scalar || raw == "" {',['enum-block-scalar'])
]
if len(sys.argv)>1: variants=[v for v in variants if v[0] in sys.argv[1:]]
reports=json.loads((out/'report.json').read_text()) if len(sys.argv)>1 and (out/'report.json').exists() else []
reports=[r for r in reports if r['variant'] not in [v[0] for v in variants]]
for name,file,before,after,expected in variants:
 dest=out/name;dest.mkdir(exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='wf-fix-ablation-') as temp:
  copy=pathlib.Path(temp)
  for directory in ['cmd','compat','internal','vendor']:shutil.copytree(root/directory,copy/directory)
  for f in ['go.mod','go.sum']:shutil.copy(root/f,copy/f)
  p=copy/file;s=p.read_text();assert before in s,(name,before);p.write_text(s.replace(before,after))
  binary=dest/'server';cmd=[env['GOROOT']+'/bin/go','build','-mod=vendor','-buildvcs=false','-trimpath','-o',str(binary),'./cmd/argo-workflows-ls']
  r=subprocess.run(cmd,cwd=copy,env=env,capture_output=True)
  (dest/'build.stdout').write_bytes(r.stdout);(dest/'build.stderr').write_bytes(r.stderr)
  assert r.returncode==0,'compile failure is not evidence'
  cmd=[sys.executable,str(root/'tests/e2e/wf-quickfixes.py'),str(binary),str(dest/'run')]
  r=subprocess.run(cmd,capture_output=True,timeout=180)
  (dest/'run.stdout').write_bytes(r.stdout);(dest/'run.stderr').write_bytes(r.stderr)
  results=json.loads((dest/'run/results.json').read_text())
  failed=[x['case'] for x in results if not x['pass']]
  detected=r.returncode==1 and all(case in failed for case in expected) and 'runner-error' not in failed
  reports.append({'variant':name,'deleted':before,'replacement':after,'environment':{k:env[k] for k in ['GOROOT','GOCACHE','GOWORK','CGO_ENABLED']},'command':cmd,'binarySha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'exitCode':r.returncode,'failed':failed,'expectedFailures':expected,'detected':detected})
  (out/'report.json').write_text(json.dumps(reports,indent=2))
  print(name,detected,failed,flush=True)
sys.exit(0 if all(any(r['variant']==v[0] for r in reports) for v in variants) and all(x['detected'] for x in reports) else 1)
