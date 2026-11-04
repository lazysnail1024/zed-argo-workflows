#!/usr/bin/env python3
"""Independent static domain identities through real LSP and CLI processes."""
import pathlib,sys,re,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('wire',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
# Tags define independent source ranges, never derive bindings from server output.
body='''  entrypoint: <entry>main</entry>
  arguments:
    parameters: [{name: <wf>global</wf>, value: ready}]
  templates:
    - name: <main>main</main>
      dag:
        tasks:
          - name: <task>produce</task>
            template: <call1>worker</call1>
            arguments: {parameters: [{name: <arg>message</arg>, value: hello}]}
          - name: consume
            depends: <dep>produce</dep>.Succeeded
            template: <call2>worker</call2>
            arguments:
              parameters:
                - name: <arg2>message</arg2>
                  value: "{{tasks.<taskref>produce</taskref>.outputs.parameters.<outref>result-name</outref>}}"
    - name: <worker>worker</worker>
      inputs:
        parameters: [{name: <param>message</param>}]
        artifacts: [{name: <artifact>file</artifact>, path: /tmp/in, optional: true}]
      outputs:
        parameters: [{name: <out>result-name</out>, value: "{{inputs.parameters.<paramref>message</paramref>}}"}]
      container:
        image: alpine
        args:
          - "{{= '\U0001f600' + inputs.parameters['<bracket>message</bracket>'] }}"
          - "{{inputs.artifacts.<artref>file</artref>.path}}"
          - "{{workflow.parameters.<wfref>global</wfref>}}"
          - "{{= let inputs = {parameters: {message: 'shadow'} }; inputs.parameters.<shadow>message</shadow> }}"
          - "{{= let inputs = inputs.parameters['<init>message</init>']; inputs }}"
          - "{{= $env.inputs.parameters.<env>message</env> }}"
    - name: other
      inputs: {parameters: [{name: <otherparam>message</otherparam>, default: other}]}
      container: {image: alpine, args: ["{{inputs.parameters.<otherref>message</otherref>}}"]}
    - name: sequential
      steps:
        - - name: <step>produce</step>
            template: <stepcall>worker</stepcall>
            arguments: {parameters: [{name: <steparg>message</steparg>, value: hello}]}
        - - name: consume
            template: <stepcall2>worker</stepcall2>
            arguments: {parameters: [{name: <steparg2>message</steparg2>, value: "{{steps.<stepref>produce</stepref>.outputs.parameters.<stepout>result-name</stepout>}}"}]}
'''
links={'entry':'main','call1':'worker','call2':'worker','arg':'param','arg2':'param','dep':'task','taskref':'task','outref':'out','paramref':'param','bracket':'param','artref':'artifact','wfref':'wf','init':'param','otherref':'otherparam','stepcall':'worker','stepcall2':'worker','steparg':'param','steparg2':'param','stepref':'step','stepout':'out'}
kind_by_tag={'main':12,'worker':12,'wf':13,'task':12,'param':13,'artifact':13,'out':13,'otherparam':13,'step':12}
def strip(marked):
 spans={};text='';end=0
 for hit in re.finditer(r'<(\w+)>(.*?)</\1>',marked):
  text+=marked[end:hit.start()];a=len(text);text+=hit[2];spans[hit[1]]=(a,len(text));end=hit.end()
 return text+marked[end:],spans
def position(text,at):
 head=text[:at];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def rng(text,span):return {'start':position(text,span[0]),'end':position(text,span[1])}
def location(path,text,span):return {'uri':path.as_uri(),'range':rng(text,span)}
def norm(values):return sorted(json.dumps(x,sort_keys=True) for x in values)
for kind in ['Workflow','CronWorkflow','WorkflowTemplate','ClusterWorkflowTemplate']:
 marked='apiVersion: argoproj.io/v1alpha1\nkind: '+kind+'\nmetadata: {name: query, annotations: {note: "\U0001f600"}}\nspec:\n'
 if kind=='CronWorkflow':marked+='  schedule: "* * * * *"\n  workflowSpec:\n'+''.join('  '+l+'\n' for l in body.splitlines())
 else:marked+=body
 if kind=='WorkflowTemplate':marked=marked.replace('\n','\r\n')
 text,spans=strip(marked);dest,work=m.setup(kind,{'case.yaml':text});path=work/'case.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);checks={}
  for use,target in links.items():
   r=client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':position(text,spans[use][0])});checks['definition-'+use]=r.get('result')==[location(path,text,spans[target])]
  for use in ['shadow','env']:
   r=client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':position(text,spans[use][0])});checks['no-invented-'+use]=r.get('result')==[]
  for target in ['param','otherparam','task','step','out']:
   for include in [False,True]:
    r=client.request('textDocument/references',{'textDocument':{'uri':path.as_uri()},'position':position(text,spans[target][0]),'context':{'includeDeclaration':include}})
    expected=[location(path,text,spans[u]) for u,t in links.items() if t==target]+([location(path,text,spans[target])] if include else []);checks['references-'+target+str(include)]=norm(r.get('result',[]))==norm(expected)
  r=client.request('textDocument/documentSymbol',{'textDocument':{'uri':path.as_uri()}});symbols=r.get('result',[])
  for tag,k in kind_by_tag.items():checks['symbol-'+tag]=any(x['name']==text[slice(*spans[tag])] and x['kind']==k and x['selectionRange']==rng(text,spans[tag]) for x in symbols)
  r=client.request('workspace/symbol',{'query':'message'});expected=[{'name':'message','kind':13,'location':location(path,text,spans[t])} for t in ['param','otherparam']];checks['workspace-symbols']=norm([{k:x[k] for k in ['name','kind','location']} for x in r.get('result',[])])==norm(expected)
  m.result(kind,checks)
 finally:client.close()
# Edits use a small, independently valid corpus with exact post-edit diagnostics.
for name,marked,label in [('template','entrypoint: wo<CURSOR>','worker'),('parameter',"container: {image: alpine, args: [\"{{inputs.parameters.me<CURSOR>}}\"]}",'message'),('bracket',"container: {image: alpine, args: [\"{{= inputs.parameters['me<CURSOR>'] }}\"]}",'message')]:
 head='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: completion}\nspec:\n'
 if name=='template':marked=head+'  '+marked+' # keep\n  templates:\n    - name: worker\n      container: {image: alpine}\n'
 else:marked=head+'  templates:\n    - name: worker\n      inputs: {parameters: [{name: message, default: hello}]}\n      '+marked+' # keep\n'
 at=marked.index('<CURSOR>');text=marked.replace('<CURSOR>','');prefix='wo' if name=='template' else 'me';expected=text[:at-len(prefix)]+label+text[at:];dest,work=m.setup('completion-'+name,{'case.yaml':text});path=work/'case.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);r=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':position(text,at)});items=[x for x in r.get('result',[]) if x['label']==label];checks={'unique':len(items)==1}
  if len(items)==1:
   actual=m.apply(text,{'documentChanges':[{'textDocument':{'uri':path.as_uri(),'version':1},'edits':[items[0]['textEdit']]}]},path.as_uri(),1);checks['exactBytes']=actual==expected;(dest/'expected.yaml').write_bytes(expected.encode());(dest/'actual.yaml').write_bytes(actual.encode());path.write_bytes(actual.encode());run=m.command(dest,'check',[m.BINARY,'check','--root',work,'--format','json']);report=json.loads(run.stdout);checks['fullCLI']=run.returncode==0 and report['diagnostics']==[] and report['filesAnalyzed']==1 and report['resourcesAnalyzed']==1 and report['analysisComplete'] is True and not report['configurationErrors']
  m.result('completion-'+name,checks,{'response':r})
 finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'helperSha256':m.sha(root/'tests/e2e/wf-audit-gaps.py'),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}})
sys.exit(0 if len(m.RESULTS)==7 and all(x['pass'] for x in m.RESULTS) else 1)
