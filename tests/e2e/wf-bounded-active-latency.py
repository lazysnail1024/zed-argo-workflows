#!/usr/bin/env python3
"""Focused same-workspace near-boundary diagnostic latency and scope behavior."""
import pathlib,sys,time,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();work=pathlib.Path(sys.argv[2]).resolve();out=pathlib.Path(sys.argv[3]).resolve();count=int(sys.argv[4]) if len(sys.argv)>4 else 20;out.mkdir(parents=True,exist_ok=True);sys.argv=[sys.argv[0],str(binary),str(out)];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);client=None;rows=[]
try:
 client=m.LSP(out,work);paths=[work/'group-000/00000.yaml',work/'group-001/00100.yaml'];texts=[p.read_text() for p in paths];texts[0]+='#'+'p'*(192*1024-len(texts[0].encode())-2)+'\n'
 for p,t in zip(paths,texts):client.open(p,t)
 for version in range(2,count+2):
  invalid=version%2==0;current=texts[0].replace('spec:\n','spec:\n  parallelism: wrong\n',1) if invalid else texts[0]
  begin=len(client.received);start=time.monotonic();diagnostics=client.change(paths[0],current+'# edit %d\n'%version,version);elapsed=time.monotonic()-start
  expected={'code':'SCHEMA_TYPE','severity':1,'range':{'start':{'line':4,'character':15},'end':{'line':4,'character':20}}}
  correct=(len(diagnostics)==1 and all(diagnostics[0].get(k)==v for k,v in expected.items())) if invalid else diagnostics==[]
  rows.append({'version':version,'invalidInput':invalid,'currentSemantics':correct,'diagnostics':diagnostics,'seconds':elapsed,'otherScopePublished':any(x.get('method')=='textDocument/publishDiagnostics' and x['params']['uri']==paths[1].as_uri() for x in client.received[begin:])});m.save(out/'samples.json',rows)
except Exception as e:rows.append({'error':repr(e)})
finally:
 if client:client.close()
valid=[r['seconds'] for r in rows if 'seconds' in r];p95=sorted(valid)[int((len(valid)-1)*.95)] if valid else None;checks={'allRevisions':len(valid)==count,'currentSemantics':len(valid)==count and all(r.get('currentSemantics') is True for r in rows),'p95Within500ms':p95 is not None and p95<=.5,'onlyAffectedScope':not any(r.get('otherScopePublished') for r in rows)};m.save(out/'results.json',{'assertions':checks,'pass':all(checks.values()),'p95Seconds':p95,'workspaceFiles':10000,'affectedScopeFiles':100,'activeFileBytes':[len(t.encode()) for t in texts]});m.save(out/'manifest.json',{'binarySha256':m.sha(binary),'testSha256':m.sha(pathlib.Path(__file__)),'command':sys.argv});sys.exit(0 if all(checks.values()) else 1)
