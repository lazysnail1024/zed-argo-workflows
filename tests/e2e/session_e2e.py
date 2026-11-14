#!/usr/bin/env python3
"""Transport controls use actual child processes, never fake product semantics."""
import argparse, hashlib, json, pathlib, sys, os, time
from support.session import Session
p=argparse.ArgumentParser();p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
peers={'early-exit-child':"import subprocess,sys;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)']);Path(sys.argv[1]).write_text(str(p.pid))",'malformed-frame':"import sys;sys.stdout.buffer.write(b'Content-Length: nope\\r\\n\\r\\n');sys.stdout.flush()",'short-frame':"import sys;sys.stdout.buffer.write(b'Content-Length: 20\\r\\n\\r\\n{}');sys.stdout.flush()",'early-exit':'pass','no-response':'import time;time.sleep(20)','stderr-over-quota':"import sys,time;sys.stderr.buffer.write(b'x'*2097152);sys.stderr.flush();time.sleep(20)"}
rows=[]
for name,source in peers.items():
 folder=a.output/name;folder.mkdir();peer=folder/'peer.py';peer.write_text(source)
 s=Session([sys.executable,str(peer),str(folder/'descendant.pid')],folder,timeout=.5,quota=1024*1024)
 error=None
 if name=='early-exit-child':s.proc.wait(timeout=5)
 try:s.wait(lambda m:False)
 except Exception as e:error=type(e).__name__+': '+str(e)
 finally:s.close()
 descendant_gone=True
 if name=='early-exit-child':
  pid=int((folder/'descendant.pid').read_text())
  if os.name=='nt':
   import ctypes
   k=ctypes.WinDLL('kernel32',use_last_error=True);k.OpenProcess.restype=ctypes.c_void_p
   h=k.OpenProcess(0x100000,False,pid)
   if h:
    k.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];k.CloseHandle.argtypes=[ctypes.c_void_p]
    descendant_gone=k.WaitForSingleObject(h,3000)==0;k.CloseHandle(h)
  elif pathlib.Path('/proc').exists():
   state=pathlib.Path(f'/proc/{pid}/stat');descendant_gone=not state.exists() or state.read_text().split()[2]=='Z'
 rows.append({'case':name,'platform':sys.platform,'descendantReaped':descendant_gone,'error':error,'pass':descendant_gone and bool(error) and s.proc.poll() is not None and not any(t.is_alive() for t in s.threads),'peerSha256':hashlib.sha256(peer.read_bytes()).hexdigest()})
(a.output/'results.json').write_text(json.dumps(rows,indent=2));(a.output/'manifest.json').write_text(json.dumps({'command':sys.argv,'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()},indent=2))
raise SystemExit(0 if all(r['pass'] for r in rows) else 1)
