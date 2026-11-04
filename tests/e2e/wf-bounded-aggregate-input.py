#!/usr/bin/env python3
"""Many individually legal files must terminate before exhausting the process budget."""
import pathlib,sys,os,subprocess,time,signal,json,hashlib
binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True);cgpath=pathlib.Path('/proc/self/cgroup').read_text().strip().split('::',1)[1];cg=pathlib.Path('/sys/fs/cgroup')/cgpath.lstrip('/');assert int((cg/'memory.max').read_text())<=512*1024**2
work=out/'input';work.mkdir(exist_ok=True);(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\n');text=b'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: large}\nspec: {templates: [{name: main, container: {image: alpine}}]}\n';text+=b'#'+b'x'*(5*1024**2-len(text)-2)+b'\n';first=work/'000.yaml';first.write_bytes(text)
for i in range(1,100):os.link(first,work/('%03d.yaml'%i))
cmd=[str(binary),'check','--root',str(work),'--format','json'];start=time.monotonic();peak=0;killed=False
with (out/'stdout.raw').open('wb') as so,(out/'stderr.raw').open('wb') as se:
 p=subprocess.Popen(cmd,stdout=so,stderr=se,start_new_session=True);identity=pathlib.Path('/proc/%d/stat'%p.pid).read_text().split(') ',1)[1].split()[19]
 while p.poll() is None:
  peak=max(peak,int((cg/'memory.current').read_text()))
  if peak>384*1024**2 or time.monotonic()-start>7:
   try:
    if pathlib.Path('/proc/%d/stat'%p.pid).read_text().split(') ',1)[1].split()[19]==identity:os.killpg(p.pid,signal.SIGKILL);killed=True
   except OSError:pass
   break
  time.sleep(.005)
 p.wait()
try:r=json.loads((out/'stdout.raw').read_text())
except ValueError:r={}
checks={'boundedTerminal':not killed and p.returncode in [2,3] and bool(r),'explicitReason':any(any(w in str(x).lower() for w in ['deadline','bytes','mib','budget']) for x in r.get('configurationErrors',[])+r.get('notEvaluated',[]) if 'not yet implemented' not in str(x)),'noIncompleteCounts':r.get('filesAnalyzed',100)<100};sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'results.json').write_text(json.dumps({'command':cmd,'exitCode':p.returncode,'seconds':time.monotonic()-start,'peakCgroupBytes':peak,'harnessKilled':killed,'assertions':checks,'pass':all(checks.values()),'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'inputFileSha256':sha(first),'fileCount':100,'logicalYamlBytes':100*len(text),'physicalFixture':'100 hardlinks to one 5 MiB file; each input URI is distinct.','cgroup':cgpath,'memory.max':(cg/'memory.max').read_text().strip()},indent=2)+'\n');sys.exit(0 if all(checks.values()) else 1)
