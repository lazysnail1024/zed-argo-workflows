#!/usr/bin/env python3
"""Real LSP warm-query authority, invalidation and fresh-context controls."""
from pathlib import Path
import importlib.util,json,sys,os,re,time,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
CONFIG=m.CONFIG+'defaultNamespace: argo\n'
CALLER='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo, annotations: {note: "{{workflow.parameters.message}}"}}\nspec:\n  workflowTemplateRef: {name: library}\n  entrypoint: main\n  arguments: {parameters: [{name: message, value: hello}]}\n'
LIBRARY='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library}\nspec:\n  templates:\n    - name: main\n      inputs: {parameters: [{name: message, default: hello}]}\n      container: {image: alpine}\n'
def location(path,text,needle,name):
 a=m.pos(text,needle,offset=len(needle)-len(name));b=dict(a);b['character']+=len(name);return {'uri':path.as_uri(),'range':{'start':a,'end':b}}
def norm(rows):return sorted(json.dumps(x,sort_keys=True) for x in rows)
class Client(m.LSP):
 def batch(self,rows):
  wire=bytearray()
  for method,params,ident in rows:
   message={'jsonrpc':'2.0','method':method,'params':params}
   if ident is not None:message['id']=ident
   self.sent.append(message);data=json.dumps(message).encode();wire.extend(('Content-Length: %d\r\n\r\n'%len(data)).encode()+data)
  self.p.stdin.write(wire);self.p.stdin.flush()
dest,work=m.setup('warm-invalidation',{'caller.yaml':CALLER,'nested/library.yaml':LIBRARY},CONFIG);cp=work/'caller.yaml';lp=work/'nested/library.yaml';cfg=work/'.argo-workflows-ls.yaml';caller=CALLER
argument=location(cp,CALLER,'name: message','message');input_decl=location(lp,LIBRARY,'name: message','message');wfuse=location(cp,CALLER,'parameters.message','message');resource=location(cp,CALLER,'name: library','library');client=None
expected_states={}
def query(method,selected,include=None):
 params={'textDocument':{'uri':selected['uri']},'position':selected['range']['start']}
 if include is not None:params['context']={'includeDeclaration':include}
 return client.request('textDocument/'+method,params)
def watch(path,kind=2):
 client.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':path.as_uri(),'type':kind}]})
def state(name,external,cli_severity=None,cli_count=2,fifo=False,settling=False,incomplete=False):
 expected={'definitions':[argument]+([input_decl] if external else []),'referencesFalse':[] if external else [wfuse],'referencesTrue':[] if external else [wfuse,argument],'diagnostics':[] if cli_severity is None else [dict(resource,code='WF_TEMPLATE_REF_UNRESOLVED',severity=cli_severity,source='argo-workflows',positionKind='exact')],'filesAnalyzed':cli_count,'resourcesAnalyzed':cli_count,'exitCode':2 if fifo else int(cli_severity=='Error'),'analysisComplete':not fifo}
 if incomplete:expected['referencesFalse']=[];expected['referencesTrue']=[]
 expected_states[name]=expected;m.save(dest/'expected.json',expected_states)
 stage=dest/name;stage.mkdir();(stage/'caller-buffer.yaml').write_text(caller);(stage/'config.yaml').write_text(cfg.read_text());(stage/'callee-state.json').write_text(json.dumps({'type':'fifo' if fifo else 'file' if lp.exists() else 'missing','text':lp.read_text() if lp.is_file() else None},indent=2)+'\n')
 responses={};checks={}
 for method,inc,key in [('definition',None,'definitions'),('references',False,'referencesFalse'),('references',True,'referencesTrue')]:
  r=query(method,argument,inc);first=r;attempts=[r]
  deadline=time.monotonic()+5
  while (settling or method=='references' and name=='same-uri-same-length-disk-change') and time.monotonic()<deadline and ('error' in r or norm(r.get('result',[]))!=norm(expected[key])):
   time.sleep(.02);r=query(method,argument,inc);attempts.append(r)
  responses[key]={'attempts':attempts,'settled':r} if settling else r
  if not external:checks[key+'NeverReturnsObsoleteCallee']=all(input_decl not in item.get('result',[]) for item in attempts)
  checks[key]='error' not in r and norm(r.get('result',[]))==norm(expected[key])
 code,cli=m.cli(stage,work);checks['exactCLI']=norm([{k:d[k] for k in ['uri','range','code','severity','source','positionKind']} for d in cli['diagnostics']])==norm(expected['diagnostics']);checks['countsExit']=code==(2 if fifo else int(cli_severity=='Error')) and cli['filesAnalyzed']==cli_count and cli['resourcesAnalyzed']==cli_count and cli['analysisComplete'] is (not fifo) and not cli['configurationErrors'];unknown=cli['notEvaluated'];base='complete domain rules, version acceptance, and resource budgets are not yet implemented';checks['unknownEnvelope']=(len(unknown)==1 and any('regular' in x for x in unknown) if fifo else unknown==[]);m.result(name,checks,{'responses':responses,'cli':cli})
try:
 client=Client(dest,work);client.open(cp,CALLER)
 state('warm',True);state('repeat-fresh-context',True)
 lp.write_text(LIBRARY.replace('message','messagh'));state('same-uri-same-length-disk-change',False)
 lp.write_text(LIBRARY);state('disk-restored',True,settling=True)
 lp.unlink();state('deleted-without-notification',False,'Error',1)
 os.mkfifo(lp);state('nonregular-without-notification',False,'Information',1,True)
 lp.unlink();lp.write_text(LIBRARY);watch(lp,1);state('regular-restored',True,settling=True)
 client.open(lp,LIBRARY.replace('message','messagh'));state('unsaved-overlay-authority',False)
 client.send('textDocument/didClose',{'textDocument':{'uri':lp.as_uri()}});state('close-restores-disk',True,settling=True)
 client.open(lp,LIBRARY);client.change(lp,LIBRARY+'#'+'x'*(5*1024*1024),2)
 refused=query('definition',input_decl);m.result('refused-selected-buffer',{'explicitRefusal':refused.get('error',{}).get('code')==-32602 and 'result' not in refused},{'response':refused});state('refusal-does-not-revive-callee',False,incomplete=True)
 client.change(lp,LIBRARY,3);state('full-replacement-recovers',True,settling=True);client.send('textDocument/didClose',{'textDocument':{'uri':lp.as_uri()}})
 cfg.write_text(CONFIG.replace('defaultNamespace: argo','defaultNamespace: other'));watch(cfg);state('namespace-option-change',False,'Error',settling=True)
 cfg.write_text(CONFIG);watch(cfg);state('namespace-option-restored',True,settling=True)
 cfg.write_text(CONFIG+'scopes:\n  - name: caller\n    include: [caller.yaml]\n  - name: library\n    include: [nested/*.yaml]\n');watch(cfg);state('scope-partition',False,'Error',settling=True)
 cfg.write_text(CONFIG);watch(cfg);state('scope-restored',True,settling=True)
 cfg.write_text(CONFIG.replace('3.6.0','9.9.9'));watch(cfg);r=query('definition',argument);stage=dest/'invalid-target';stage.mkdir();code,cli=m.cli(stage,work);m.result('invalid-target-after-warm',{'queryRefused':'error' in r and 'result' not in r,'honestCLI':code==2 and cli['filesAnalyzed']==0 and cli['resourcesAnalyzed']==0 and cli['analysisComplete'] is False and len(cli['configurationErrors'])==1},{'response':r,'cli':cli});cfg.write_text(CONFIG);watch(cfg);state('valid-target-restored',True,settling=True)
 (work/'nested/.argo-workflows-ls.yaml').write_text(CONFIG);client.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[{'uri':(work/'nested').as_uri(),'name':'nested'}],'removed':[]}});state('registered-root-isolation',False,settling=True)
 client.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[],'removed':[{'uri':(work/'nested').as_uri(),'name':'nested'}]}});state('root-restored',True,settling=True)
 latest=CALLER.replace('message','messagh');params={'textDocument':{'uri':cp.as_uri()},'position':argument['range']['start']};client.batch([('textDocument/definition',params,9000),('$/cancelRequest',{'id':9000},None),('textDocument/didChange',{'textDocument':{'uri':cp.as_uri(),'version':2},'contentChanges':[{'text':latest}]},None),('textDocument/definition',params,9001)])
 client.wait(lambda _:len([x for x in client.received if x.get('id') in [9000,9001]])>=2);replies=[x for x in client.received if x.get('id') in [9000,9001]];old=next(x for x in replies if x['id']==9000);new=next(x for x in replies if x['id']==9001)
 m.result('cancel-change-current-query',{'oneTerminalEach':sorted(x['id'] for x in replies)==[9000,9001],'honestOldTerminal':old.get('error',{}).get('code') in [-32800,-32801] or norm(old.get('result',[]))==norm([argument,input_decl]),'newBytesOnly':'error' not in new and new.get('result')==[argument]},{'responses':replies})
 caller=latest;state('fresh-context-after-cancel',False);caller=CALLER;client.change(cp,CALLER,3);state('restored-after-cancel',True)
except Exception:
 (dest/'error.txt').write_text(traceback.format_exc());m.result('completed',{'completed':False})
finally:
 if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'caseCount':len(m.RESULTS),'inputAndExpectedHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*') if p.is_file() and p.suffix in ['yaml','json'] and p.name not in ['manifest.json','results.json','traffic.json']}})
sys.exit(0 if len(m.RESULTS)==23 and all(x['pass'] for x in m.RESULTS) else 1)
