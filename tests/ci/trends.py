#!/usr/bin/env python3
"""Summarize raw CI command timings without replacing product budget assertions."""
import argparse, hashlib, json, math, pathlib, statistics
p=argparse.ArgumentParser();p.add_argument('--inputs',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args()
rows=[]
for file in sorted(a.inputs.rglob('command.json')):
 value=json.loads(file.read_text());rows.append({'path':str(file.relative_to(a.inputs)),'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'seconds':value.get('seconds'),'exitCode':value.get('exitCode'),'failureClass':'timeout' if value.get('timedOut') else 'startup' if not value.get('started') else 'command' if value.get('exitCode')!=0 else None})
assert rows,'no timing samples'
for row in rows:
 seconds=row['seconds']
 if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<0:
  raise ValueError('missing or invalid command timing: '+row['path'])
a.output.parent.mkdir(parents=True,exist_ok=True);assert not a.output.exists(),'never overwrite first-run observations'
a.output.write_text(json.dumps({'schemaVersion':1,'samples':rows,'sampleCount':len(rows),'totalCommandSeconds':sum(r['seconds'] for r in rows),'comparisonStatus':'insufficient: requires five warm and three cold complete runs plus hosted observations','productBudgetStatus':'original consumer assertions are authoritative; unchanged','firstFailures':[r for r in rows if r['failureClass']]},indent=2)+'\n')
