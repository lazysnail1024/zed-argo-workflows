#!/usr/bin/env python3
"""Both explicit tools must terminate, reclaim descendants and retain failures."""
import pathlib,sys,os,subprocess,time,signal,json,hashlib
binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
cgpath=pathlib.Path('/proc/self/cgroup').read_text().strip().split('::',1)[1];cg=pathlib.Path('/sys/fs/cgroup')/cgpath.lstrip('/');limit=(cg/'memory.max').read_text().strip();assert limit!='max' and int(limit)<=512*1024**2,'adversarial helpers require a real <=512MiB cgroup'
selected=set(filter(None,os.environ.get("WF_PROCESS_CASES","").split(",")));rows=[]
def alive(identity):
 try:
  fields=pathlib.Path('/proc/%s/stat'%identity[0]).read_text().split(') ',1)[1].split();return fields[0]!='Z' and fields[19]==identity[1]
 except OSError:return False
for tool in ['helm','argo']:
 for mode in ['nonzero','inherited-pipe','flood','cancel','deadline','version-cancel','help-cancel']:
  if tool=='helm' and mode=='help-cancel':continue
  name=tool+'-'+mode
  if selected and name not in selected:continue
  folder=out/name;folder.mkdir(exist_ok=True);work=folder/'input';work.mkdir(exist_ok=True);(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\n');(work/'case.yaml').write_text('apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: sample}\nspec: {templates: [{name: main, container: {image: alpine}}]}\n');chart=folder/'chart';chart.mkdir(exist_ok=True);(chart/'Chart.yaml').write_text('apiVersion: v2\nname: sample\nversion: 0.1.0\n');pidfile=folder/'pids.json';helper=folder/'tool'
  helper.write_text('#!/usr/bin/python3\nimport os,sys,time,subprocess,pathlib,json\nmode='+repr(mode)+'\ntool='+repr(tool)+'\nif sys.argv[1]=="version" and mode!="version-cancel":\n print("v3.19.0" if tool=="helm" else "argo: v3.6.0");sys.exit(0)\nif "--help" in sys.argv and mode!="help-cancel":\n print("--offline");sys.exit(0)\nprint("raw-start",flush=True);print("raw-error",file=sys.stderr,flush=True)\nif mode=="nonzero":sys.exit(7)\nchild=subprocess.Popen([sys.executable,"-c","import time;time.sleep(120)"])\npathlib.Path('+repr(str(pidfile))+').write_text(json.dumps([[p,pathlib.Path(f"/proc/{p}/stat").read_text().split(") ",1)[1].split()[19]] for p in [os.getpid(),child.pid]]))\nif mode=="inherited-pipe":sys.exit(0)\nif mode=="flood":\n for _ in range(96):\n  os.write(1,b"x"*65536);os.write(2,b"y"*65536)\n sys.exit(7)\ntime.sleep(120)\n');helper.chmod(0o755)
  cmd=[str(binary),'render-check','--root',str(work),'--chart',str(chart),'--helm-binary',str(helper),'--output',str(folder/'generated')] if tool=='helm' else [str(binary),'lint-official','--root',str(work),'--argo-binary',str(helper)]
  start=time.monotonic();peak=0;killed=False;sent=False
  with (folder/'stdout.raw').open('wb') as so,(folder/'stderr.raw').open('wb') as se:
   p=subprocess.Popen(cmd,stdout=so,stderr=se,start_new_session=True)
   while p.poll() is None:
    try:peak=max(peak,int((cg/'memory.current').read_text()))
    except OSError:pass
    if peak>384*1024**2 or time.monotonic()-start>34:killed=True;os.killpg(p.pid,signal.SIGKILL);break
    if mode in ['cancel','version-cancel','help-cancel'] and pidfile.exists() and not sent:p.send_signal(signal.SIGINT);sent=True
    time.sleep(.01)
   p.wait();identities=json.loads(pidfile.read_text()) if pidfile.exists() else [];survivors=[x for x in identities if alive(x)]
   for identity in survivors:
    if alive(identity):os.kill(identity[0],signal.SIGKILL)
  try:r=json.loads((folder/'stdout.raw').read_text())
  except ValueError:r={}
  expected=3 if mode in ['cancel','deadline','version-cancel','help-cancel'] else 2;rawout=r.get('stdout','');rawerr=r.get('stderr','')
  checks={'terminalExit':p.returncode==expected and not killed,'treeReclaimed':not survivors,'terminalJSON':r.get('analysisComplete') is False and bool(r.get('error')),'rawFailureRetained':'raw-start' in rawout and 'raw-error' in rawerr,'combinedOutputBound':len(rawout.encode())+len(rawerr.encode())<=10*1024**2,'nonzeroPreserved':mode!='nonzero' or r.get('toolExitCode')==7,'noGeneratedYAML':not(folder/'generated/rendered.yaml').exists()}
  rows.append({'case':name,'command':cmd,'exitCode':p.returncode,'seconds':time.monotonic()-start,'peakCgroupBytes':peak,'harnessKilled':killed,'survivors':survivors,'assertions':checks,'pass':all(checks.values())});(out/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'manifest.json').write_text(json.dumps({'cgroup':cgpath,'memory.max':limit,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'command':sys.argv},indent=2)+'\n');sys.exit(0 if len(rows)==(len(selected) if selected else 13) and all(r['pass'] for r in rows) else 1)
