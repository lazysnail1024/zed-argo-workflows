#!/usr/bin/env python3
"""Source navigation uses original coordinates and never pretends to render Helm."""
import importlib.util, pathlib, sys, traceback
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');sp=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
text='''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: sample, namespace: default}
spec:
  entrypoint: main
  arguments:
    parameters:
      - name: message
        value: hello
  templates:
    - name: main
      steps:
        - - name: invoke
            template: leaf
    - name: leaf
      container:
        image: {{ .Values.image }}
        args: ["{{ `{{workflow.parameters.message}}` }}"]
'''
try:
 d,w=m.setup('navigation',{'Chart.yaml':'apiVersion: v2\nname: sample\nversion: 0.1.0\n','templates/workflow.yaml':text,'templates/library.yaml': 'apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: default}\nspec:\n  templates:\n    - name: external-leaf\n      container: {image: {{ .Values.image | quote }}}\n'},config='')
 c=m.LSP(d,w);f=w/'templates/workflow.yaml'
 try:
  ds=c.open(f,text,language_id='helm')
  def query(method,needle,offset=0):return c.request('textDocument/'+method,{'textDocument':{'uri':f.as_uri()},'position':m.pos(text,needle,offset=offset)})
  for needle,target in [('main','main'),('template: leaf','leaf'),('workflow.parameters.message','message')]:
   off=10 if needle=='template: leaf' else (20 if needle.startswith('workflow.') else 1)
   r=query('definition',needle,off);expected=m.pos(text,'name: '+target,offset=6)
   m.result(needle,{'exact_original_target':any(x['uri']==f.as_uri() and x['range']['start']==expected for x in r.get('result') or [])},r)
  for method in ['hover','completion','references','prepareRename']:
   r=query(method,'main',1);m.result(method,{'empty_without_error':not r.get('error') and not r.get('result')},r)
  m.result('no-render-diagnostics',{'empty':ds==[]})
  for i,action in enumerate(['{{ if .Values.enabled }}\n','{{ range .Values.items }}\n','{{ include "other" . }}\n']):
   c.change(f,action+text,2+i);r=c.request('textDocument/definition',{'textDocument':{'uri':f.as_uri()},'position':m.pos(action+text,'template: leaf',offset=10)})
   m.result('reject-structure-'+str(i),{'empty':not r.get('error') and not r.get('result')},r)
  pure=text.replace('{{ .Values.image }}','alpine').replace('{{ `{{workflow.parameters.message}}` }}','{{workflow.parameters.message}}')
  c.change(f,pure,5);r=c.request('textDocument/hover',{'textDocument':{'uri':f.as_uri()},'position':m.pos(pure,'name: leaf',offset=1)})
  m.result('pure-chart-name-hover',{'supported':bool(r.get('result')) and not r.get('error')},r)
  external=text.replace('template: leaf','templateRef: {name: library, template: external-leaf}')
  c.change(f,external,6)
  library=w/'templates/library.yaml';library_text=library.read_text()
  for needle,target in [('name: library','library'),('template: external-leaf','external-leaf')]:
   r=c.request('textDocument/definition',{'textDocument':{'uri':f.as_uri()},'position':m.pos(external,needle,offset=len(needle.split(':')[0])+2)})
   m.result('external-'+target,{'exact':any(x['uri']==library.as_uri() and x['range']['start']==m.pos(library_text,'name: '+target,offset=6) for x in r.get('result') or [])},r)
  dynamic=external.replace('name: library','name: {{ .Values.library }}')
  c.change(f,dynamic,7)
  r=c.request('textDocument/definition',{'textDocument':{'uri':f.as_uri()},'position':m.pos(dynamic,'external-leaf',offset=2)})
  m.result('dynamic-resource-refused',{'empty':not r.get('error') and not r.get('result')},r)
  for version,caller_ns,library_ns in [(8,'namespace','namespace'),(9,'foo','bar')]:
   unknown=external.replace('namespace: default','namespace: "{{ .Values.'+caller_ns+' }}"')
   library.write_text(library_text.replace('namespace: default','namespace: "{{ .Values.'+library_ns+' }}"'))
   c.change(f,unknown,version)
   r=c.request('textDocument/definition',{'textDocument':{'uri':f.as_uri()},'position':m.pos(unknown,'template: external-leaf',offset=12)})
   m.result('dynamic-namespace-'+caller_ns+'-'+library_ns,{'empty':not r.get('error') and not r.get('result')},r)
  library.write_text(library_text)
  dynamic_key='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: sample}\nspec:\n  entrypoint: main\n  templates: [{name: main, container: {image: alpine}, {{ .Values.key }}: renamed}]\n'
  c.change(f,dynamic_key,10)
  r=c.request('textDocument/definition',{'textDocument':{'uri':f.as_uri()},'position':m.pos(dynamic_key,'entrypoint: main',offset=13)})
  m.result('dynamic-mapping-key',{'empty':not r.get('error') and not r.get('result')},r)
  unicode=text.replace('args: [','args: ["\U0001f680", ').replace('\n','\r\n')
  c.change(f,unicode,11)
  r=c.request('textDocument/definition',{'textDocument':{'uri':f.as_uri()},'position':m.pos(unicode,'workflow.parameters.message',offset=20)})
  m.result('unicode-crlf',{'exact':any(x['range']['start']==m.pos(unicode,'name: message',offset=6) for x in r.get('result') or [])},r)

 finally:c.close()
except Exception as e:m.result('runner',{'completed':False},{'error':repr(e),'traceback':traceback.format_exc()})
finally:m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'clientSha256':m.sha(p),'inputs':{str(x.relative_to(m.OUT)):m.sha(x) for x in m.OUT.rglob('*') if x.is_file() and 'input' in x.parts}})
sys.exit(0 if len(m.RESULTS)==19 and all(x['pass'] for x in m.RESULTS) else 1)
