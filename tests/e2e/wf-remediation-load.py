#!/usr/bin/env python3
"""Audit-shaped single-scope workload: semantic output, latency, RSS and recovery."""
import importlib.util,pathlib,sys,time,json,platform,traceback,threading,math
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');spec=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
opened_count=int(sys.argv[3]) if len(sys.argv)>3 else 30
pairs=int(sys.argv[4]) if len(sys.argv)>4 else 2000
files={}
for i in range(pairs):
 files[f'base-{i:05d}.yaml']=m.BASE.replace('name: base,',f'name: base-{i},')
 files[f'caller-{i:05d}.yaml']=m.CALLER.replace('name: caller,',f'name: caller-{i},').replace('name: base,',f'name: base-{i},')
if pairs==2000:assert sum(len(v.encode()) for v in files.values())==888670
dest,work=m.setup('single-scope',files);c=m.LSP(dest,work);latencies=[];responses=[];rss=[];stop=threading.Event()
def memory():
 while not stop.wait(.02):
  try:
   status=pathlib.Path(f'/proc/{c.p.pid}/status').read_text();rss.append({'time':time.monotonic(),'rssKiB':int(next(x for x in status.splitlines() if x.startswith('VmRSS:')).split()[1])})
  except (OSError,StopIteration):pass
monitor=threading.Thread(target=memory,daemon=True);monitor.start()
def publications(version):return [(t,x['params']) for t,x in zip(c.received_times,c.received) if x.get('method')=='textDocument/publishDiagnostics' and x['params'].get('version')==version]
def quantile(values,q):return sorted(values)[max(0,math.ceil(len(values)*q)-1)]
try:
 for i in range(opened_count):
  path=work/f'caller-{i:05d}.yaml';c.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'yaml','text':files[path.name]}})
 opened=time.monotonic();time.sleep(.22);path=work/'caller-00000.yaml';text=files[path.name]
 for i in range(121):
  method='textDocument/hover' if i%2==0 else 'textDocument/completion';pos=m.pos(text,'entrypoint',offset=2) if i%2==0 else m.pos(text,'entrypoint: main',offset=14)
  now=time.monotonic();r=c.request(method,{'textDocument':{'uri':path.as_uri()},'position':pos});latencies.append(time.monotonic()-now);responses.append({'method':method,'response':r});time.sleep(.005)
 deadline=opened+21.3
 while time.monotonic()<deadline and len({d['uri'] for t,d in publications(1)})<opened_count:time.sleep(.02)
 ds=publications(1);first=min((t-opened for t,d in ds),default=None);complete=max((t-opened for t,d in ds),default=None) if len({d['uri'] for t,d in ds})==opened_count else None
 correct=[]
 for item in responses:
  r=item['response'];correct.append('entrypoint' in json.dumps(r.get('result')) and 'Type: `string`' in json.dumps(r.get('result')) if item['method'].endswith('hover') else any(x.get('label')=='main' for x in (r.get('result') or [])))
 broken=text.replace('  entrypoint:', '  parallelism: wrong\n  entrypoint:');after=c.change(path,broken,2)
 m.result('single-scope',{'first_within_budget':first is not None and first<=5,'all_current_diagnostics':len({d['uri'] for t,d in ds})==opened_count,'valid_graph_no_errors':bool(ds) and all(not d['diagnostics'] for t,d in ds),'type_error_retained':any(d['code']=='SCHEMA_TYPE' for d in after),'queries_correct':all(correct),'within_completion_budget':complete is not None and complete<=10,'warm_p95':quantile(latencies[1:],.95)<=.1,'warm_p99':quantile(latencies[1:],.99)<=.25},{'diagnosticCount':len(ds),'firstSeconds':first,'allSeconds':complete,'coldRequestSeconds':latencies[0],'warmSamples':len(latencies)-1,'warmP50':quantile(latencies[1:],.5),'warmP95':quantile(latencies[1:],.95),'warmP99':quantile(latencies[1:],.99),'maxSeconds':max(latencies),'latenciesSeconds':latencies,'responses':responses,'errors':sum('error' in x['response'] for x in responses),'timeouts':sum('timed out' in json.dumps(x['response']) for x in responses),'cancelled':sum('cancel' in json.dumps(x['response']) for x in responses),'peakRssKiB':max((x['rssKiB'] for x in rss),default=None)})
 # Repeated close/reopen discards stale open-buffer errors and returns disk input.
 lifecycle=[]
 for i in range(3):
  c.send('textDocument/didClose',{'textDocument':{'uri':path.as_uri()}});time.sleep(.15);ds=c.open(path,text,3+i);lifecycle.append({'pass':ds==[],'rssKiB':rss[-1]['rssKiB'] if rss else None})
 m.result('lifecycle-growth',{'disk_recovered':all(x['pass'] for x in lifecycle)},{'samples':lifecycle,'note':'Short-cycle trend; not a long-running leak claim.'})
except Exception as e:m.result('runner',{'completed':False},{'error':repr(e),'traceback':traceback.format_exc()})
finally:
 c.close();stop.set();monitor.join();m.save(m.OUT/'rss.json',rss);m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'platform':platform.platform(),'cpu':next((s for s in pathlib.Path('/proc/cpuinfo').read_text().splitlines() if s.startswith('model name')),'unknown') if pathlib.Path('/proc/cpuinfo').exists() else platform.processor(),'python':sys.version,'files':pairs*2,'resources':pairs*2,'externalReferences':pairs,'localEntrypointReferences':pairs*2,'scopeCount':1,'openCount':opened_count,'yamlBytes':sum(len(v.encode()) for v in files.values()),'configBytes':len(m.CONFIG.encode()),'commentPaddingBytes':0,'coldProcess':True,'diskCache':'uncontrolled','inputs':{str(p.relative_to(work)):m.sha(p) for p in work.rglob('*') if p.is_file()}})
sys.exit(0 if m.RESULTS and all(x['pass'] for x in m.RESULTS) else 1)
