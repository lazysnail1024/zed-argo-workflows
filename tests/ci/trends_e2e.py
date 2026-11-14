#!/usr/bin/env python3
"""Real timing CLI controls; no product budgets or successful tests are invented."""
import argparse, hashlib, json, pathlib, subprocess, sys
p=argparse.ArgumentParser();p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
script=pathlib.Path(__file__).with_name('trends.py');rows=[]
for name,seconds,expected in [('valid',1.25,0),('missing','absent',1),('null',None,1),('negative',-1,1),('nan',float('nan'),1),('infinity',float('inf'),1),('boolean',True,1),('string','1.25',1)]:
 folder=a.output/name;inputs=folder/'inputs';inputs.mkdir(parents=True)
 record={'started':True,'timedOut':False,'exitCode':7}
 if seconds!='absent':record['seconds']=seconds
 (inputs/'command.json').write_text(json.dumps(record));cmd=[sys.executable,str(script),'--inputs',str(inputs),'--output',str(folder/'summary.json')]
 r=subprocess.run(cmd,capture_output=True);(folder/'stdout.log').write_bytes(r.stdout);(folder/'stderr.log').write_bytes(r.stderr)
 assertions={'expectedExit':r.returncode==expected}
 if expected==0:
  summary=json.loads((folder/'summary.json').read_text());assertions|={'exactTotal':summary['totalCommandSeconds']==1.25,'failurePreserved':len(summary['firstFailures'])==1 and summary['firstFailures'][0]['exitCode']==7}
 rows.append({'case':name,'command':cmd,'exitCode':r.returncode,'assertions':assertions,'pass':all(assertions.values())})
 (a.output/'results.json').write_text(json.dumps(rows,indent=2))
(a.output/'manifest.json').write_text(json.dumps({'python':sys.version,'command':sys.argv,'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'runnerSha256':hashlib.sha256(script.read_bytes()).hexdigest(),'scope':'timing verifier only'},indent=2))
raise SystemExit(0 if all(row['pass'] for row in rows) else 1)
