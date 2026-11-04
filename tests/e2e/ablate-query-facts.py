#!/usr/bin/env python3
"""Compiled full-source identity removal against warmed real LSP consumers."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys
root=Path(__file__).resolve().parents[2];out=Path(sys.argv[1]).resolve();source=out/'source';source.mkdir(parents=True,exist_ok=False)
for folder in ['cmd','internal','compat','schemas','vendor']:shutil.copytree(root/folder,source/folder)
for name in ['go.mod','go.sum']:shutil.copy2(root/name,source/name)
p=source/'internal/analysis/invocation.go';before=p.read_text();after=before.replace('return snapshotIdentity{s.URI(), s.Revision(), s.Text()}', 'return snapshotIdentity{s.URI(), s.Revision(), ""}',1);assert before!=after;p.write_text(after)
(out/'change.json').write_text(json.dumps({'file':str(p.relative_to(source)),'before':before,'after':after},indent=2)+'\n')
env=os.environ|{'GOWORK':'off','GOTOOLCHAIN':'local','CGO_ENABLED':'1','GOPROXY':'off','GOSUMDB':'off'}
cmd=[str(Path(env['GOROOT'])/'bin/go'),'build','-mod=vendor','-trimpath','-buildvcs=false','-o',str(out/'native'),'./cmd/argo-workflows-ls']
with (out/'build.stdout').open('wb') as so,(out/'build.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=source,env=env,stdout=so,stderr=se)
report={'buildCommand':cmd,'buildExit':r.returncode,'detected':False}
if r.returncode==0:
 report['nativeSha256']=hashlib.sha256((out/'native').read_bytes()).hexdigest();cmd=[sys.executable,str(root/'tests/e2e/wf-query-fact-handoff.py'),str(out/'native'),str(out/'consumer')]
 with (out/'consumer.stdout').open('wb') as so,(out/'consumer.stderr').open('wb') as se:r=subprocess.run(cmd,env=env,stdout=so,stderr=se,timeout=180)
 results=json.loads((out/'consumer/results.json').read_text());failed=[x['case'] for x in results if not x['pass']];report.update({'consumerCommand':cmd,'consumerExit':r.returncode,'failedCases':failed,'detected':r.returncode==1 and 'same-uri-same-length-disk-change' in failed})
(out/'results.json').write_text(json.dumps(report,indent=2)+'\n');sys.exit(0 if report['detected'] else 1)
