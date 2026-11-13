#!/usr/bin/env python3
"""Independent real Workflows schema-query outcomes and actual-edit CLI checks."""
import pathlib,sys,json,importlib.util,hashlib
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
MARK='<CURSOR>';head='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: query}\n';base=head+'spec:\n';enum=['""','"Never"','"OnWorkflowCompletion"','"OnWorkflowDeletion"'];cases=[]
def key(name,body,label,old,new,errors):cases.append({'name':name,'marked':body,'label':label,'old':old,'new':new,'errors':errors})
key('partial-key-existing-value',base+'  par'+MARK+': 2 # keep\n','parallelism','par:','parallelism:',[])
key('quoted-key',base+"  'par"+MARK+"': 2 # keep\n",'parallelism',"'par':","'parallelism':",[])
key('blank-key',base+'  '+MARK+'\n','parallelism','  \n','  parallelism: \n',['SCHEMA_TYPE'])
key('first-sequence-partial',base+'  templates:\n    - na'+MARK+'\n','name','na\n','name: \n',['SCHEMA_TYPE'])
key('first-sequence-blank',base+'  templates:\n    - '+MARK+'\n','name','- \n','- name: \n',['SCHEMA_TYPE'])
key('existing-key-excluded',base+'  parallelism: 2\n  '+MARK+'\n','entrypoint','  \n','  entrypoint: \n',['SCHEMA_TYPE']);cases[-1]['excluded']=['parallelism']
key('typed-hook-map',base+'  hooks:\n    exit:\n      tem'+MARK+': main\n  templates:\n    - name: main\n      container: {image: alpine}\n','template','tem:','template:',[])
flow='{apiVersion: argoproj.io/v1alpha1, kind: Workflow, metadata: {name: "\U0001f600"}, spec: {par'+MARK+': 2}}\n'
key('utf16-flow-key',flow,'parallelism','par:','parallelism:',[])
for name,fragment,replaced in [('enum-empty','strategy: '+MARK,'strategy: "Never"'),('enum-empty-colon','strategy:'+MARK,'strategy: "Never"'),('enum-partial','strategy: Ne'+MARK,'strategy: "Never"'),('enum-quoted','strategy: "Ne'+MARK+'"','strategy: "Never"'),('enum-comment','strategy: '+MARK+'# keep','strategy: "Never" # keep'),('enum-colon-comment','strategy:'+MARK+'# keep','strategy: "Never" # keep')]:
 body=base+'  artifactGC:\n    '+fragment+'\n';key(name,body,'"Never"',fragment.replace(MARK,''),replaced,[]);cases[-1]['labels']=enum
key('enum-flow',head+'spec: {artifactGC: {strategy: Ne'+MARK+'}, parallelism: 2}\n','"Never"','strategy: Ne','strategy: "Never"',[]);cases[-1]['labels']=enum
key('enum-empty-string',base+'  artifactGC:\n    strategy: '+MARK+'\n','""','strategy: ','strategy: ""',[]);cases[-1]['labels']=enum
key('crlf-comment', (base+'  artifactGC:\n    strategy: Ne'+MARK+' # keep\n').replace('\n','\r\n'),'"Never"','strategy: Ne','strategy: "Never"',[]);cases[-1]['labels']=enum
key('existing-annotation-key',head.replace('metadata: {name: query}', 'metadata: {name: query, annotations: {__argo_query_key__: kept}}')+'spec:\n  par'+MARK+': 2\n','parallelism','par:','parallelism:',[])
key('existing-annotation-value',head.replace('metadata: {name: query}', 'metadata: {name: query, annotations: {note: __argo_query_value__}}')+'spec:\n  artifactGC:\n    strategy: Ne'+MARK+'\n','"Never"','strategy: Ne','strategy: "Never"',[]);cases[-1]['labels']=enum
class Client(m.LSP):
 snippets=False
 def send(self,method,params=None,ident=None):
  if method=='initialize':params['capabilities']['textDocument']={'completion':{'completionItem':{'snippetSupport':self.snippets}}}
  return super().send(method,params,ident)
def complete(case,snippets):
 name=case['name']+('-snippet-client' if snippets else '-plain-client');text=case['marked'].replace(MARK,'');dest,work=m.setup(name,{'case.yaml':text});path=work/'case.yaml';client=None
 try:
  Client.snippets=snippets;client=Client(dest,work);client.open(path,text);position=m.pos(case['marked'],MARK);r=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':position});items=r.get('result',[]);labels=[x['label'] for x in items];matches=[x for x in items if x['label']==case['label']];checks={'candidate':len(matches)==1,'existingExcluded':not any(x in labels for x in case.get('excluded',[]))};checks.update(plainTextUsable=False,exactTextEditPresent=False,exactBytes=False,actualCLICompleteResource=False,acceptedTarget=False,exactActualDiagnostics=False)
  if 'labels' in case:checks['exactEnum']=sorted(labels)==sorted(case['labels'])
  if matches:
   item=matches[0];checks['plainTextUsable']=item.get('insertTextFormat',1)==1;edit=item.get('textEdit');checks['exactTextEditPresent']=bool(edit)
   if edit:
    actual=m.apply(text,{'documentChanges':[{'textDocument':{'uri':path.as_uri(),'version':1},'edits':[edit]}]},path.as_uri(),1);expected=text.replace(case['old'],case['new'],1);checks['exactBytes']=actual==expected;path.write_bytes(actual.encode());(dest/'expected.yaml').write_bytes(expected.encode());run=m.command(dest,'applied-check',[m.BINARY,'check','--root',work,'--format','json']);report=json.loads(run.stdout);checks['actualCLICompleteResource']=run.returncode==(1 if case['errors'] else 0) and report.get('analysisComplete') is True and report.get('filesAnalyzed')==1 and report.get('resourcesAnalyzed')==1 and report.get('configurationErrors')==[] and report.get('notEvaluated')==[];checks['acceptedTarget']=len(report.get('targets',[]))==1 and report['targets'][0].get('version')=='3.6.0' and report['targets'][0].get('analysisComplete') is True and report['targets'][0].get('experimental') is False;checks['exactActualDiagnostics']=sorted((d['code'],d['severity']) for d in report['diagnostics'])==sorted((code,'Error') for code in case['errors'])
  m.result(name,checks,{'response':r})
 except Exception as e:m.result(name,{'completed':False},{'error':repr(e)})
 finally:
  if client:client.close()
for case in cases:
 for snippets in [False,True]:complete(case,snippets)
# Exact types, annotations and source provenance from the locked3.6.0 CRD.
hovers=[('integer',base+'  parallel'+MARK+'ism: 2\n',['integer']),('quantity-union',base+'  templates:\n    - name: main\n      container:\n        image: alpine\n        resources:\n          limits:\n            cp'+MARK+'u: "1"\n',['integer','string']),('protocol-default',base+'  templates:\n    - name: main\n      container:\n        image: alpine\n        ports:\n          - containerPort: 80\n            proto'+MARK+'col: TCP\n',['string','Default','TCP']),('typed-hook',base+'  hooks:\n    exit:\n      tem'+MARK+'plate: main\n',['string'])]
hovers += [('plain-colon-block',base+'  templates:\n    - name: main\n      container:\n        image: alpine:3'+MARK+'\n',['Type: `string`']),('plain-colon-value',base+'  templates:\n    - name: main\n      container: {image: alpine:3'+MARK+'}\n',['Type: `string`'])]
hovers += [('api-version-key',head.replace('apiVersion','apiVer'+MARK+'sion')+'spec: {}\n',['string']),('kind-value',head.replace('kind: Workflow','kind: Work'+MARK+'flow')+'spec: {}\n',['string'])]
for name,marked,needles in hovers:
 text=marked.replace(MARK,'');dest,work=m.setup('hover-'+name,{'case.yaml':text});client=Client(dest,work);path=work/'case.yaml'
 try:
  client.open(path,text);r=client.request('textDocument/hover',{'textDocument':{'uri':path.as_uri()},'position':m.pos(marked,MARK)});value=r.get('result') or {};content=value.get('contents',{}).get('value','');m.result('hover-'+name,{'typeAndAnnotations':all(x in content for x in needles),'noNil':'<nil>' not in content,'target':'3.6.0' in content,'sourceCommit':'b26ed4aa4dee395844531efa4a76a022183bec22' in content,'exactRangePresent':bool(value.get('range'))},{'response':r})
 finally:client.close()
for name,marked in [('comment',base+'  # par'+MARK+' comment\n'),('foreign',head+'spec: {}\n---\napiVersion: argoproj.io/v1alpha1\nkind: Application\nspec:\n  par'+MARK+'\n'),('unknown-gvk','apiVersion: argoproj.io/v1alpha1\nkind: Unknown\nspec:\n  par'+MARK+'\n')]:
 text=marked.replace(MARK,'');dest,work=m.setup('filtered-'+name,{'case.yaml':text});client=Client(dest,work);path=work/'case.yaml'
 try:
  client.open(path,text);params={'textDocument':{'uri':path.as_uri()},'position':m.pos(marked,MARK)};c=client.request('textDocument/completion',params);h=client.request('textDocument/hover',params);m.result('filtered-'+name,{'noCompletion':c.get('result')==[],'noHover':h.get('result') is None},{'completion':c,'hover':h})
 finally:client.close()
m.save(m.OUT/'manifest.json',{'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'command':sys.argv,'fixtureType':'actual embedded Workflows3.6.0 CRD','inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}});sys.exit(0 if len(m.RESULTS)==len(cases)*2+11 and all(x['pass'] for x in m.RESULTS) else 1)
