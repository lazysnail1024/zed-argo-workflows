"""Mechanical stdio framing and owned process lifecycle; no domain oracle."""
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time

class Session:
 def __init__(self,command,output,timeout=15,quota=32*1024*1024):
  self.output=Path(output);self.output.mkdir(parents=True,exist_ok=True)
  self.timeout=timeout;self.quota=quota;self.sent=[];self.received=[];self.raw=bytearray();self.stderr=bytearray();self.pending=[];self.q=queue.Queue();self.threads=[]
  self.timeline=[]
  self.job=None
  if os.name=='nt':
   from .windows_job import Job
   self.job=Job()
  try:
   self.proc=subprocess.Popen([str(x) for x in command],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**({'start_new_session':True} if os.name!='nt' else {'creationflags':0x4}))
   if self.job:self.job.start(self.proc)
  except Exception:
   if hasattr(self,'proc'):
    self.proc.kill();self.proc.wait(timeout=10)
   if self.job:self.job.close()
   raise
  for fn in (self._read,self._stderr):
   t=threading.Thread(target=fn,daemon=True);t.start();self.threads.append(t)
 def _stderr(self):
  signaled=False
  try:
   while True:
    chunk=self.proc.stderr.read1(65536)
    if not chunk:return
    remaining=self.quota-len(self.stderr);self.stderr.extend(chunk[:max(0,remaining)])
    if len(chunk)>remaining and not signaled:
     signaled=True
     self.q.put(RuntimeError('stderr quota exceeded; bounded prefix retained'))
     # Keep draining to avoid blocking the child before owner cleanup.
  except Exception as e:self.q.put(e)
 def _read(self):
  try:
   while True:
    headers={};size=0
    while True:
     line=self.proc.stdout.readline(8193);self.raw.extend(line);size+=len(line)
     if not line:raise EOFError('server exited before awaited message')
     if size>8192:raise ValueError('oversized LSP header')
     if line==b'\r\n':break
     key,value=line.decode('ascii').strip().split(':',1)
     if key.lower() in headers:raise ValueError('duplicate LSP header')
     headers[key.lower()]=value.strip()
    length=int(headers['content-length'])
    if not 0<length<=self.quota:raise ValueError('invalid frame length')
    payload=self.proc.stdout.read(length);self.raw.extend(payload)
    if len(payload)!=length:raise EOFError('truncated LSP frame')
    if len(self.raw)>self.quota:raise ValueError('stdout quota exceeded')
    value=json.loads(payload)
    if not isinstance(value,dict) or value.get('jsonrpc')!='2.0':raise ValueError('invalid JSON-RPC message')
    self.received.append(value);self.timeline.append({'direction':'received','message':value});self.q.put(value)
  except Exception as e:self.q.put(e)
 def send(self,method,params=None,id=None):
  value={'jsonrpc':'2.0','method':method}
  if params is not None:value['params']=params
  if id is not None:value['id']=id
  data=json.dumps(value).encode();self.sent.append(value);self.timeline.append({'direction':'sent','message':value})
  self.proc.stdin.write(f'Content-Length: {len(data)}\r\n\r\n'.encode()+data);self.proc.stdin.flush()
 def wait(self,predicate,timeout=None):
  deadline=time.monotonic()+(self.timeout if timeout is None else timeout)
  for i,value in enumerate(self.pending):
   if predicate(value):return self.pending.pop(i)
  while True:
   remaining=deadline-time.monotonic()
   if remaining<=0:raise TimeoutError('LSP response deadline exceeded')
   try:value=self.q.get(timeout=remaining)
   except queue.Empty:raise TimeoutError('LSP response deadline exceeded') from None
   if isinstance(value,Exception):raise value
   if predicate(value):return value
   self.pending.append(value)
 def diagnostics(self,uri,version,predicate=lambda params:True):
  return self.wait(lambda m:m.get('method')=='textDocument/publishDiagnostics' and m.get('params',{}).get('uri')==uri and m['params'].get('version')==version and predicate(m['params']))['params']
 def close(self):
  if self.job:self.job.close()
  if os.name!='nt':
   try:os.killpg(self.proc.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   except PermissionError as error:
    # Darwin can report EPERM for an exited, unreaped group leader.
    # Reap first, then accept only proof the group no longer exists.
    try:self.proc.wait(timeout=10)
    except subprocess.TimeoutExpired:raise error
    try:os.killpg(self.proc.pid,0)
    except ProcessLookupError:pass
    else:raise
  if os.name=='nt' and self.proc.poll() is None:self.proc.kill()
  self.proc.wait(timeout=10)
  for t in self.threads:t.join(timeout=2)
  (self.output/'stdout.raw').write_bytes(self.raw);(self.output/'stderr.raw').write_bytes(self.stderr)
  (self.output/'traffic.json').write_text(json.dumps({'sent':self.sent,'received':self.received,'timeline':self.timeline},indent=2))
  (self.output/'lifecycle.json').write_text(json.dumps({'exitCode':self.proc.returncode,'reaped':self.proc.poll() is not None,'threadsDrained':not any(t.is_alive() for t in self.threads)}))
