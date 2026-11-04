#!/usr/bin/env python3
"""Real Helm3 input provenance, generated diagnostics, and stale input refusal."""
import pathlib,sys,json,subprocess,hashlib,shutil,os,time,signal
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();helm=pathlib.Path(sys.argv[2]).resolve();out=pathlib.Path(sys.argv[3]).resolve();out.mkdir(parents=True,exist_ok=True);results=[]
for name in ['valid','invalid','render-failure','changed-input','missing-tool','cancelled','deadline']:
 work=out/name;chart=work/'chart';chart.mkdir(parents=True,exist_ok=True);(chart/'templates').mkdir(exist_ok=True);(chart/'Chart.yaml').write_text('apiVersion: v2\nname: sample\nversion: 0.1.0\n');values=work/'values.yaml';values.write_text('image: alpine\n');template=chart/'templates/workflow.yaml';template.write_text('apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: demo}\nspec:\n  entrypoint: main\n'+('  parallelism: wrong\n' if name=='invalid' else '')+'  templates:\n    - name: main\n      container: {image: "{{ .Values.image }}"}\n');(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\n')
 if name=='render-failure':template.write_text('{{ fail "deliberate render failure" }}')
 tool=helm
 if name=='changed-input':
  tool=work/'helm-wrapper';tool.write_text('#!/usr/bin/env python3\nimport os,sys,pathlib\nif sys.argv[1]=="template":pathlib.Path('+repr(str(values))+').write_text("image: changed\\n")\nos.execv('+repr(str(helm))+',['+repr(str(helm))+']+sys.argv[1:])\n');tool.chmod(0o755)
 if name in ['cancelled','deadline']:
  tool=work/'helm-wrapper';tool.write_text('#!/usr/bin/env python3\nimport os,sys,time\nif sys.argv[1]=="template":time.sleep(31)\nos.execv('+repr(str(helm))+',['+repr(str(helm))+']+sys.argv[1:])\n');tool.chmod(0o755)
 if name=='missing-tool':tool=work/'missing'
 argv=[str(binary),'render-check','--root',str(work),'--chart',str(chart),'--values',str(values),'--helm-binary',str(tool),'--output',str(work/'generated')]
 if name=='cancelled':
  process=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE);time.sleep(.3);process.send_signal(signal.SIGINT);stdout_raw,stderr_raw=process.communicate(timeout=5);r=subprocess.CompletedProcess(argv,process.returncode,stdout_raw,stderr_raw)
 else:r=subprocess.run(argv,capture_output=True,timeout=40)
 (work/'stdout.raw').write_bytes(r.stdout);(work/'stderr.raw').write_bytes(r.stderr);report=json.loads((work/'generated/render-report.json').read_text()) if (work/'generated/render-report.json').exists() else {};stdout=json.loads(r.stdout);checks={'exit':r.returncode==({'valid':0,'invalid':1,'cancelled':3,'deadline':3}.get(name,2))}
 if name in ['valid','invalid']:checks['provenance']=bool(report.get('inputSha256')) and report.get('inputsUnchanged') is True
 if name=='invalid':checks['generated_diagnostic']=any(d['code']=='SCHEMA_TYPE' and d['uri']==(work/'generated/rendered.yaml').as_uri() for d in stdout.get('diagnostics',[]))
 if name=='changed-input':checks['stale_refused']=report.get('inputsUnchanged') is False and not (work/'generated/rendered.yaml').exists()
 if name in ['missing-tool','render-failure','cancelled','deadline']:checks['visible_reason']=bool(stdout.get('error'))
 if name in ['cancelled','deadline']:checks['no_stale_render']=not (work/'generated/rendered.yaml').exists()
 results.append({'case':name,'command':argv,'exitCode':r.returncode,'assertions':checks,'pass':all(checks.values())});print(name,results[-1]['pass'])
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'results.json').write_text(json.dumps(results,indent=2));(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'binarySha256':sha(binary),'helmSha256':sha(helm),'testSha256':sha(pathlib.Path(__file__)),'inputs':{str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file() and ('chart' in p.parts or p.name=='values.yaml')}},indent=2));sys.exit(0 if all(x['pass'] for x in results) else 1)
