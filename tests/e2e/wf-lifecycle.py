#!/usr/bin/env python3
"""Independent standard LSP lifecycle scenarios; no analysisStatus/test RPCs."""
import pathlib,sys,importlib.util,json,hashlib,time
root=pathlib.Path(__file__).resolve().parents[2]
if len(sys.argv)<3:sys.argv.append(str(root/'artifacts/wf-lifecycle'))
spec=importlib.util.spec_from_file_location('consumer',root/'tests/e2e/wf-audit-gaps.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
def notify_change(lsp,file,text,version):lsp.send('textDocument/didChange',{'textDocument':{'uri':file.as_uri(),'version':version},'contentChanges':[{'text':text}]})
base='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: main}\nspec: {parallelism: wrong}\n'
try:
 dest,work=a.setup('roots-buffers',{'case.yaml':base,'nested/other.yaml':a.BASE,'nested/.argo-workflows-ls.yaml':a.CONFIG,'chart/Chart.yaml':'apiVersion: v2\nname: example\nversion: 0.1.0\n','chart/templates/raw.yaml':base,'vendor/open.yaml':base})
 lsp=a.LSP(dest,work)
 try:
  file=work/'case.yaml';ds=lsp.open(file,base);assert any(x['code']=='SCHEMA_TYPE' for x in ds)
  # A workspace-folder change must move the nested resource out of the parent's index.
  lsp.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[{'uri':(work/'nested').as_uri(),'name':'nested'}],'removed':[]}})
  response=lsp.request('workspace/symbol',{'query':'work'})
  a.result('nested-root-single-owner',{'one_nested_symbol':len(response.get('result',[]))==1},response)
  raw=work/'chart/templates/raw.yaml';ds=lsp.open(raw,base)
  a.result('helm-open-skipped',{'no_raw_template_diagnostics':ds==[]},ds)
  ds=lsp.open(work/'vendor/open.yaml',base)
  a.result('excluded-open-single-file',{'explicit_buffer_analyzed':any(x['code']=='SCHEMA_TYPE' for x in ds)},ds)
  # Drain prior same-version publications with an ordinary request barrier.
  lsp.request('workspace/symbol',{'query':''})
  # Remove both roots; already published diagnostics must clear, never use a fallback cwd.
  lsp.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[],'removed':[{'uri':work.as_uri(),'name':'root'},{'uri':(work/'nested').as_uri(),'name':'nested'}]}})
  ds=lsp.diagnostics(file.as_uri(),1)
  response=lsp.request('textDocument/documentSymbol',{'textDocument':{'uri':file.as_uri()}})
  a.result('removed-root-clears',{'cleared':ds==[],'query_rejected':'error' in response},response)
 finally:lsp.close()
 dest,work=a.setup('interleaving',{'case.yaml':base});lsp=a.LSP(dest,work)
 try:
  file=work/'case.yaml';lsp.open(file,base)
  # Changes continue while queries arrive. Only the latest version may win.
  for v in range(2,22):
   notify_change(lsp,file,base if v%2 else base.replace('wrong','2'),v)
   if v in [5,11,17]:lsp.send('textDocument/hover',{'textDocument':{'uri':file.as_uri()},'position':{'line':3,'character':10}},100+v)
  ds=lsp.diagnostics(file.as_uri(),21)
  a.result('continuous-input-latest',{'latest_error':any(x['code']=='SCHEMA_TYPE' for x in ds)},ds)
  stable=lsp.request('textDocument/documentSymbol',{'textDocument':{'uri':file.as_uri()}})
  versions=[x['params']['version'] for x in lsp.received if x.get('method')=='textDocument/publishDiagnostics' and x['params'].get('uri')==file.as_uri() and 'version' in x['params']]
  replies=[x for x in lsp.received if x.get('id') in [105,111,117]]
  a.result('interleaved-requests-complete',{'one_response_each':sorted(x['id'] for x in replies)==[105,111,117],'standard_result_or_stale':all('result' in x or x.get('error',{}).get('code') in [-32801,-32800] for x in replies)},replies)
  a.result('old-result-rejected',{'versions_never_regress':versions==sorted(versions),'stable_request_completed':'result' in stable},versions)
  # Configuration invalidation and repair must use the current unsaved config buffer.
  cfg=work/'.argo-workflows-ls.yaml';lsp.send('textDocument/didOpen',{'textDocument':{'uri':cfg.as_uri(),'languageId':'yaml','version':1,'text':'schemaVersion: 1\ntargetVersion: "99.0.0"\n'}})
  ds=lsp.diagnostics(file.as_uri(),21);a.result('config-invalid-clears',{'cleared':ds==[]},ds)
  notify_change(lsp,cfg,a.CONFIG,2);ds=lsp.diagnostics(file.as_uri(),21);a.result('config-buffer-repair',{'error_returns':any(x['code']=='SCHEMA_TYPE' for x in ds)},ds)
  # Request cancellation gets its own terminal response before shutdown starts.
  huge='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: many}\nspec:\n  templates:\n'+''.join('  - name: t%d\n    container: {image: alpine}\n'%i for i in range(1200))
  notify_change(lsp,file,huge,22)
  lsp.send('textDocument/documentSymbol',{'textDocument':{'uri':file.as_uri()}},490)
  notify_change(lsp,file,base,23)
  stale=lsp.wait(lambda x:x.get('id')==490)
  a.result('request-old-epoch-rejected',{'content_modified':stale.get('error',{}).get('code')==-32801},stale)
  notify_change(lsp,file,huge,24)
  lsp.send('textDocument/documentSymbol',{'textDocument':{'uri':file.as_uri()}},500)
  lsp.send('$/cancelRequest',{'id':500})
  canceled=lsp.wait(lambda x:x.get('id')==500)
  a.result('cancel-acknowledged',{'request_cancelled':canceled.get('error',{}).get('code')==-32800},canceled)
  notify_change(lsp,file,base,25);ds=lsp.diagnostics(file.as_uri(),25)
  a.result('cancel-recovery',{'normal_diagnostic':any(x['code']=='SCHEMA_TYPE' for x in ds),'one_cancel_response':len([x for x in lsp.received if x.get('id')==500])==1},ds)
  start=time.monotonic();reply=lsp.request('shutdown');elapsed=time.monotonic()-start
  a.result('shutdown-after-cancel',{'shutdown_response':'result' in reply,'bounded':elapsed<5},reply)
  lsp.send('exit');lsp.p.wait(timeout=5)
 finally:lsp.close()
except Exception as e:a.result('runner-error',{'completed':False},{'error':repr(e)})
finally:
 sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
 a.save(a.OUT/'manifest.json',{'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(a.BINARY),'testSha256':sha(pathlib.Path(__file__)),'clientSha256':sha(root/'tests/e2e/wf-audit-gaps.py'),'inputHashes':{str(p.relative_to(a.OUT)):sha(p) for p in a.OUT.rglob('*.yaml')},'expectation':'independent named assertions, standard version/request-id completion; cancellation response precedes shutdown'})
sys.exit(0 if len(a.RESULTS)>=10 and all(x['pass'] for x in a.RESULTS) else 1)
