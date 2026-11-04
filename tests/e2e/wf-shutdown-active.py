#!/usr/bin/env python3
import pathlib,sys,time,json,hashlib,importlib.util
HERE=pathlib.Path(__file__).resolve().parent
sys.dont_write_bytecode=True
if len(sys.argv)<3:sys.argv.append(str(HERE.parent.parent/'artifacts/wf-lifecycle-fixes/shutdown-active'))
spec=importlib.util.spec_from_file_location('independent',HERE/'wf-lifecycle-gaps.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
dest,work=a.setup('active-and-queued-shutdown',{'large.yaml':a.huge(1200),'small.yaml':a.SMALL});c=a.Client(dest,work)
try:
 requests=[('textDocument/documentSymbol',a.symbol_params(work/'large.yaml'),3000)]
 requests += [('textDocument/documentSymbol',a.symbol_params(work/'small.yaml'),i) for i in range(3001,3021)]
 requests += [('shutdown',None,3999)]
 start=time.monotonic();c.batch(requests)
 replies=c.wait(lambda ms:ms if len([m for m in ms if m.get('id') in list(range(3000,3021))+[3999]])>=22 else None,5)
 c.send('exit');c.p.wait(timeout=5);elapsed=time.monotonic()-start
 terminal=[m for m in replies if m.get('id') in range(3000,3021)]
 a.result('active-and-queued-shutdown',{'one_terminal_each':sorted(m['id'] for m in terminal)==list(range(3000,3021)),'active_cancelled':next(m for m in terminal if m['id']==3000).get('error',{}).get('code')==-32800,'shutdown_replied':any(m.get('id')==3999 and 'result' in m for m in replies),'clean_bounded_process_exit':c.p.returncode==0 and elapsed<5},{'responses':terminal,'elapsedSeconds':elapsed,'exitCode':c.p.returncode})
finally:c.close()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
a.save(a.OUT/'manifest.json',{'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(a.BINARY),'testSha256':sha(HERE/'wf-shutdown-active.py'),'clientSha256':sha(HERE/'wf-lifecycle-gaps.py'),'expectedSha256':sha(HERE/'wf-shutdown-active.expected.json'),'inputHashes':{str(p.relative_to(a.OUT)):sha(p) for p in (a.OUT).rglob('*.yaml')}})
sys.exit(0 if a.RESULTS and all(x['pass'] for x in a.RESULTS) else 1)
