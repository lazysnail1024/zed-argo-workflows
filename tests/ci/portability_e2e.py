#!/usr/bin/env python3
"""Verifier portability controls from original native consumer reports."""
import argparse,copy,hashlib,json,pathlib,subprocess,sys
p=argparse.ArgumentParser();p.add_argument('--native',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
root=pathlib.Path(__file__).resolve().parents[2];rows=[]
for name,key in [('wf-persistent-workspace','checks'),('wf-persistent-slowio','assertions')]:
 source=a.native/name/'results.json';original=json.loads(source.read_text());original=original if isinstance(original,list) else [original]
 assert all(row['pass'] is True and row[key]['peak_rss_under_512_mib'] is True for row in original)
 for platform,omit_rss,omit_semantics,expected in [('linux/amd64',False,False,0),('darwin/arm64',True,False,0),('windows/amd64',True,False,0),('linux/amd64',True,False,1),('darwin/arm64',True,True,1)]:
  tag=name+'-'+platform.replace('/','-')+('-no-rss' if omit_rss else '')+('-no-semantics' if omit_semantics else '');folder=a.output/tag;folder.mkdir();report=copy.deepcopy(original)
  for row in report:
   if omit_rss:row[key].pop('peak_rss_under_512_mib')
   if omit_semantics:row[key].pop('clean_shutdown')
  fixture=folder/'results.json';fixture.write_text(json.dumps(report))
  code='import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import run;run.verify_result_contract(sys.argv[2],Path(sys.argv[3]),producer_platform=sys.argv[4])'
  cmd=[sys.executable,'-c',code,str(root/'tests/ci'),name,str(fixture),platform];r=subprocess.run(cmd,capture_output=True);(folder/'stdout.log').write_bytes(r.stdout);(folder/'stderr.log').write_bytes(r.stderr)
  rows.append({'case':tag,'command':cmd,'originalReport':str(source),'originalSha256':hashlib.sha256(source.read_bytes()).hexdigest(),'expectedExit':expected,'exitCode':r.returncode,'pass':r.returncode==expected})
  (a.output/'report.json').write_text(json.dumps({'scope':'aggregate verifier controls; no macOS or Windows product execution','cases':rows,'passed':all(row['pass'] for row in rows)},indent=2))
raise SystemExit(0 if all(row['pass'] for row in rows) else 1)
