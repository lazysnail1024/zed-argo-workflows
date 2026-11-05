#!/usr/bin/env python3
import pathlib,json,hashlib,sys
root=pathlib.Path(__file__).resolve().parents[2]; out=pathlib.Path(sys.argv[2]).resolve() if len(sys.argv)>2 else root/'artifacts/wf2-assets';out.mkdir(parents=True,exist_ok=True)
e=json.loads((root/'tests/e2e/wf2.expected.json').read_text()); results=[]
try:
 c=json.loads((root/'compat/catalog.lock.json').read_text()); entries=c['versions']; results.append({'assertion':'nonzero distinct stable versions','pass':len(entries)>0 and len({v['version'] for v in entries})==len(entries) and all(v['version'].startswith('3.') and '-' not in v['version'] for v in entries)})
 for v in entries:
  for s in v['sources']:
   p=root/s['path']; results.append({'version':v['version'],'assertion':'source hash '+s['path'],'pass':p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==s['sha256']})
 for version,present in e['cronSchedules'].items():
  v=next(v for v in entries if v['version']==version); s=next(s for s in v['sources'] if s['kind']=='CronWorkflow'); text=(root/s['path']).read_text(); results.append({'version':version,'assertion':'CronWorkflow schedules availability','pass':('\n              schedules:' in text)==present})
except Exception as err: results.append({'assertion':'assets load','pass':False,'error':str(err)})
(out/'results.json').write_text(json.dumps(results,indent=2)); (out/'manifest.json').write_text(json.dumps({'command':sys.argv,'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'expectedSha256':hashlib.sha256((root/'tests/e2e/wf2.expected.json').read_bytes()).hexdigest(),'scope':'source asset integrity only; runtime validation not yet accepted'},indent=2));sys.exit(0 if results and all(r['pass'] for r in results) else 1)
