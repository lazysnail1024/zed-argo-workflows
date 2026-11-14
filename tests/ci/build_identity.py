"""Ordinary producer receipt and explicit read-only import validation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from control import ROOT, identity, sha, require, git

def input_digest():
 paths=['go.mod','go.sum','vendor/modules.txt','contracts/toolkit.lock.json','tests/ci/contracts.json']
 values={p:sha(ROOT/p) for p in paths}
 values['nativeHeaders']=hashlib.sha256('\n'.join(str(p.relative_to(ROOT))+':'+sha(p) for p in sorted(p for p in (ROOT/'vendor').rglob('*') if p.is_file())).encode()).hexdigest()
 return values

def enrich(metadata, binary, native, flags):
 return metadata | {'schemaVersion':1,'repository':identity()['repository'],'sourceTree':git('rev-parse','HEAD^{tree}'),'dirty':bool(metadata['sourceStatus']),'buildRole':'ordinary','producer':identity() | {'job':os.environ.get('GITHUB_JOB','local')},'buildMode':native['buildMode'],'productVersion':native['serverVersion'],'inputs':input_digest(),'linkerFlags':flags,'runnerImage':{'os':os.environ.get('ImageOS','local'),'version':os.environ.get('ImageVersion','local')},'binary':str(binary),'binarySha256':sha(binary),'identity':native}

def verify(binary, manifest, expected_digest, version, platform):
 require(sha(manifest)==expected_digest,'producer manifest digest mismatch')
 row=json.loads(manifest.read_text())
 require(row.get('schemaVersion')==1,'unsupported build schema')
 require(row.get('buildRole')=='ordinary','only ordinary bytes may be imported')
 require(row.get('sourceCommit')==git('rev-parse','HEAD') and row.get('sourceTree')==git('rev-parse','HEAD^{tree}'),'build source mismatch')
 require(row.get('dirty') is False and not git('status','--porcelain'),'import requires clean source and producer')
 expected=identity();require(all(row.get('producer',{}).get(k)==v for k,v in expected.items()),'producer run/attempt mismatch; rerun producer and all consumers')
 require(row.get('platform')==platform and row.get('inputs')==input_digest(),'platform or dependency/contract mismatch')
 require(row.get('repository')==expected['repository'],'repository mismatch')
 require(row.get('go')==subprocess.check_output(['go','version'],text=True),'Go toolchain mismatch')
 require(row.get('compiler')==subprocess.check_output([os.environ.get('CC','cc'),'--version'],text=True),'C compiler mismatch')
 goenv=json.loads(row['goEnvironment']); current=json.loads(subprocess.check_output(['go','env','-json'],text=True))
 for key in ('GOOS','GOARCH','GOVERSION','CGO_ENABLED','CC','CGO_CFLAGS','CGO_CPPFLAGS','CGO_CXXFLAGS','CGO_FFLAGS','CGO_LDFLAGS'):
  require(goenv.get(key)==current.get(key),'compiler environment mismatch: '+key)
 module='github.com/lazysnail1024/zed-argo-workflows/internal/buildinfo'
 flags=f"-X {module}.ServerVersion={version} -X {module}.SourceCommit={expected['testedSHA']} -X {module}.Dirty=false -X {module}.BuildMode=release"
 require(row.get('linkerFlags')==flags,'ordinary linker flags mismatch')
 require(row.get('runnerImage')=={'os':os.environ.get('ImageOS','local'),'version':os.environ.get('ImageVersion','local')},'runner ABI/image mismatch')
 require(row.get('buildMode')=='release' and row.get('productVersion')==version,'mode/version mismatch')
 require(row.get('binarySha256')==sha(binary),'binary digest mismatch')
 # Artifact extraction may omit executable mode; this does not alter its bytes.
 if os.name!='nt':binary.chmod(binary.stat().st_mode|0o100)
 actual=json.loads(subprocess.check_output([str(binary.resolve()),'version','--json'],timeout=20))
 require(actual==row['identity'],'executed binary identity differs from producer')
 require(actual.get('sourceCommit')==expected['testedSHA'] and actual.get('dirty') is False and actual.get('buildMode')=='release' and actual.get('serverVersion')==version,'native handshake source/mode/version mismatch')
 return {'schemaVersion':1,'consumer':identity(),'producer':row['producer'],'buildManifestSha256':expected_digest,'binarySha256':sha(binary),'pass':True}
