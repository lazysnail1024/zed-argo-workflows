#!/usr/bin/env python3
"""Transport controls use actual child processes, never fake product semantics."""
import argparse, hashlib, json, pathlib, sys, os, signal
from support.session import Session
p=argparse.ArgumentParser();p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
peers={'early-exit-child':"import subprocess,sys;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)']);Path(sys.argv[1]).write_text(str(p.pid))",'malformed-frame':"import sys;sys.stdout.buffer.write(b'Content-Length: nope\\r\\n\\r\\n');sys.stdout.flush()",'short-frame':"import sys;sys.stdout.buffer.write(b'Content-Length: 20\\r\\n\\r\\n{}');sys.stdout.flush()",'early-exit':'pass','no-response':'import time;time.sleep(20)','stderr-over-quota':"import sys,time;sys.stderr.buffer.write(b'x'*2097152);sys.stderr.flush();time.sleep(20)"}
if os.name!='nt':peers['cleanup-signal-race']='import time;time.sleep(20)'
if sys.platform.startswith('linux'):
 peers.update({'cleanup-exited-group-race':'pass','cleanup-live-group-denied':'import time;time.sleep(20)','cleanup-exited-group-denied':'pass','cleanup-exited-existing-group':peers['early-exit-child']})
rows=[]
for name,source in peers.items():
 folder=a.output/name;folder.mkdir();peer=folder/'peer.py';peer.write_text(source)
 s=Session([sys.executable,str(peer),str(folder/'descendant.pid')],folder,timeout=.5,quota=1024*1024)
 error=None;cleanup_error=None;signals=[];real_killpg=getattr(os,'killpg',None);real_poll=s.proc.poll;real_wait=s.proc.wait
 if name=='early-exit-child':s.proc.wait(timeout=5)
 try:s.wait(lambda m:False)
 except Exception as e:error=type(e).__name__+': '+str(e)
 if name=='cleanup-signal-race':
  # Model Darwin's observed exit interleaving, retaining the real signal/wait.
  wait_started=[False]
  def killpg(pid,sig):
   signals.append([pid,sig])
   if len(signals)>1:raise PermissionError(1,'Operation not permitted: already signaled group')
   return real_killpg(pid,sig)
  def wait(*args,**kwargs):wait_started[0]=True;return real_wait(*args,**kwargs)
  s.proc.poll=lambda:real_poll() if wait_started[0] else None
  s.proc.wait=wait;os.killpg=killpg
 if name in ('cleanup-exited-group-race','cleanup-exited-group-denied','cleanup-live-group-denied','cleanup-exited-existing-group'):
  if name!='cleanup-live-group-denied':os.waitid(os.P_PID,s.proc.pid,os.WEXITED|os.WNOWAIT)
  def killpg(pid,sig):
   signals.append([pid,sig])
   if name not in ('cleanup-exited-group-race','cleanup-exited-existing-group') or len(signals)==1:raise PermissionError(1,'Operation not permitted: injected kernel group state')
   return real_killpg(pid,sig)
  os.killpg=killpg
 try:s.close()
 except Exception as e:cleanup_error=type(e).__name__+': '+str(e)
 finally:
  if real_killpg is not None:os.killpg=real_killpg
  s.proc.poll=real_poll;s.proc.wait=real_wait
 # Capture helper outcome before emergency test hygiene; cleanup cannot earn pass.
 reaped=real_poll() is not None;drained=not any(t.is_alive() for t in s.threads)
 if cleanup_error:
  (folder/'cleanup-error.json').write_text(json.dumps({'error':cleanup_error,'signals':signals,'helperReaped':reaped,'helperDrained':drained},indent=2))
  (folder/'failed-stdout.raw').write_bytes(s.raw);(folder/'failed-stderr.raw').write_bytes(s.stderr)
  if os.name!='nt':
   try:real_killpg(s.proc.pid,signal.SIGKILL)
   except ProcessLookupError:pass
  elif not reaped:s.proc.kill()
  real_wait(timeout=5)
  for t in s.threads:t.join(timeout=2)
 descendant_gone=True
 if name in ('early-exit-child','cleanup-exited-existing-group'):
  pid=int((folder/'descendant.pid').read_text())
  if os.name=='nt':
   import ctypes
   k=ctypes.WinDLL('kernel32',use_last_error=True);k.OpenProcess.restype=ctypes.c_void_p
   h=k.OpenProcess(0x100000,False,pid)
   if h:
    k.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];k.CloseHandle.argtypes=[ctypes.c_void_p]
    descendant_gone=k.WaitForSingleObject(h,3000)==0;k.CloseHandle(h)
  elif pathlib.Path('/proc').exists():
   state=pathlib.Path(f'/proc/{pid}/stat')
   try:descendant_gone=state.read_text().split()[2]=='Z'
   except (FileNotFoundError,ProcessLookupError):descendant_gone=True
 refused=name in ('cleanup-live-group-denied','cleanup-exited-group-denied','cleanup-exited-existing-group')
 expected_refusal=bool(cleanup_error and cleanup_error.startswith('PermissionError:')) and (name!='cleanup-live-group-denied' or not reaped)
 rows.append({'case':name,'platform':sys.platform,'descendantReaped':descendant_gone,'error':error,'cleanupError':cleanup_error,'cleanupSignals':signals,'expectedCleanupRefusal':refused,'pass':expected_refusal and descendant_gone if refused else not cleanup_error and descendant_gone and bool(error) and reaped and drained,'peerSha256':hashlib.sha256(peer.read_bytes()).hexdigest()})
 (a.output/'results.json').write_text(json.dumps(rows,indent=2))
(a.output/'manifest.json').write_text(json.dumps({'command':sys.argv,'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'controlScope':'Actual subprocesses; cleanup controls inject only observed Darwin errno/scheduling while retaining real process lifetime and group probes'},indent=2))
raise SystemExit(0 if all(r['pass'] for r in rows) else 1)
