#!/usr/bin/env python3
"""Nonregular config must not block CLI or defeat an already-open overlay."""
import pathlib,subprocess,sys,os,json,time,hashlib,importlib.util,signal
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True);rows=[]
for mode in ['fifo','directory','oversize']:
 folder=out/mode;folder.mkdir(exist_ok=True);config=folder/'.argo-workflows-ls.yaml'
 if mode.startswith('fifo'):os.mkfifo(config)
 elif mode=='directory':config.mkdir()
 else:config.write_bytes(b'#'+b'x'*(1024*1024)+b'\n')
 cmd=[str(binary),'check','--root',str(folder),'--format','json'];start=time.monotonic();p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
 killed=False
 try:stdout,stderr=p.communicate(timeout=2)
 except subprocess.TimeoutExpired:killed=True;os.killpg(p.pid,signal.SIGKILL);stdout,stderr=p.communicate()
 (folder/'stdout.raw').write_bytes(stdout);(folder/'stderr.raw').write_bytes(stderr)
 try:report=json.loads(stdout)
 except ValueError:report={}
 checks={'terminalWithoutHarnessKill':not killed,'exit':p.returncode==2,'explicitFailure':report.get('analysisComplete') is False and bool(report.get('configurationErrors') or report.get('notEvaluated'))}
 rows.append({'case':mode,'command':cmd,'exitCode':p.returncode,'seconds':time.monotonic()-start,'assertions':checks,'pass':all(checks.values())});(out/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
sys.argv=[sys.argv[0],str(binary),str(out/'overlay')];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
folder=out/'overlay';folder.mkdir(exist_ok=True);os.mkfifo(folder/'.argo-workflows-ls.yaml');file=folder/'case.yaml';text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: overlay}\nspec: {parallelism: wrong}\n';file.write_text(text);client=None
try:
 client=m.LSP(folder,folder);client.send('textDocument/didOpen',{'textDocument':{'uri':(folder/'.argo-workflows-ls.yaml').as_uri(),'languageId':'yaml','version':1,'text':'schemaVersion: 1\ntargetVersion: "3.6.0"\n'}});ds=client.open(file,text);rows.append({'case':'overlay-before-fifo-read','pass':any(d['code']=='SCHEMA_TYPE' for d in ds),'diagnostics':ds})
except Exception as e:rows.append({'case':'overlay-before-fifo-read','pass':False,'error':repr(e)})
finally:
 if client:client.close()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'results.json').write_text(json.dumps(rows,indent=2)+'\n');(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'python':sys.version},indent=2)+'\n')
sys.exit(0 if len(rows)==4 and all(r['pass'] for r in rows) else 1)
