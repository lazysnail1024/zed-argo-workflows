#!/usr/bin/env python3
"""Compiled removals of read-only recovery, visibility, and lexical selection."""
import pathlib,sys,os,shutil,json,subprocess,hashlib
root=pathlib.Path(__file__).resolve().parents[2];out=pathlib.Path(sys.argv[1]).resolve();env=os.environ|{'GOWORK':'off','GOTOOLCHAIN':'local','CGO_ENABLED':'1'};rows=[]
for name in ['visibility','recovery','lexical-shadowing','ambiguous-union','overlap-identity','origin-cardinality']:
 if len(sys.argv)>2 and name not in sys.argv[2:]:continue
 dest=out/name;source=dest/'source';source.mkdir(parents=True,exist_ok=False)
 for folder in ['cmd','internal','compat','schemas','vendor']:shutil.copytree(root/folder,source/folder)
 for file in ['go.mod','go.sum']:shutil.copy2(root/file,source/file)
 if name=='visibility':
  path=source/'internal/analysis/domain_queries.go';before=path.read_text();after=before.replace('constrained && !visible[key]', 'false && constrained && !visible[key]',1);suite='wf-domain-boundaries';required=['parallel-peer']
 elif name=='recovery':
  path=source/'internal/analysis/localrename.go';before=path.read_text();after=before.replace('if b.readOnly && b.ctx.Err() == nil {','if false && b.readOnly && b.ctx.Err() == nil {',1);suite='wf-domain-boundaries';required=['unrelated-malformed-expression']
 elif name=='origin-cardinality':
  path=source/'internal/analysis/domain_queries.go';before=path.read_text();after=before.replace(' || b.declarationCount[key] != len(b.declarations[key])','',1).replace(' || b.declarationCount[candidate] != 1','',1);suite='wf-domain-inexact-cardinality';required=['local-definition','local-completion','local-alias-definition','local-alias-completion']
 elif name=='overlap-identity':
  path=source/'internal/analysis/localrename.go';before=path.read_text();after=before.replace('b.queryPositions[span][key] = true','b.queryPositions[span] = map[renameKey]bool{key:true}',1);suite='wf-domain-overlap';required=['overlap','workflow']
 elif name=='ambiguous-union':
  path=source/'internal/analysis/external.go';before=path.read_text();after=before.replace('if len(targets) != 1 {','if false && len(targets) != 1 {',1);suite='wf-domain-ambiguous-references';required=['useTrue','declarationTrue']
 else:
  path=source/'internal/analysis/expressionreferences.go';before=path.read_text();after=before.replace('if shadowed[node] {','if shadowed[node] && false {',1);suite='wf-domain-queries';required=['Workflow','CronWorkflow','WorkflowTemplate','ClusterWorkflowTemplate']
 assert before!=after;path.write_text(after);(dest/'change.json').write_text(json.dumps({'file':str(path.relative_to(source)),'before':before,'after':after},indent=2)+'\n')
 cmd=[str(pathlib.Path(env['GOROOT'])/'bin/go'),'build','-mod=vendor','-trimpath','-buildvcs=false','-o',str(dest/'server'),'./cmd/argo-workflows-ls']
 with (dest/'build.stdout').open('wb') as so,(dest/'build.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=source,env=env,stdout=so,stderr=se)
 row={'variant':name,'buildCommand':cmd,'buildExit':r.returncode,'requiredFailures':required}
 if r.returncode==0:
  row['binarySha256']=hashlib.sha256((dest/'server').read_bytes()).hexdigest();cmd=[sys.executable,str(root/'tests/e2e'/f'{suite}.py'),str(dest/'server'),str(dest/'consumer')];row['consumerCommand']=cmd
  with (dest/'consumer.stdout').open('wb') as so,(dest/'consumer.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=root,stdout=so,stderr=se,timeout=240)
  row['consumerExit']=r.returncode;results=json.loads((dest/'consumer/results.json').read_text());row['failedCases']=[x['case'] for x in results if not x['pass']];row['detected']=r.returncode==1 and set(required)<=set(row['failedCases'])
 rows.append(row);(out/'results.json').write_text(json.dumps(rows,indent=2)+'\n');print(name,row,flush=True)
sys.exit(0 if bool(rows) and all(r['buildExit']==0 and r.get('detected') for r in rows) else 1)
