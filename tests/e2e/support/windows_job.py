"""Windows peer ownership, including descendants after the peer exits."""
import ctypes
from ctypes import wintypes as w

class Job:
 def __init__(self):
  self.k=ctypes.WinDLL('kernel32',use_last_error=True)
  signatures={'CreateJobObjectW':([ctypes.c_void_p,w.LPCWSTR],w.HANDLE),'SetInformationJobObject':([w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD],w.BOOL),'AssignProcessToJobObject':([w.HANDLE,w.HANDLE],w.BOOL),'CloseHandle':([w.HANDLE],w.BOOL),'CreateToolhelp32Snapshot':([w.DWORD,w.DWORD],w.HANDLE),'Thread32First':([w.HANDLE,ctypes.c_void_p],w.BOOL),'Thread32Next':([w.HANDLE,ctypes.c_void_p],w.BOOL),'OpenThread':([w.DWORD,w.BOOL,w.DWORD],w.HANDLE),'ResumeThread':([w.HANDLE],w.DWORD)}
  for name,(args,result) in signatures.items():
   fn=getattr(self.k,name);fn.argtypes=args;fn.restype=result
  class Basic(ctypes.Structure):
   _fields_=[('perProcess',ctypes.c_longlong),('perJob',ctypes.c_longlong),('flags',w.DWORD),('minWorking',ctypes.c_size_t),('maxWorking',ctypes.c_size_t),('activeLimit',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
  class IO(ctypes.Structure):
   _fields_=[(n,ctypes.c_ulonglong) for n in ('readOps','writeOps','otherOps','readBytes','writeBytes','otherBytes')]
  class Extended(ctypes.Structure):
   _fields_=[('basic',Basic),('io',IO),('processMemory',ctypes.c_size_t),('jobMemory',ctypes.c_size_t),('peakProcessMemory',ctypes.c_size_t),('peakJobMemory',ctypes.c_size_t)]
  self.handle=self.k.CreateJobObjectW(None,None)
  if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
  limits=Extended();limits.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
  if not self.k.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
   error=ctypes.WinError(ctypes.get_last_error());self.close();raise error
 def start(self,process):
  if not self.k.AssignProcessToJobObject(self.handle,int(process._handle)):
   raise ctypes.WinError(ctypes.get_last_error())
  class Thread(ctypes.Structure):
   _fields_=[('size',w.DWORD),('usage',w.DWORD),('id',w.DWORD),('owner',w.DWORD),('basePriority',w.LONG),('deltaPriority',w.LONG),('flags',w.DWORD)]
  snapshot=self.k.CreateToolhelp32Snapshot(4,0)
  if snapshot==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
  try:
   entry=Thread();entry.size=ctypes.sizeof(entry);found=self.k.Thread32First(snapshot,ctypes.byref(entry))
   while found:
    if entry.owner==process.pid:
     thread=self.k.OpenThread(2,False,entry.id)
     if not thread:raise ctypes.WinError(ctypes.get_last_error())
     try:
      if self.k.ResumeThread(thread)==0xffffffff:raise ctypes.WinError(ctypes.get_last_error())
     finally:self.k.CloseHandle(thread)
     return
    found=self.k.Thread32Next(snapshot,ctypes.byref(entry))
   raise RuntimeError('Suspended peer has no primary thread')
  finally:self.k.CloseHandle(snapshot)
 def close(self):
  if self.handle:
   if not self.k.CloseHandle(self.handle):raise ctypes.WinError(ctypes.get_last_error())
   self.handle=None
