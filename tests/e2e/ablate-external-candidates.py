#!/usr/bin/env python3
"""Compiled candidate removal and ambiguous-reference contamination checks."""
from pathlib import Path
import sys,os,subprocess,shutil,json,hashlib
root=Path(__file__).resolve().parents[2];out=Path(sys.argv[1]).resolve();env=os.environ|{'GOWORK':'off','GOTOOLCHAIN':'local','CGO_ENABLED':'1','GOPROXY':'off','GOSUMDB':'off'};rows=[]
for variant in ['candidate-navigation','candidate-reference-contamination']:
 dest=out/variant;source=dest/'source';source.mkdir(parents=True,exist_ok=False)
 for folder in ['cmd','internal','compat','schemas','vendor']:shutil.copytree(root/folder,source/folder)
 for name in ['go.mod','go.sum']:shutil.copy2(root/name,source/name)
 p=source/'internal/analysis/external.go';before=p.read_text()
 if variant=='candidate-navigation':
  after=before.replace('if query != nil && query.definitions &&','if false && query != nil && query.definitions &&',1);suites={'wf-external-candidates':['resource-duplicates','template-duplicates','encoded-resource-duplicate']}
 else:
  after=before.replace('query := &externalQuery{offset: -1, references: true, uri: uri, position: pos}\n\t_, bindings, _, _ := external(ctx, w, query)', 'query := &externalQuery{offset: -1, definitions: true, uri: uri, position: pos}\n\t_, bindings, _, _ := external(ctx, w, query)\n\tbindings = append(bindings, query.candidates...)',1);suites={'wf-external-candidates':['encoded-resource-duplicate','encoded-template-duplicate']}
 assert after!=before;p.write_text(after);(dest/'change.json').write_text(json.dumps({'file':str(p.relative_to(source)),'before':before,'after':after},indent=2)+'\n');cmd=[str(Path(env['GOROOT'])/'bin/go'),'build','-mod=vendor','-trimpath','-buildvcs=false','-o',str(dest/'native'),'./cmd/argo-workflows-ls']
 with (dest/'build.stdout').open('wb') as so,(dest/'build.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=source,env=env,stdout=so,stderr=se)
 row={'variant':variant,'buildCommand':cmd,'buildExit':r.returncode,'consumers':[],'detected':False}
 if r.returncode==0:
  row['nativeSha256']=hashlib.sha256((dest/'native').read_bytes()).hexdigest()
  for suite,required in suites.items():
   cmd=[sys.executable,str(root/'tests/e2e'/f'{suite}.py'),str(dest/'native'),str(dest/suite)]
   with (dest/(suite+'.stdout')).open('wb') as so,(dest/(suite+'.stderr')).open('wb') as se:r=subprocess.run(cmd,env=env,stdout=so,stderr=se,timeout=180)
   results=json.loads((dest/suite/'results.json').read_text());failed=[x['case'] for x in results if not x['pass']];row['consumers'].append({'command':cmd,'exitCode':r.returncode,'requiredFailures':required,'failedCases':failed,'detected':r.returncode==1 and set(required)<=set(failed)})
  row['detected']=all(x['detected'] for x in row['consumers'])
 rows.append(row);(out/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
sys.exit(0 if len(rows)==2 and all(x['buildExit']==0 and x['detected'] for x in rows) else 1)
