#!/usr/bin/env python3
"""Current schema-query latency and behavior on the fixed full workspace."""
import pathlib,sys,time,json,importlib.util,gzip
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();work=pathlib.Path(sys.argv[2]).resolve();out=pathlib.Path(sys.argv[3]).resolve();out.mkdir(parents=True,exist_ok=True)
sys.argv=[sys.argv[0],str(binary),str(out)];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);client=None;rows=[]
def record(name,checks,**details):
 rows.append({'case':name,'assertions':checks,'pass':all(checks.values()),**details});m.save(out/'results.json',rows)
try:
 client=m.LSP(out,work);paths=[work/'group-000/00000.yaml',work/'group-001/00100.yaml'];texts=[p.read_text() for p in paths];texts[0]+='#'+'p'*(192*1024-len(texts[0].encode())-2)+'\n';assert len(texts[0].encode())==192*1024
 for p,t in zip(paths,texts):client.open(p,t)
 samples=[];checks={'correctCompletion':True,'correctDefinition':True,'currentHover':True}
 for i in range(1000):
  path=paths[i%2];text=texts[i%2];version=['3.6.0','3.5.0'][i%2];method=['completion','definition','hover'][i%3];position={'line':5,'character':2} if method=='completion' else m.pos(text,'entrypoint: main',offset=13 if method=='definition' else 3);start=time.monotonic();r=client.request('textDocument/'+method,{'textDocument':{'uri':path.as_uri()},'position':position});samples.append(time.monotonic()-start)
  if method=='completion':checks['correctCompletion'] &= 'activeDeadlineSeconds' in {x['label'] for x in r.get('result',[])} and all('textEdit' in x for x in r.get('result',[]))
  elif method=='definition':checks['correctDefinition'] &= bool(r.get('result')) and all(x['uri']==path.as_uri() for x in r['result'])
  else:
   content=(r.get('result') or {}).get('contents',{}).get('value','');checks['currentHover'] &= version in content and 'Source:' in content and 'string' in content
 p95=sorted(samples)[int((len(samples)-1)*.95)];checks['p95Within100ms']=p95<=.1;record('1000-queries',checks,seconds=samples,p95Seconds=p95)
 start=time.monotonic();client.close();record('shutdown',{'withinFiveSeconds':time.monotonic()-start<=5,'exitZero':client.p.returncode==0});client=None
except Exception as e:record('runner-error',{'completed':False},error=repr(e))
finally:
 if client:client.close()
 m.save(out/'manifest.json',{'binarySha256':m.sha(binary),'testSha256':m.sha(pathlib.Path(__file__)),'workspaceFiles':10000,'filesPerScope':100,'activeFileBytes':[len(t.encode()) for t in texts],'activeScopes':2,'versions':['3.6.0','3.5.0'],'classification':'Query workload; unchanged lifecycle/burst evidence retains its original binary identity.'})
sys.exit(0 if len(rows)==2 and all(x['pass'] for x in rows) else 1)
