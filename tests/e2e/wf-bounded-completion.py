#!/usr/bin/env python3
import pathlib,sys,subprocess,json,hashlib
A=pathlib.Path(__file__).resolve().parent;B=pathlib.Path(sys.argv[1]).resolve();O=pathlib.Path(sys.argv[2]).resolve();O.mkdir(parents=True,exist_ok=False)
good='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: complete}\nspec: {templates: [{name: main, container: {image: alpine}}]}\n'
bad='apiVersion: [\n'
foreign='apiVersion: v1\nkind: ConfigMap\nmetadata: {name: foreign}\ndata: {parallelism: wrong}\n'
rows=[]
for label,files,expected_files,expected_resources,incomplete in [('damaged-gvk',{'case.yaml':bad},0,0,True),('mixed-damaged-gvk',{'case.yaml':good+'---\n'+bad},0,1,True),('separate-damaged-gvk',{'a.yaml':good,'b.yaml':bad},1,1,True),('valid-foreign',{'case.yaml':foreign},1,0,False),('mixed-valid-foreign',{'case.yaml':good+'---\n'+foreign},1,1,False),('damaged-foreign',{'case.yaml':foreign+'broken: [\n'},0,0,True),('valid-unknown-gvk',{'case.yaml':'apiVersion: []\n'},1,0,False)]:
 d=O/label;w=d/'input';w.mkdir(parents=True);(w/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\n')
 for n,t in files.items():(w/n).write_text(t)
 cmd=[str(B),'check','--root',str(w),'--format','json'];p=subprocess.run(cmd,capture_output=True,timeout=10);(d/'stdout.raw').write_bytes(p.stdout);(d/'stderr.raw').write_bytes(p.stderr);r=json.loads(p.stdout)
 checks={'completionExit':p.returncode==(2 if incomplete else 0),'exactFiles':r['filesAnalyzed']==expected_files,'exactResources':r['resourcesAnalyzed']==expected_resources,'explicitIncompleteInput':any('incomplete' in x.lower() or 'syntax' in x.lower() for x in r['notEvaluated'] if 'not yet implemented' not in x)==incomplete,'noProductSyntaxDiagnostic':r['diagnostics']==[]}
 rows.append({'case':label,'command':cmd,'exitCode':p.returncode,'expectedFiles':expected_files,'expectedResources':expected_resources,'assertions':checks,'pass':all(checks.values()),'report':r});(O/'results.json').write_text(json.dumps(rows,indent=2)+'\n');print(label,rows[-1]['pass'],flush=True)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(O/'manifest.json').write_text(json.dumps({'command':sys.argv,'binarySha256':sha(B),'testSha256':sha(pathlib.Path(__file__)),'python':sys.version,'inputHashes':{str(p.relative_to(O)):sha(p) for p in O.rglob('*.yaml')}},indent=2)+'\n');sys.exit(0 if all(r['pass'] for r in rows) else 1)
