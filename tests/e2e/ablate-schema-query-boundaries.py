#!/usr/bin/env python3
"""Compile two narrow removals and check ordinary exact consumer outcomes."""
import pathlib,sys,os,shutil,json,subprocess,hashlib
root=pathlib.Path(__file__).resolve().parents[2];out=pathlib.Path(sys.argv[1]).resolve();env=os.environ|{'GOWORK':'off','GOTOOLCHAIN':'local','CGO_ENABLED':'1'};rows=[]
for name in ['flow-context','key-closure']:
 dest=out/name;source=dest/'source';source.mkdir(parents=True,exist_ok=False)
 for folder in ['cmd','internal','compat','schemas','vendor']:shutil.copytree(root/folder,source/folder)
 for file in ['go.mod','go.sum']:shutil.copy2(root/file,source/file)
 if name=='flow-context':
  path=source/'internal/analysis/query_context.go';before=path.read_text();after=before.replace('offset, flowDepth)', 'offset, 0)');kind='actual';required=['multiline-flow-sibling','multiline-flow-sibling-crlf-comment','multiline-flow-array-object']
 else:
  path=source/'internal/analysis/queries.go';before=path.read_text();after=before.replace('if !queryAllowsKey(ctx, field.schema, path, field.value, 0) {','if false {');kind='synthetic';required=['closedConjunction-keys','closedRefConjunction-keys']
  shutil.copy2(root/'tests/fixtures/schema-query/contract.json',source/'internal/assets/schemas/96631f9b6d683112ccd0dd0dd7c28b3a59310400e3b2550e8cadb0a370410ac7.json')
 assert before!=after;path.write_text(after);(dest/'change.json').write_text(json.dumps({'file':str(path.relative_to(source)),'before':before,'after':after},indent=2)+'\n')
 cmd=[str(pathlib.Path(env['GOROOT'])/'bin/go'),'build','-mod=vendor','-trimpath','-buildvcs=false','-o',str(dest/'server'),'./cmd/argo-workflows-ls']
 with (dest/'build.stdout').open('wb') as so,(dest/'build.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=source,env=env,stdout=so,stderr=se)
 row={'variant':name,'buildCommand':cmd,'buildExit':r.returncode,'requiredFailures':required}
 if r.returncode==0:
  row['binarySha256']=hashlib.sha256((dest/'server').read_bytes()).hexdigest();cmd=[sys.executable,str(root/'tests/e2e/wf-schema-query-boundaries.py'),str(dest/'server'),str(dest/'consumer'),kind];row['consumerCommand']=cmd
  with (dest/'consumer.stdout').open('wb') as so,(dest/'consumer.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=root,stdout=so,stderr=se,timeout=240)
  row['consumerExit']=r.returncode
  results=json.loads((dest/'consumer/results.json').read_text());row['failedCases']=[x['case'] for x in results if not x['pass']];row['detected']=r.returncode==1 and set(required)<=set(row['failedCases'])
 rows.append(row);(out/'results.json').write_text(json.dumps(rows,indent=2)+'\n');print(name,row,flush=True)
sys.exit(0 if len(rows)==2 and all(r['buildExit']==0 and r.get('detected') for r in rows) else 1)
