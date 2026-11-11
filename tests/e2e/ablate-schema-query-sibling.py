#!/usr/bin/env python3
"""Reverse branch-only admission gating and run real sibling-error consumers."""
import pathlib,sys,os,shutil,json,subprocess,hashlib
root=pathlib.Path(__file__).resolve().parents[2];out=pathlib.Path(sys.argv[1]).resolve();source=out/'source';source.mkdir(parents=True,exist_ok=False)
for folder in ['cmd','internal','compat','schemas','vendor']:shutil.copytree(root/folder,source/folder)
for file in ['go.mod','go.sum']:shutil.copy2(root/file,source/file)
path=source/'vendor/github.com/lazysnail1024/k8s-crd-toolkit/schemaquery/query.go';before=path.read_text();start=before.index('func (w *walk) allowsKey(');end=before.index('// Project constraints',start);part=before[start:end];part=part.replace('!w.inspect(s, depth) || s.Bool != nil && !*s.Bool','!w.inspect(s, depth) || !w.compatible(s, path, value, depth)').replace('w.compatible(child, path, value, depth+1) && w.allowsKey','w.allowsKey');after=before[:start]+part+before[end:];assert before!=after;path.write_text(after);(out/'change.json').write_text(json.dumps({'file':str(path.relative_to(source)),'before':before,'after':after},indent=2)+'\n')
env=os.environ|{'GOWORK':'off','GOTOOLCHAIN':'local','CGO_ENABLED':'1'};cmd=[str(pathlib.Path(env['GOROOT'])/'bin/go'),'build','-mod=vendor','-trimpath','-buildvcs=false','-o',str(out/'server'),'./cmd/argo-workflows-ls']
with (out/'build.stdout').open('wb') as so,(out/'build.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=source,env=env,stdout=so,stderr=se)
report={'buildCommand':cmd,'buildExit':r.returncode,'detected':False}
if r.returncode==0:
 report['binarySha256']=hashlib.sha256((out/'server').read_bytes()).hexdigest();cmd=[sys.executable,str(root/'tests/e2e/wf-schema-query-sibling.py'),str(out/'server'),str(out/'consumer')];report['consumerCommand']=cmd
 with (out/'consumer.stdout').open('wb') as so,(out/'consumer.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=root,stdout=so,stderr=se,timeout=90)
 report['consumerExit']=r.returncode;rows=json.loads((out/'consumer/results.json').read_text());report['failedCases']=[x['case'] for x in rows if not x['pass']];report['detected']=r.returncode==1 and len(rows)==2 and report['failedCases']==['unrelated-invalid-enum-key'] and rows[0]['assertions']['noProtocolError'] and rows[0]['assertions']['expected-exactDiagnostics']
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));sys.exit(0 if report['detected'] else 1)
