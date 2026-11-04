#!/usr/bin/env python3
"""Live process query-cache eviction and changing-input retention trend."""
import importlib.util,pathlib,sys,time,json
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');spec=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
dest,work=m.setup('retention',{'caller.yaml':m.CALLER,'base.yaml':m.BASE});c=m.LSP(dest,work);path=work/'caller.yaml';rss=[];checks=[]
try:
 c.open(path,m.CALLER)
 for i in range(48):
  text=m.CALLER+'# lifecycle '+str(i)+'\n';ds=c.change(path,text,i+2);r=c.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'template: work',offset=12)});checks.append(not ds and len(r.get('result',[]))==1 and r['result'][0]['uri']==(work/'base.yaml').as_uri());status=pathlib.Path(f'/proc/{c.p.pid}/status').read_text();rss.append(int(next(x for x in status.splitlines() if x.startswith('VmRSS:')).split()[1]))
 m.result('bounded-retention',{'all_versions_correct':all(checks),'growth_plateau':max(rss[24:])<=max(rss[:24])*1.8+16384},{'rssKiB':rss,'cycles':48,'queryCacheCapacity':16,'note':'48 actual input changes, not a production lifetime guarantee.'})
finally:c.close();m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputHashes':{str(p.relative_to(work)):m.sha(p) for p in work.rglob('*') if p.is_file()}})
sys.exit(0 if m.RESULTS and all(x['pass'] for x in m.RESULTS) else 1)
