#!/usr/bin/env python3
"""Compile narrow query removals against independent ordinary consumer cases."""
import pathlib,sys,os,shutil,json,subprocess,hashlib
root=pathlib.Path(__file__).resolve().parents[2];out=pathlib.Path(sys.argv[1]).resolve();env=os.environ|{'GOWORK':'off','GOTOOLCHAIN':'local','CGO_ENABLED':'1'};rows=[]
for name in ['exclusive-cardinality','marker-identity']:
 if len(sys.argv)>2 and name not in sys.argv[2:]:continue
 dest=out/name;source=dest/'source';source.mkdir(parents=True,exist_ok=False)
 for folder in ['cmd','internal','compat','schemas','vendor']:shutil.copytree(root/folder,source/folder)
 for file in ['go.mod','go.sum']:shutil.copy2(root/file,source/file)
 if name=='exclusive-cardinality':
  path=source/'internal/analysis/query_schema.go';before=path.read_text();after=before.replace('for i, branches := range [][]*jsonschema.Schema{s.AnyOf, s.OneOf}', 'for _, branches := range [][]*jsonschema.Schema{s.AnyOf, s.OneOf}').replace('matches == 0 || i == 1 && matches != 1','matches == 0')
  shutil.copy2(root/'tests/fixtures/schema-query/contract.json',source/'internal/assets/schemas/96631f9b6d683112ccd0dd0dd7c28b3a59310400e3b2550e8cadb0a370410ac7.json');suite='wf-schema-query-contract'
 else:
  path=source/'internal/analysis/query_context.go';before=path.read_text();after=before.replace(' && m.Key.Span().Start == markerStart','').replace('m.Value.Span().Start == markerStart && ','').replace('n.Span().Start == markerStart && ','');suite='wf-schema-queries'
 assert before!=after;path.write_text(after);(dest/'change.json').write_text(json.dumps({'file':str(path.relative_to(source)),'before':before,'after':after},indent=2)+'\n')
 cmd=[str(pathlib.Path(env['GOROOT'])/'bin/go'),'build','-mod=vendor','-trimpath','-buildvcs=false','-o',str(dest/'server'),'./cmd/argo-workflows-ls']
 with (dest/'build.stdout').open('wb') as so,(dest/'build.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=source,env=env,stdout=so,stderr=se)
 row={'variant':name,'buildCommand':cmd,'buildExit':r.returncode,'suite':suite}
 if r.returncode==0:
  row['binarySha256']=hashlib.sha256((dest/'server').read_bytes()).hexdigest();cmd=[sys.executable,str(root/'tests/e2e'/f'{suite}.py'),str(dest/'server'),str(dest/'consumer')];row['consumerCommand']=cmd
  with (dest/'consumer.stdout').open('wb') as so,(dest/'consumer.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=root,stdout=so,stderr=se,timeout=240)
  row['consumerExit']=r.returncode
 rows.append(row);(out/'results.json').write_text(json.dumps(rows,indent=2)+'\n');print(name,row,flush=True)
sys.exit(0 if bool(rows) and all(r['buildExit']==0 and r.get('consumerExit')==1 for r in rows) else 1)
