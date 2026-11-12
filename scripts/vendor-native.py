#!/usr/bin/env python3
"""Prepare ignored, verified build inputs from the pinned Go modules.

The generated vendor tree is disposable; go.mod/go.sum and the two explicit
patch contracts are the source of truth. Never patch the shared module cache.
"""
import subprocess,pathlib,shutil,json,hashlib,os,re
root=pathlib.Path(__file__).resolve().parents[1]
env=os.environ|{'GOENV':'off','GOWORK':'off','GOTOOLCHAIN':'local','GOFLAGS':''}
go=str(pathlib.Path(env['GOROOT'])/'bin/go') if env.get('GOROOT') else shutil.which('go')
if not go: raise SystemExit('Go is required on PATH or via GOROOT')
locks={name:(root/name).read_bytes() for name in ('go.mod','go.sum')}
if json.loads(subprocess.check_output([go,'mod','edit','-json'],cwd=root,env=env)).get('Replace'):
 raise SystemExit('local or remote module replacements are not accepted; pin the source module explicitly')
subprocess.run([go,'mod','download'],cwd=root,env=env,check=True)
subprocess.run([go,'mod','verify'],cwd=root,env=env,check=True)
if any((root/name).read_bytes()!=data for name,data in locks.items()):
 raise SystemExit('dependency preparation changed go.mod/go.sum; update the locks explicitly')
toolkit=json.loads((root/'contracts/toolkit.lock.json').read_text())
module=json.loads(subprocess.check_output([go,'list','-mod=readonly','-m','-json',toolkit['module']],cwd=root,env=env,text=True))
if 'Replace' in module or module['Version']!=toolkit['implementationVersion']:
 raise SystemExit('Toolkit module differs from the pinned source version')
commit=toolkit['implementationCommit']
download=json.loads(subprocess.check_output([go,'mod','download','-json',toolkit['module']],cwd=root,env=env,text=True))
origin=download.get('Origin',{}).get('Hash')
if not re.fullmatch('[0-9a-f]{40}',commit) or (origin and origin!=commit) or not (origin==commit or module['Version'].endswith('-'+commit[:12])):
 raise SystemExit('Toolkit source commit differs from the pinned module')
for path in (pathlib.Path(module['Dir'])/'CONTRACT.md',root/'contracts'/toolkit['contractFile']):
 if hashlib.sha256(path.read_bytes()).hexdigest()!=toolkit['contractSha256']:
  raise SystemExit('Toolkit contract differs from the pinned source')
is_link=lambda p:p.is_symlink() or getattr(p,'is_junction',lambda:False)()
vendor=root/'vendor'
if is_link(vendor):raise SystemExit('symlinks and junctions are not accepted in generated vendor')
previous=list(vendor.rglob('*'))
if any(is_link(path) for path in previous):raise SystemExit('symlinks and junctions are not accepted in generated vendor')
for directory in previous:
 if directory.is_dir(): directory.chmod(directory.stat().st_mode | 0o700)
subprocess.run([go,'mod','vendor'],cwd=root,env=env,check=True)
records={}
for module,dirs in {'github.com/tree-sitter/go-tree-sitter':['include','src'],'github.com/lazysnail1024/k8s-crd-toolkit':['yamlview/internal/parser/grammar/tree_sitter']}.items():
 result=subprocess.check_output([go,'list','-mod=readonly','-m','-json',module],cwd=root,env=env,text=True);meta=json.loads(result)
 for name in dirs:
  source=pathlib.Path(meta['Dir'])/name;target=root/'vendor'/module/name;shutil.copytree(source,target,dirs_exist_ok=True)
  target.chmod(target.stat().st_mode | 0o700)
  for path in sorted(target.rglob('*')):
   if path.is_dir(): path.chmod(path.stat().st_mode | 0o700)
   if path.is_file():
    path.chmod(path.stat().st_mode | 0o600)
    records[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
# Apply only the declared patch files, never a second implementation of them.
for name in ('expr-patch.json','jsonschema-patch.json'):
 spec=json.loads((root/'contracts'/name).read_text())
 info=json.loads(subprocess.check_output([go,'list','-mod=readonly','-m','-json',spec['module']],cwd=root,env=env,text=True))
 if info['Version']!=spec['version']:raise SystemExit('patch module version differs: '+spec['module'])
 target=root/spec['file'];patch=root/'contracts'/spec['patch']
 if hashlib.sha256(target.read_bytes()).hexdigest()!=spec['upstreamSha256']:raise SystemExit('patch input hash differs: '+spec['file'])
 affected=subprocess.check_output(['git','apply','--numstat',str(patch)],cwd=root,env=env,text=True).strip().splitlines()
 if len(affected)!=1 or affected[0].split('\t')[-1]!=spec['file']:raise SystemExit('patch modifies an unexpected source file')
 subprocess.run(['git','apply','--check',str(patch)],cwd=root,env=env,check=True)
 subprocess.run(['git','apply',str(patch)],cwd=root,env=env,check=True)
 if hashlib.sha256(target.read_bytes()).hexdigest()!=spec['patchedSha256']:raise SystemExit('patch output hash differs: '+spec['file'])
(root/'artifacts').mkdir(exist_ok=True)
# Keep only legal notices in Git so offline release verification can compare
# their exact bytes with the source archive. Detect missing or altered notices.
legal=lambda p:any(word in p.name.upper() for word in ('LICENSE','NOTICE','COPYING','COPYRIGHT','PATENTS'))
expected={str(p.relative_to(root/'vendor')).replace(os.sep,'/'):p.read_bytes() for p in (root/'vendor').rglob('*') if p.is_file() and legal(p)}
actual={str(p.relative_to(root/'licenses/modules')).replace(os.sep,'/'):p.read_bytes() for p in (root/'licenses/modules').rglob('*') if p.is_file()}
if expected!=actual:
 missing=sorted(set(expected)^set(actual));changed=sorted(name for name in expected.keys()&actual.keys() if expected[name]!=actual[name])
 raise SystemExit('dependency legal notices differ from the pinned modules: '+str(missing+changed))
if any((root/name).read_bytes()!=data for name,data in locks.items()):
 raise SystemExit('vendoring changed go.mod/go.sum; update the locks explicitly')
(root/'artifacts/vendor-native.json').write_text(json.dumps({'command':'python3 scripts/vendor-native.py','reason':'generated build-only source; exact module locks and explicit patches','files':records,'notices':{name:hashlib.sha256(data).hexdigest() for name,data in sorted(expected.items())}},indent=2)+'\n')
