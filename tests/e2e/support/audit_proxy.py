#!/usr/bin/env python3
"""Transparent real-server fault injection; used only by oracle effectiveness E2E."""
import json, os, subprocess, sys, threading
p=subprocess.Popen([os.environ['WF_AUDIT_BINARY'],*sys.argv[1:]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=sys.stderr.buffer)
mode=os.environ.get('WF_AUDIT_FAULT','none');injected=False;first_uri=None

def read(stream):
 headers={}
 while True:
  line=stream.readline()
  if not line:return None
  if line==b'\r\n':break
  key,value=line.decode().split(':',1);headers[key.lower()]=value.strip()
 return json.loads(stream.read(int(headers['content-length'])))
def write(stream,value):
 data=json.dumps(value).encode();stream.write(f'Content-Length: {len(data)}\r\n\r\n'.encode()+data);stream.flush()
def upstream():
 while True:
  value=read(sys.stdin.buffer)
  if value is None:return
  if mode=='drop-root' and value.get('method')=='workspace/didChangeWorkspaceFolders':continue
  write(p.stdin,value)
t=threading.Thread(target=upstream,daemon=True);t.start()
while True:
 value=read(p.stdout)
 if value is None:break
 if value.get('method')=='textDocument/publishDiagnostics':
  params=value['params']
  if mode in ('bad-current','old-version') and not injected and params.get('version')==2 and params['diagnostics']==[]:
   write(sys.stdout.buffer,value);bad=json.loads(json.dumps(value));bad['params']['version']=2 if mode=='bad-current' else 1;bad['params']['diagnostics']=[{'code':'SCHEMA_TYPE','severity':1,'range':{'start':{'line':6,'character':15},'end':{'line':6,'character':20}},'message':'injected obsolete type error'}];write(sys.stdout.buffer,bad);injected=True;continue
 if mode=='wrong-root' and isinstance(value.get('result'),list):
  for location in value['result']:
   if isinstance(location,dict) and '/other/' in location.get('uri',''):location['uri']=location['uri'].replace('/other/','/workspace/')
 write(sys.stdout.buffer,value)
os._exit(p.wait())
