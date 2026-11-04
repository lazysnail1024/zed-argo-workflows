#!/usr/bin/env python3
"""Real full-workspace cold publication and existing query controls."""
import pathlib,sys,time,importlib.util,json
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();work=pathlib.Path(sys.argv[2]).resolve();out=pathlib.Path(sys.argv[3]).resolve();count=int(sys.argv[4]) if len(sys.argv)>4 else 10;out.mkdir(parents=True,exist_ok=True)
sys.argv=[sys.argv[0],str(binary),str(out)];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);rows=[];client=None
try:
 start=time.monotonic();client=m.LSP(out,work);path=work/'group-000/00000.yaml';text=path.read_text();client.open(path,text);rows.append({'case':'cold-publication','seconds':time.monotonic()-start,'pass':time.monotonic()-start<=5})
 for i in range(count):
  start=time.monotonic();r=client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'entrypoint: main',offset=13)});elapsed=time.monotonic()-start;locations=r.get('result',[]);rows.append({'case':'definition-%d'%i,'seconds':elapsed,'response':r,'pass':bool(locations) and all(x['uri']==path.as_uri() for x in locations) and elapsed<=.1})
  (out/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
except Exception as e:rows.append({'case':'runner-error','pass':False,'error':repr(e)})
finally:
 if client:client.close()
 (out/'results.json').write_text(json.dumps(rows,indent=2)+'\n');m.save(out/'manifest.json',{'binarySha256':m.sha(binary),'testSha256':m.sha(pathlib.Path(__file__)),'workspaceFiles':10000,'scopeFiles':100,'queries':count,'python':sys.version})
sys.exit(0 if len(rows)==count+1 and all(r['pass'] for r in rows) else 1)
