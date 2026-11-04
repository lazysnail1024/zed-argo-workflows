#!/usr/bin/env python3
"""Publication, cancellation and changed-version recovery on a live LSP process."""
import importlib.util,pathlib,sys,time,json,traceback
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');spec=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
text=m.REN_DAG;dest,work=m.setup('lifecycle',{'a.yaml':text,'b.yaml':text});c=m.LSP(dest,work);a=work/'a.yaml';b=work/'b.yaml'
try:
 c.open(a,text);c.open(b,text)
 # Cancellation has one terminal response and does not cancel later work.
 c.next_id+=1;ident=c.next_id;c.send('textDocument/references',{'textDocument':{'uri':a.as_uri()},'position':m.pos(text,'name: work',offset=7)},ident);c.send('$/cancelRequest',{'id':ident});r=c.wait(lambda x:x.get('id')==ident)
 broken=text.replace('  entrypoint:', '  parallelism: bad\n  entrypoint:');ds=c.change(a,broken,2);m.result('cancel-recovery',{'one_terminal':len([x for x in c.received if x.get('id')==ident])==1,'current_type_error':any(x['code']=='SCHEMA_TYPE' for x in ds)},{'response':r})
 for version in range(3,20):c.send('textDocument/didChange',{'textDocument':{'uri':a.as_uri(),'version':version},'contentChanges':[{'text':broken+'#'+str(version)}]})
 ds=c.diagnostics(a.as_uri(),19);m.result('latest-version',{'type_error':any(x['code']=='SCHEMA_TYPE' for x in ds)})
 c.send('textDocument/didClose',{'textDocument':{'uri':a.as_uri()}});c.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[],'removed':[{'uri':work.as_uri(),'name':'root'}]}});time.sleep(.5);updates=[x['params'] for x in c.received if x.get('method')=='textDocument/publishDiagnostics'];m.result('remove-root',{'cleared_b':any(x['uri']==b.as_uri() and x['diagnostics']==[] for x in updates[-3:])})
except Exception as e:m.result('runner',{'completed':False},{'error':repr(e),'traceback':traceback.format_exc()})
finally:c.close();m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputs':{str(p.relative_to(work)):m.sha(p) for p in work.rglob('*') if p.is_file()}})
sys.exit(0 if m.RESULTS and all(x['pass'] for x in m.RESULTS) else 1)
