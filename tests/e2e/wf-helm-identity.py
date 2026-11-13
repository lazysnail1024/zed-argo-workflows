#!/usr/bin/env python3
"""The GUI source fixture: caller runtime name does not identify a template target."""
import importlib.util,pathlib,sys,traceback
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');sp=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
fixture=p.parents[1]/'fixtures/helm-static-source';files={str(f.relative_to(fixture)):f.read_text() for f in fixture.rglob('*') if f.is_file()}
try:
 d,w=m.setup('source',files,config='');f=w/'templates/workflow.yaml';library=w/'templates/library.yaml';text=files['templates/workflow.yaml'];target=files['templates/library.yaml'];c=m.LSP(d,w)
 try:
  c.open(f,text,language_id='helm')
  for needle,target_name in [('name: helm-library','helm-library'),('template: render','render')]:
   r=c.request('textDocument/definition',{'textDocument':{'uri':f.as_uri()},'position':m.pos(text,needle,offset=len(needle.split(':')[0])+3)})
   m.result('dynamic-caller-name-'+target_name,{'exact_target':any(x['uri']==library.as_uri() and x['range']['start']==m.pos(target,'name: '+target_name,offset=6) for x in r.get('result') or [])},r)
  r=c.request('textDocument/definition',{'textDocument':{'uri':f.as_uri()},'position':m.pos(text,'workflow.parameters.message',offset=20)})
  m.result('escaped-parameter-with-dynamic-caller-name',{'exact_target':any(x['uri']==f.as_uri() and x['range']['start']==m.pos(text,'name: message',offset=6) for x in r.get('result') or [])},r)
  unknown=target.replace('name: helm-library','name: {{ .Values.libraryName | quote }}')
  (w/'templates/unknown.yaml').write_text(unknown)
  r=c.request('textDocument/definition',{'textDocument':{'uri':f.as_uri()},'position':m.pos(text,'template: render',offset=12)})
  m.result('unknown-template-candidate-refuses-binding',{'empty':not r.get('error') and not r.get('result')},r)
 finally:c.close()
except Exception as e:m.result('runner',{'completed':False},{'error':repr(e),'traceback':traceback.format_exc()})
finally:m.save(m.OUT/'manifest.json',{'command':sys.argv,'pythonVersion':sys.version,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputs':{str(f.relative_to(fixture)):m.sha(f) for f in fixture.rglob('*') if f.is_file()}})
sys.exit(0 if len(m.RESULTS)==4 and all(x['pass'] for x in m.RESULTS) else 1)
