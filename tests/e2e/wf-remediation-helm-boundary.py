#!/usr/bin/env python3
"""Only actual chart template paths are excluded; ordinary YAML remains interactive."""
import importlib.util,pathlib,sys,json
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');spec=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
text=m.REN_DAG.replace('args: [work]','args: ["{{workflow.name}}"]');dest,work=m.setup('helm-boundary',{'chart/Chart.yaml':'apiVersion: v2\nname: sample\nversion: 0.1.0\n','chart/templates/workflow.yaml':text,'templates/plain.yaml':text,'ordinary.yaml':text});c=m.LSP(dest,work)
try:
 for relative,owned in [('chart/templates/workflow.yaml',False),('templates/plain.yaml',True),('ordinary.yaml',True)]:
  path=work/relative;c.open(path,text);c.send('textDocument/didSave',{'textDocument':{'uri':path.as_uri()}});r=c.request('textDocument/hover',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'entrypoint',offset=2)});m.result(relative,{'ownership':bool(r.get('result'))==owned,'source_unchanged':path.read_text()==text},{'response':r})
finally:c.close();m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputs':{str(p.relative_to(work)):m.sha(p) for p in work.rglob('*') if p.is_file()}})
sys.exit(0 if len(m.RESULTS)==3 and all(x['pass'] for x in m.RESULTS) else 1)
