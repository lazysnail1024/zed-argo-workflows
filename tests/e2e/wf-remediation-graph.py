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
 # This case measures complete warm answers. Readiness is an index event,
 # not a fixed sleep or an accidental whole-workspace scan by the first query.
 ready=None;deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  for line in bytes(c.stderr_raw).splitlines():
   try:state=json.loads(line)
   except (ValueError,UnicodeDecodeError):continue
   if state.get('phase')=='index' and state.get('state')=='Ready' and state.get('files')==len(files):ready=state
  if ready is not None:break
  time.sleep(.02)
 for i in range(6):
  start=time.monotonic();r=c.request('textDocument/definition',params);times.append(time.monotonic()-start);answers.append(r)
 m.result('unchanged-query',{'complete_index_before_warm_queries':ready is not None,'same_exact_definition':all(len(r.get('result',[]))==1 and r['result'][0]['uri']==callee.as_uri() for r in answers),'warm_budget':max(times[1:])<.1},{'ready':ready,'seconds':times,'responses':answers})
 original=callee.read_text();location={'start':m.pos(original,'name: work',offset=6),'end':m.pos(original,'name: work',offset=10)};original_definition=[{'uri':callee.as_uri(),'range':location}]
 callee.write_text(original.replace('name: work','name: other'));r=c.request('textDocument/definition',params);m.result('changed-dependency',{'no_stale_definition':r.get('result')==[]},{'response':r})
 # The changed-input query withdraws old facts immediately. Reindexing the
 # restored known file is asynchronous, so allow only safe intermediate answers.
 callee.write_text(original);recovery=[];deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  r=c.request('textDocument/definition',params);recovery.append(r)
  if 'error' not in r and r.get('result')==original_definition:break
  time.sleep(.02)
 m.result('dependency-recovery',{'restored':bool(recovery) and 'error' not in recovery[-1] and recovery[-1].get('result')==original_definition,
  'each_response_has_only_current_location':all(response.get('error',{}).get('code')==-32801 or 'error' not in response and response.get('result') in ([],original_definition) for response in recovery)}, {'responses':recovery,'expected':original_definition})
 # New URIs enter the persistent index through file events. Queries may use
 # known candidates while that event is processed, but must eventually return
 # both exact declarations without selecting one ambiguous target.
 duplicate=work/'duplicate.yaml';duplicate.write_text(original)
 c.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':duplicate.as_uri(),'type':1}]})
 expected=[{'uri':candidate.as_uri(),'range':location} for candidate in (callee,duplicate)]
 normalize=lambda rows:sorted(json.dumps(row,sort_keys=True) for row in rows)
 created=[];deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  r=c.request('textDocument/definition',params);created.append(r)
  if 'error' not in r and normalize(r.get('result',[]))==normalize(expected):break
  time.sleep(.02)
 ambiguous=c.request('textDocument/references',params|{'context':{'includeDeclaration':True}})
 duplicate.unlink()
 c.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':duplicate.as_uri(),'type':3}]})
 removed=[];deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  current=c.request('textDocument/definition',params);removed.append(current)
  if 'error' not in current and current.get('result')==expected[:1]:break
  time.sleep(.02)
 m.result('duplicate',{'both_candidates':bool(created) and 'error' not in created[-1] and normalize(created[-1].get('result',[]))==normalize(expected),
  'ambiguous_reference_not_uniquely_bound':'error' not in ambiguous and ambiguous.get('result')==[],
  'deleted_candidate_never_reappears':bool(removed) and all(response.get('error',{}).get('code')==-32801 or 'error' not in response and all(item['uri']!=duplicate.as_uri() for item in response.get('result',[])) for response in removed),
  'remaining_exact_candidate_recovers':bool(removed) and 'error' not in removed[-1] and removed[-1].get('result')==expected[:1]},
  {'created':created,'references':ambiguous,'afterDelete':removed,'expected':expected})
 callee.unlink();r=c.request('textDocument/definition',params);m.result('deleted',{'no_stale_definition':r.get('result')==[]},{'response':r});callee.write_text(original)
 c.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':callee.as_uri(),'type':1}]});ds=c.diagnostics(path.as_uri(),1);m.result('diagnostic-recovery',{'resolved':not any(d['code']=='WF_TEMPLATE_REF_UNRESOLVED' for d in ds)},{'diagnostics':ds})
finally:c.close();m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputs':{str(p.relative_to(work)):m.sha(p) for p in work.rglob('*') if p.is_file()}})
sys.exit(0 if len(m.RESULTS)==6 and all(x['pass'] for x in m.RESULTS) else 1)
