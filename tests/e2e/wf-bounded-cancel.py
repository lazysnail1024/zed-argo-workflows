#!/usr/bin/env python3
"""CLI cancellation is terminal JSON even before input scan finishes."""
import pathlib,sys,subprocess,time,signal,json,hashlib
binary=pathlib.Path(sys.argv[1]).resolve();work=pathlib.Path(sys.argv[2]).resolve();out=pathlib.Path(sys.argv[3]).resolve();out.mkdir(parents=True,exist_ok=True)
cmd=[str(binary),'check','--root',str(work),'--format','json'];p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE);time.sleep(.05);start=time.monotonic();p.send_signal(signal.SIGINT)
killed=False
try:so,se=p.communicate(timeout=2)
except subprocess.TimeoutExpired:killed=True;p.kill();so,se=p.communicate()
(out/'stdout.raw').write_bytes(so);(out/'stderr.raw').write_bytes(se)
try:r=json.loads(so)
except ValueError:r={}
checks={'exit3':p.returncode==3,'terminalJSON':r.get('analysisComplete') is False,'explicitReason':any('cancel' in str(x).lower() for x in r.get('notEvaluated',[])),'bounded':not killed and time.monotonic()-start<=.2,'noInflatedCounts':0<=r.get('filesAnalyzed',-1)<10000}
(out/'report.json').write_text(json.dumps({'command':cmd,'exitCode':p.returncode,'assertions':checks,'passed':all(checks.values()),'binarySha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()},indent=2)+'\n');sys.exit(0 if all(checks.values()) else 1)
