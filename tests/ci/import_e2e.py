#!/usr/bin/env python3
"""Black-box ordinary import controls against the newly compiled real server."""
import argparse,hashlib,json,pathlib,shutil,subprocess,sys
p=argparse.ArgumentParser();p.add_argument('--build',type=pathlib.Path,required=True);p.add_argument('--version',required=True);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
root=pathlib.Path(__file__).resolve().parents[2];original=json.loads((a.build/'build.json').read_text());binary=a.build/'argo-workflows-ls';rows=[]
for name in ['valid','wrong-sha','wrong-attempt','wrong-role','wrong-mode','wrong-version','wrong-compiler','wrong-flags','bad-binary','unknown-schema','image-patch-compatible','wrong-image-os','missing-runtime-abi','wrong-runtime-abi']:
 folder=a.output/name;folder.mkdir();manifest=folder/'build.json';value=json.loads(json.dumps(original));selected=binary
 if name=='image-patch-compatible':value['runnerImage']['version']+='-other-hosted-patch'
 if name=='wrong-image-os':value['runnerImage']['os']='incompatible-os'
 if name=='missing-runtime-abi':value.pop('runtimeABI',None)
 if name=='wrong-runtime-abi':value['runtimeABI']={'schemaVersion':1,'libraries':{'libc.so.6':'0'*64}}
 if name=='wrong-sha':value['sourceCommit']='0'*40
 if name=='wrong-attempt':value['producer']['attempt']='0'
 if name=='wrong-role':value['buildRole']='mutation'
 if name=='wrong-mode':value['buildMode']='development'
 if name=='wrong-version':value['productVersion']='0.0.0'
 if name=='wrong-compiler':value['compiler']='unknown compiler'
 if name=='wrong-flags':value['linkerFlags']='-s'
 if name=='unknown-schema':value['schemaVersion']=99
 if name=='bad-binary':
  selected=folder/'server';shutil.copy2(binary,selected)
  with selected.open('ab') as f:f.write(b'corrupt')
 manifest.write_text(json.dumps(value));digest=hashlib.sha256(manifest.read_bytes()).hexdigest()
 command=[sys.executable,str(root/'tests/ci/run.py'),'import-build','--binary',str(selected),'--manifest',str(manifest),'--manifest-sha256',digest,'--release-version',a.version,'--platform','linux/amd64','--output',str(folder/'result')]
 r=subprocess.run(command,capture_output=True);(folder/'stdout.log').write_bytes(r.stdout);(folder/'stderr.log').write_bytes(r.stderr);rows.append({'case':name,'command':command,'exitCode':r.returncode,'pass':(r.returncode==0)==(name in ('valid','image-patch-compatible'))})
 (a.output/'results.json').write_text(json.dumps(rows,indent=2))
command=[sys.executable,str(root/'tests/ci/runtime_e2e.py'),'--binary',str(binary),'--output',str(a.output/'runtime-loader-controls')]
r=subprocess.run(command,capture_output=True);(a.output/'runtime.stdout.log').write_bytes(r.stdout);(a.output/'runtime.stderr.log').write_bytes(r.stderr)
rows.append({'case':'real-loader-runtime-controls','command':command,'exitCode':r.returncode,'pass':r.returncode==0})
(a.output/'results.json').write_text(json.dumps(rows,indent=2))
raise SystemExit(0 if all(r['pass'] for r in rows) else 1)
