#!/usr/bin/env python3
"""Remove a required snippet spec in an isolated copy; actual check must detect it."""
import hashlib,json,pathlib,shutil,subprocess,sys,tempfile
root=pathlib.Path(__file__).resolve().parents[1]
binary=pathlib.Path(sys.argv[1]).resolve()
out=root/'artifacts/wf5-snippets-ablation';out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='wf-snippet-ablation-') as d:
 copy=pathlib.Path(d);(copy/'tests').mkdir(parents=True);(copy/'snippets').mkdir()
 shutil.copy2(root/'tests/snippets_e2e.py',copy/'tests/snippets_e2e.py')
 snippets=json.loads((root/'snippets/yaml.json').read_text())
 for snippet in snippets.values():
  if 'kind: Workflow' in snippet['body']:
   snippet['body']=snippet['body'][:snippet['body'].index('spec:')]
 (copy/'snippets/yaml.json').write_text(json.dumps(snippets,indent=2))
 command=[sys.executable,str(copy/'tests/snippets_e2e.py'),str(binary)]
 p=subprocess.run(command,capture_output=True,timeout=90)
 shutil.copytree(copy/'artifacts/wf5-snippets',out/'consumer',dirs_exist_ok=True)
 shutil.copy2(copy/'snippets/yaml.json',out/'mutant-snippets.json')
 (out/'stdout').write_bytes(p.stdout);(out/'stderr').write_bytes(p.stderr)
 results=json.loads((out/'consumer/results.json').read_text())
 detected=p.returncode==1 and any(x.get('kind')=='Workflow' and not x['pass'] for x in results)
 sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
 (out/'report.json').write_text(json.dumps({'command':command,'pythonVersion':sys.version,'exitCode':p.returncode,'binarySha256':sha(binary),'driverSha256':sha(pathlib.Path(__file__)),'mutation':'Remove required Workflow spec; no source/build mutation','detected':detected,'decision':'retain independent snippet consumer: detects invalid inserted resource'},indent=2))
 sys.exit(0 if detected else 1)
