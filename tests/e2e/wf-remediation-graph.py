#!/usr/bin/env python3
"""Repeated external queries reuse complete answers; actual dependency changes do not."""
import importlib.util,pathlib,sys,time,json
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');spec=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
files={}
for i in range(2000):
 files[f'base-{i:05d}.yaml']=m.BASE.replace('name: base,',f'name: base-{i},')
 files[f'caller-{i:05d}.yaml']=m.CALLER.replace('name: caller,',f'name: caller-{i},').replace('name: base,',f'name: base-{i},')
dest,work=m.setup('graph',files);c=m.LSP(dest,work);path=work/'caller-00000.yaml';callee=work/'base-00000.yaml';text=files[path.name];params={'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'template: work',offset=12)};times=[];answers=[]
try:
 c.open(path,text)
 for i in range(6):
  start=time.monotonic();r=c.request('textDocument/definition',params);times.append(time.monotonic()-start);answers.append(r)
 m.result('unchanged-query',{'same_exact_definition':all(len(r.get('result',[]))==1 and r['result'][0]['uri']==callee.as_uri() for r in answers),'warm_budget':max(times[1:])<.1},{'seconds':times,'responses':answers})
 original=callee.read_text();callee.write_text(original.replace('name: work','name: other'));r=c.request('textDocument/definition',params);m.result('changed-dependency',{'no_stale_definition':r.get('result')==[]},{'response':r})
 callee.write_text(original);r=c.request('textDocument/definition',params);m.result('dependency-recovery',{'restored':len(r.get('result',[]))==1},{'response':r})
 duplicate=work/'duplicate.yaml';duplicate.write_text(original);r=c.request('textDocument/definition',params);m.result('duplicate',{'both_candidates':len(r.get('result',[]))==2},{'response':r});duplicate.unlink()
 callee.unlink();r=c.request('textDocument/definition',params);m.result('deleted',{'no_stale_definition':r.get('result')==[]},{'response':r});callee.write_text(original)
 c.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':callee.as_uri(),'type':1}]});ds=c.diagnostics(path.as_uri(),1);m.result('diagnostic-recovery',{'resolved':not any(d['code']=='WF_TEMPLATE_REF_UNRESOLVED' for d in ds)},{'diagnostics':ds})
finally:c.close();m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputs':{str(p.relative_to(work)):m.sha(p) for p in work.rglob('*') if p.is_file()}})
sys.exit(0 if len(m.RESULTS)==6 and all(x['pass'] for x in m.RESULTS) else 1)
