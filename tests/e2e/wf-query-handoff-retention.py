#!/usr/bin/env python3
"""Bounded real-process retention across the canonical 100 distinct scopes."""
from pathlib import Path
import importlib.util,json,sys,time,threading,statistics,traceback
HERE=Path(__file__).resolve().parent
from query_density_support import wait_for_index_ready
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
WORK=Path(sys.argv[3]).resolve();phases=[('warm',[i%2 for i in range(20)]),('distinct',list(range(100))),('steady',[98+i%2 for i in range(20)])];rows=[];samples=[];stop=threading.Event();client=None;checks={};manifest=json.loads((WORK/'workload-manifest.json').read_text());assert manifest['yamlFiles']==10000 and manifest['yamlBytes']==52428800 and manifest['scopeCount']==100
expected={'phases':[{'name':n,'scopes':s} for n,s in phases],'query':'two fixed entrypoint definitions per step; each exact or explicit reconciliation cancellation, at least one exact','declarationRange':{'start':{'line':6,'character':12},'end':{'line':6,'character':16}},'retentionGrowthLimit':1.10,'singleServerRSSLimitKiB':819200,'diskInputsRemainUnchanged':True,'indexPrecondition':{'files':10000,'queue':0,'retainedTextBytes':0,'deadlineSeconds':60}};m.save(m.OUT/'expected.json',expected)
def rss():
 try:return int(next(line.split()[1] for line in Path(f'/proc/{client.p.pid}/status').read_text().splitlines() if line.startswith('VmRSS:')))
 except (FileNotFoundError,StopIteration):return 0
def monitor():
 while not stop.wait(.05):samples.append({'seconds':time.monotonic()-start,'rssKiB':rss()})
start=time.monotonic();readiness=None;readiness_seconds=None
try:
 client=m.LSP(m.OUT,WORK);thread=threading.Thread(target=monitor,daemon=True);thread.start();previous=None;step=0
 readiness=wait_for_index_ready(client,WORK,10000,0,timeout=max(0,60-(time.monotonic()-start)));readiness_seconds=time.monotonic()-start
 for phase,scopes in phases:
  for scope in scopes:
   path=WORK/f'group-{scope:03d}'/f'{scope*100:05d}.yaml';text=path.read_text()+f'# retained-fact revision {step}\n';step+=1
   if previous is not None:client.send('textDocument/didClose',{'textDocument':{'uri':previous.as_uri()}})
   diagnostics=client.open(path,text,step)
   responses=[client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':{'line':4,'character':15}}) for _ in range(2)];want=[{'uri':path.as_uri(),'range':expected['declarationRange']}]
   row={'phase':phase,'scope':scope,'step':step,'uri':path.as_uri(),'bufferSha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'diagnostics':diagnostics,'responses':responses,'exact':diagnostics==[] and any(r.get('result')==want for r in responses) and all(r.get('result')==want or r.get('error',{}).get('code')==-32801 for r in responses),'rssKiB':rss()};rows.append(row);m.save(m.OUT/'requests.json',rows);previous=path
  print(phase,len(rows),flush=True)
 warm=[r['rssKiB'] for r in rows if r['phase']=='warm'][-10:];steady=[r['rssKiB'] for r in rows if r['phase']=='steady'];warm_median=statistics.median(warm);steady_median=statistics.median(steady);growth=steady_median/warm_median if warm_median else None
 checks={'indexReady':readiness is not None and readiness_seconds<=60,'all140CurrentResponsePairs':len(rows)==140 and all(r['exact'] for r in rows),'all100Scopes':set(r['scope'] for r in rows if r['phase']=='distinct')==set(range(100)),'steadyGrowthWithin10Percent':growth is not None and growth<=1.10,'singleServerRSSWithinPairBudget':max([s['rssKiB'] for s in samples]+[0])<=819200};summary={'warmMedianRSSKiB':warm_median,'steadyMedianRSSKiB':steady_median,'growth':growth}
except Exception:
 (m.OUT/'error.txt').write_text(traceback.format_exc());checks['completed']=False;summary={}
finally:
 if client:client.close()
 stop.set()
 if 'thread' in locals():thread.join(timeout=1)
 checks['diskInputsRemainUnchanged']=all(m.sha(WORK/p)==h for p,h in manifest['files'].items())
 checks['cleanExit']=client is not None and client.p.returncode==0;m.save(m.OUT/'rss.json',samples);m.save(m.OUT/'results.json',{'assertions':checks,'pass':bool(checks) and all(checks.values()),**summary,'wallSeconds':time.monotonic()-start,'indexReadiness':readiness,'indexReadinessSeconds':readiness_seconds,'samples':len(samples),'peakRSSKiB':max([s['rssKiB'] for s in samples]+[0])});m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'queryReadinessHelperSha256':m.sha(HERE/'query_density_support.py'),'expectedSha256':m.sha(m.OUT/'expected.json'),'workloadManifestSha256':m.sha(WORK/'workload-manifest.json'),'scopeCount':100,'yamlFiles':10000,'yamlBytes':52428800,'phases':phases})
sys.exit(0 if checks and all(checks.values()) else 1)
