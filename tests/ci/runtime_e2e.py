#!/usr/bin/env python3
"""Exercise actual ELF loader discovery across ldd formats and ASLR runs."""
import argparse, hashlib, json, os, pathlib, re, subprocess, sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--binary',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
binary=a.binary.resolve()
# Obtain the executable's actual interpreter from ELF metadata, not parser output.
elf=subprocess.run(['readelf','-l',str(binary)],capture_output=True,text=True,check=True)
(a.output/'readelf.stdout').write_text(elf.stdout);(a.output/'readelf.stderr').write_text(elf.stderr)
loader=re.search(r'Requesting program interpreter: ([^\]]+)\]',elf.stdout).group(1)
shim=a.output/'loader-bin';shim.mkdir();adapter=shim/'ldd'
adapter.write_text('#!'+sys.executable+'\nimport os,sys\nos.execv('+repr(loader)+', ['+repr(loader)+', "--list", *sys.argv[1:]])\n');adapter.chmod(0o755)
code='import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from build_identity import linux_runtime;print(json.dumps(linux_runtime(Path(sys.argv[2]))))'
rows=[];values=[]
for name,env in [('native-ldd',os.environ),('elf-loader-first',os.environ|{'PATH':str(shim.resolve())+os.pathsep+os.environ['PATH']}),('elf-loader-second',os.environ|{'PATH':str(shim.resolve())+os.pathsep+os.environ['PATH']})]:
 command=[sys.executable,'-c',code,str(ROOT/'tests/ci'),str(binary)]
 result=subprocess.run(command,env=env,capture_output=True);(a.output/(name+'.stdout')).write_bytes(result.stdout);(a.output/(name+'.stderr')).write_bytes(result.stderr)
 value=json.loads(result.stdout) if result.returncode==0 else None;values.append(value)
 rows.append({'case':name,'command':command,'exitCode':result.returncode,'pass':result.returncode==0})
if all(values):
 fingerprints=[v[0] for v in values]
 rows.append({'case':'same-runtime-bytes-across-formats-and-aslr','pass':fingerprints[0]==fingerprints[1]==fingerprints[2]})
 rows.append({'case':'real-interpreter-record-has-no-arrow','pass':all(any(loader in line and '=>' not in line for line in v[1]['stdout'].splitlines()) for v in values[1:])})
 rows.append({'case':'loader-hash-binds-actual-interpreter','pass':all(v[0]['libraries'].get(pathlib.Path(loader).name)==hashlib.sha256(pathlib.Path(loader).read_bytes()).hexdigest() for v in values)})
(a.output/'results.json').write_text(json.dumps(rows,indent=2))
(a.output/'manifest.json').write_text(json.dumps({'command':sys.argv,'python':sys.version,'scope':'Real ELF loader subprocesses; PATH adapter chooses --list but never synthesizes dependency output','binarySha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'implementationSha256':hashlib.sha256((ROOT/'tests/ci/build_identity.py').read_bytes()).hexdigest(),'loader':loader,'readelfCommand':elf.args},indent=2))
raise SystemExit(0 if all(r['pass'] for r in rows) else 1)
