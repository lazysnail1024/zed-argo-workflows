#!/usr/bin/env python3
"""Real LSP query, editing and close lifecycle load with independent public outcomes."""
import pathlib,sys,time,importlib.util,json,statistics,os,threading,gzip
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();work=pathlib.Path(sys.argv[2]).resolve();out=pathlib.Path(sys.argv[3]).resolve();out.mkdir(parents=True,exist_ok=True)
sys.argv=[sys.argv[0],str(binary),str(out)];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
rows=[];client=None
p95=lambda values:sorted(values)[int((len(values)-1)*.95)]
def record(name,checks,**details):
 rows.append({'case':name,'assertions':checks,'pass':all(checks.values()),**details});m.save(out/'results.json',rows)
def rss():
 return int(next(x.split()[1] for x in pathlib.Path('/proc/%d/status'%client.p.pid).read_text().splitlines() if x.startswith('VmRSS:')))
try:
 start=time.monotonic();client=m.LSP(out,work);paths=[work/'group-000/00000.yaml',work/'group-001/00100.yaml'];texts=[p.read_text() for p in paths];texts[0]+='#'+('p'*(192*1024-len(texts[0].encode())-2))+'\n';assert len(texts[0].encode())==192*1024
 sent_log=gzip.open(out/'lsp.sent.jsonl.gz','wt');original_send=client.send
 for frame in client.sent:sent_log.write(json.dumps(frame)+'\n')
 client.sent.clear()
 def streamed_send(*args,**kwargs):
  original_send(*args,**kwargs);sent_log.write(json.dumps(client.sent[-1])+'\n');client.sent.clear()
 client.send=streamed_send
 for path,text in zip(paths,texts):client.open(path,text)
 record('cold-two-version-publication',{'withinFiveSeconds':time.monotonic()-start<=5},seconds=time.monotonic()-start)
 query=[];correct=True
 for i in range(1000):
  path=paths[i%2];text=texts[i%2];method='textDocument/definition' if i%4<2 else 'textDocument/completion';position=m.pos(text,'entrypoint: main',offset=13) if method.endswith('definition') else {'line':5,'character':2};start=time.monotonic();r=client.request(method,{'textDocument':{'uri':path.as_uri()},'position':position});query.append(time.monotonic()-start)
  if method.endswith('definition'):correct &= bool(r.get('result')) and all(x['uri']==path.as_uri() for x in r['result'])
  else:correct &= 'activeDeadlineSeconds' in {x['label'] for x in r.get('result',[])}
 record('1000-existing-queries',{'correct':correct,'p95Within100ms':p95(query)<=.1},seconds=query)
 # Debounced edits arrive at 10 Hz for exactly 60 seconds; then verify the final revision.
 start=time.monotonic();sent={};uri=paths[0].as_uri();begin=len(client.received)
 for i in range(600):
  due=start+i*.1;time.sleep(max(0,due-time.monotonic()));version=i+2;sent[version]=time.monotonic();client.send('textDocument/didChange',{'textDocument':{'uri':uri,'version':version},'contentChanges':[{'text':texts[0]+('# edit %d\n'%i)}]})
 client.diagnostics(uri,601);time.sleep(max(0,start+60-time.monotonic()));frames=client.received[begin:];versions=[x['params'].get('version') for x in frames if x.get('method')=='textDocument/publishDiagnostics' and x['params']['uri']==uri]
 record('600-edits-60-seconds',{'latestDelivered':601 in versions,'monotonic':versions==sorted(versions),'serverAlive':client.p.poll() is None},publishedVersions=versions,seconds=time.monotonic()-start)
 # A small number of spaced edits measures latency independently of coalesced burst delivery.
 lat=[]
 for version in range(602,622):
  start=time.monotonic();client.change(paths[0],texts[0]+'# spaced %d\n'%version,version);lat.append(time.monotonic()-start)
 record('active-diagnostic-latency',{'p95Within500ms':p95(lat)<=.5},seconds=lat)
 # Warm RSS and five equal lifecycle batches; unsaved URI must always clear on close.
 temp=work/'group-000/ephemeral.yaml';warm=[];after=[]
 for batch in range(6):
  for i in range(2000 if batch else 200):
   client.send('textDocument/didOpen',{'textDocument':{'uri':temp.as_uri(),'version':1,'languageId':'yaml','text':texts[0]}})
   client.send('textDocument/didChange',{'textDocument':{'uri':temp.as_uri(),'version':2},'contentChanges':[{'text':texts[0]+'# edited\n'}]})
   last_close_marker=len(client.received);client.send('textDocument/didClose',{'textDocument':{'uri':temp.as_uri()}})
   if i%100==99:client.request('textDocument/documentSymbol',{'textDocument':{'uri':uri}})
  cleared=lambda x:x.get('method')=='textDocument/publishDiagnostics' and x['params']['uri']==temp.as_uri() and x['params']['diagnostics']==[]
  if not any(cleared(x) for x in client.received[last_close_marker:]):client.wait(cleared)
  # Terminal query is also an ordering barrier; allow the debounced analysis to retire.
  client.request('textDocument/documentSymbol',{'textDocument':{'uri':uri}});time.sleep(.3)
  (warm if batch==0 else after).append(rss())
 record('10000-open-edit-close',{'fiveSamples':len(after)==5,'growthWithin10Percent':max(after)<=warm[0]*1.1,'serverAlive':client.p.poll() is None},warmRSSKiB=warm,sampleRSSKiB=after)
 start=time.monotonic();client.close();record('shutdown',{'withinFiveSeconds':time.monotonic()-start<=5,'exitZero':client.p.returncode==0},seconds=time.monotonic()-start);client=None
except Exception as e:record('runner-error',{'completed':False},error=repr(e))
finally:
 if client:client.close()
 if 'sent_log' in globals():sent_log.close()
 m.save(out/'manifest.json',{'binarySha256':m.sha(binary),'testSha256':m.sha(pathlib.Path(__file__)),'workspaceFiles':10000,'filesPerScope':100,'activeFiles':2,'activeBufferMutation':'First buffer padded to exactly 192 KiB; canonical disk workload remains unchanged.','affectedScopeFilesPerEdit':100,'activeScopes':2,'activeFileBytes':[len(t.encode()) for t in texts],'activeBytes':sum(len(t.encode()) for t in texts),'versions':['3.6.0','3.5.0'],'python':sys.version})
sys.exit(0 if len(rows)==6 and all(r['pass'] for r in rows) else 1)
