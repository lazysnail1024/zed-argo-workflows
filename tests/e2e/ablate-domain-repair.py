#!/usr/bin/env python3
"""Compiled removals of read-only recovery, visibility, and lexical selection."""
import pathlib,sys,os,shutil,json,subprocess,hashlib
root=pathlib.Path(__file__).resolve().parents[2];out=pathlib.Path(sys.argv[1]).resolve();env=os.environ|{'GOWORK':'off','GOTOOLCHAIN':'local','CGO_ENABLED':'1'};rows=[]
for name in ['scalar-encoding','external-definition-role','cross-role-uniqueness','inexact-role']:
 if len(sys.argv)>2 and name not in sys.argv[2:]:continue
 dest=out/name;source=dest/'source';source.mkdir(parents=True,exist_ok=False)
 for folder in ['cmd','internal','compat','schemas','vendor']:shutil.copytree(root/folder,source/folder)
 for file in ['go.mod','go.sum']:shutil.copy2(root/file,source/file)
 suite='wf-domain-repair'
 if name=='scalar-encoding':
  path=source/'internal/analysis/domain_queries.go';before=path.read_text();after=before.replace('return strconv.Quote(name)', 'return name',1).replace('"strconv"','');required=['yaml-plain-template-true','yaml-plain-template-null','yaml-plain-template-123','yaml-plain-argument-true','yaml-external-argument-plain']
 elif name=='external-definition-role':
  path=source/'internal/lsp/stdio.go';before=path.read_text();needle='result = append(result, analysis.ExternalDefinitions(ctx, scopeWorkspace(w, d.Target.ID), string(d.Snapshot.URI()), pos)...)';after=before.replace(needle, 'if len(result) == 0 { '+needle+' }',1);required=['external-overlap']
 elif name=='cross-role-uniqueness':
  path=source/'internal/analysis/external.go';before=path.read_text();after=before.replace('if len(targets) != 1 {','if false && len(targets) != 1 {',1);required=['external-overlap']
 else:
  path=source/'internal/analysis/external.go';before=path.read_text();after=before.replace('if containsPosition(selection, uri, pos) {','if false && containsPosition(selection, uri, pos) {',1);required=['external-overlap-inexact-input','external-overlap-duplicate-input']
 assert before!=after;path.write_text(after);(dest/'change.json').write_text(json.dumps({'file':str(path.relative_to(source)),'before':before,'after':after},indent=2)+'\n')
 cmd=[str(pathlib.Path(env['GOROOT'])/'bin/go'),'build','-mod=vendor','-trimpath','-buildvcs=false','-o',str(dest/'server'),'./cmd/argo-workflows-ls']
 with (dest/'build.stdout').open('wb') as so,(dest/'build.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=source,env=env,stdout=so,stderr=se)
 row={'variant':name,'buildCommand':cmd,'buildExit':r.returncode,'requiredFailures':required}
 if r.returncode==0:
  row['binarySha256']=hashlib.sha256((dest/'server').read_bytes()).hexdigest();cmd=[sys.executable,str(root/'tests/e2e'/f'{suite}.py'),str(dest/'server'),str(dest/'consumer'),'--cases='+','.join(required)];row['consumerCommand']=cmd
  with (dest/'consumer.stdout').open('wb') as so,(dest/'consumer.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=root,stdout=so,stderr=se,timeout=240)
  row['consumerExit']=r.returncode;results=json.loads((dest/'consumer/results.json').read_text());row['failedCases']=[x['case'] for x in results if not x['pass']];row['detected']=r.returncode==1 and set(required)<=set(row['failedCases'])
 rows.append(row);(out/'results.json').write_text(json.dumps(rows,indent=2)+'\n');print(name,row,flush=True)
sys.exit(0 if bool(rows) and all(r['buildExit']==0 and r.get('detected') for r in rows) else 1)
