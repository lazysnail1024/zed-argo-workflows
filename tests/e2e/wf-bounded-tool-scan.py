#!/usr/bin/env python3
"""Signals during input scanning are terminal JSON before either tool launches."""
import pathlib,subprocess,sys,time,signal,json,hashlib
binary=pathlib.Path(sys.argv[1]).resolve();work=pathlib.Path(sys.argv[2]).resolve();out=pathlib.Path(sys.argv[3]).resolve();out.mkdir(parents=True,exist_ok=True);rows=[]
for tool in ['helm','argo']:
 folder=out/tool;folder.mkdir(exist_ok=True);marker=folder/'launched';helper=folder/'tool';helper.write_text('#!/usr/bin/python3\nimport pathlib,time\npathlib.Path('+repr(str(marker))+').write_text("started")\ntime.sleep(30)\n');helper.chmod(0o755);chart=folder/'chart';chart.mkdir(exist_ok=True);(chart/'Chart.yaml').write_text('apiVersion: v2\nname: sample\nversion: 0.1.0\n')
 cmd=[str(binary),'render-check','--root',str(work),'--chart',str(chart),'--helm-binary',str(helper),'--output',str(folder/'output')] if tool=='helm' else [str(binary),'lint-official','--root',str(work),'--argo-binary',str(helper)]
 p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True);time.sleep(.03);start=time.monotonic();p.send_signal(signal.SIGINT)
 try:so,se=p.communicate(timeout=2)
 except subprocess.TimeoutExpired:import os;os.killpg(p.pid,signal.SIGKILL);so,se=p.communicate()
 (folder/'stdout.raw').write_bytes(so);(folder/'stderr.raw').write_bytes(se)
 try:r=json.loads(so)
 except ValueError:r={}
 checks={'exit3':p.returncode==3,'beforeTool':not marker.exists(),'terminalJSON':r.get('analysisComplete') is False and 'cancel' in r.get('error',''),'prompt':time.monotonic()-start<=.2};rows.append({'case':tool,'command':cmd,'exitCode':p.returncode,'assertions':checks,'pass':all(checks.values())})
(out/'results.json').write_text(json.dumps(rows,indent=2)+'\n');(out/'manifest.json').write_text(json.dumps({'binarySha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()},indent=2)+'\n');sys.exit(0 if all(x['pass'] for x in rows) else 1)
