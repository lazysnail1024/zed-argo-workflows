#!/usr/bin/env python3
import pathlib,json,hashlib,re,sys
root=pathlib.Path(__file__).resolve().parents[2]; out=pathlib.Path(sys.argv[2]).resolve() if len(sys.argv)>2 else root/'artifacts/wf2-assets';out.mkdir(parents=True,exist_ok=True)
e=json.loads((root/'tests/e2e/wf2.expected.json').read_text()); results=[]
try:
 c=json.loads((root/'compat/catalog.lock.json').read_text()); entries=c['versions']; versions=[v['version'] for v in entries]
 tags={line.split()[1].removeprefix('refs/tags/') for line in (root/'compat/upstream/tags.txt').read_text().splitlines()}
 final_versions={tag[1:] for tag in tags if re.fullmatch(r'v\d+\.\d+\.\d+',tag)}
 latest=max(tuple(map(int,v.split('.'))) for v in final_versions); majors=[latest[0]-1,latest[0]]
 expected=sorted((v for v in final_versions if int(v.split('.')[0]) in majors),key=lambda v:tuple(map(int,v.split('.'))))
 results.append({'assertion':'every final version tag in the latest two majors exactly once','pass':bool(versions) and versions==expected and c['supportedMajors']==majors})
 results.append({'assertion':'automatic default','pass':c['defaultVersion']=='auto'})
 results.append({'assertion':'all official CRD kinds','pass':set(c['supportedKinds'])==set(e['kindSet'])=={s['kind'] for v in entries for s in v['sources']}})
 for v in entries:
  for s in v['sources']:
   p=root/s['path']; results.append({'version':v['version'],'assertion':'source hash '+s['path'],'pass':p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==s['sha256']})
 for version,present in e['cronSchedules'].items():
  v=next(v for v in entries if v['version']==version); s=next(s for s in v['sources'] if s['kind']=='CronWorkflow'); text=(root/s['path']).read_text(); results.append({'version':version,'assertion':'CronWorkflow schedules availability','pass':('\n              schedules:' in text)==present})
except Exception as err: results.append({'assertion':'assets load','pass':False,'error':str(err)})
for row in results: row['assertions']={row['assertion']:row['pass']}
(out/'results.json').write_text(json.dumps(results,indent=2)); (out/'manifest.json').write_text(json.dumps({'command':sys.argv,'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'expectedSha256':hashlib.sha256((root/'tests/e2e/wf2.expected.json').read_bytes()).hexdigest(),'tagInventorySha256':hashlib.sha256((root/'compat/upstream/tags.txt').read_bytes()).hexdigest(),'scope':'source asset integrity; runtime validation is covered separately by wf-multi-version'},indent=2));sys.exit(0 if results and all(r['pass'] for r in results) else 1)
