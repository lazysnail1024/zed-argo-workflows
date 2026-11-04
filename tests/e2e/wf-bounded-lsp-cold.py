#!/usr/bin/env python3
"""Five real full-workspace symbol indexes, with all 20,000 declarations observed."""
import pathlib,sys,time,importlib.util,json,os
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();work=pathlib.Path(sys.argv[2]).resolve();out=pathlib.Path(sys.argv[3]).resolve();out.mkdir(parents=True,exist_ok=True)
cgpath=pathlib.Path('/proc/self/cgroup').read_text().strip().split('::',1)[1];cg=pathlib.Path('/sys/fs/cgroup')/cgpath.lstrip('/');quota,period=(cg/'cpu.max').read_text().split();assert quota!='max' and int(quota)/int(period)<=4 and len(os.sched_getaffinity(0))==4;assert int((cg/'memory.max').read_text())<=8*1024**3
sys.argv=[sys.argv[0],str(binary),str(out)];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);rows=[]
for sample in range(5):
 folder=out/str(sample);folder.mkdir(exist_ok=True);client=None
 try:
  start=time.monotonic();client=m.LSP(folder,work);response=client.request('workspace/symbol',{'query':''});elapsed=time.monotonic()-start;symbols=response.get('result',[]);by_file={}
  for symbol in symbols:by_file.setdefault(symbol['location']['uri'],[]).append(symbol['name'])
  checks={'allFilesIndexed':len(by_file)==10000,'allDeclarations':len(symbols)==20000 and all(sorted(names)==['main','unused'] for names in by_file.values()),'withinFiveSeconds':elapsed<=5};rows.append({'sample':sample,'seconds':elapsed,'assertions':checks,'pass':all(checks.values())})
 except Exception as e:rows.append({'sample':sample,'pass':False,'error':repr(e)})
 finally:
  if client:client.close()
  m.save(out/'results.json',rows)
m.save(out/'manifest.json',{'binarySha256':m.sha(binary),'testSha256':m.sha(pathlib.Path(__file__)),'workloadManifestSha256':m.sha(work/'workload-manifest.json'),'cgroup':cgpath,'cpu.max':(cg/'cpu.max').read_text().strip(),'memory.max':(cg/'memory.max').read_text().strip(),'affinity':sorted(os.sched_getaffinity(0))});sys.exit(0 if len(rows)==5 and all(r['pass'] for r in rows) else 1)
