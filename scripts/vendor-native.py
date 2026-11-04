#!/usr/bin/env python3
"""Complete go mod vendor with the pinned dependencies' required C source trees."""
import subprocess,pathlib,shutil,json,hashlib,os
root=pathlib.Path(__file__).resolve().parents[1]
env=os.environ|{'GOWORK':'off'}
go=str(pathlib.Path(env['GOROOT'])/'bin/go') if env.get('GOROOT') else shutil.which('go')
if not go: raise SystemExit('Go is required on PATH or via GOROOT')
for directory in (root/'vendor').rglob('*'):
 if directory.is_dir(): directory.chmod(directory.stat().st_mode | 0o700)
subprocess.run([go,'mod','vendor'],cwd=root,env=env,check=True)
records={}
for module,dirs in {'github.com/tree-sitter/go-tree-sitter':['include','src'],'github.com/lazysnail1024/k8s-crd-toolkit':['yamlview/internal/parser/grammar/tree_sitter']}.items():
 result=subprocess.check_output([go,'list','-mod=mod','-m','-json',module],cwd=root,env=env,text=True);meta=json.loads(result)
 for name in dirs:
  source=pathlib.Path(meta['Dir'])/name;target=root/'vendor'/module/name;shutil.copytree(source,target,dirs_exist_ok=True)
  for path in sorted(target.rglob('*')):
   if path.is_dir(): path.chmod(path.stat().st_mode | 0o700)
   if path.is_file():records[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
# Preserve upstream character values while keeping explanatory text in English.
number=root/'vendor/golang.org/x/text/internal/number'
p=number/'number.go';p.write_text(p.read_text().replace("could return '\u4e09'", 'could return U+4E09 (CJK digit three)'))
p=number/'tables.go';p.write_text(p.read_text().replace('\u975e\u6578\u503c\u975e\u6570\u503c', r'\u975e\u6578\u503c\u975e\u6570\u503c'))
# Apply the single recorded local Expr formatter patch after a fresh vendor.
expr_patch=json.loads((root/'contracts/expr-patch.json').read_text())
p=root/expr_patch['file'];original=p.read_bytes()
if hashlib.sha256(original).hexdigest()!=expr_patch['upstreamSha256']:raise SystemExit('Expr source does not match the pinned patch input')
patched=original.decode().replace('var indLine = "\\n | "','var indLine strings.Builder\n\t\tindLine.WriteString("\\n | ")').replace('indLine += "."', "indLine.WriteByte('.')").replace('indLine += "^"', "indLine.WriteByte('^')").replace('srcLine += indLine','srcLine += indLine.String()').encode()
if hashlib.sha256(patched).hexdigest()!=expr_patch['patchedSha256']:raise SystemExit('Expr patch output hash differs')
p.write_bytes(patched)
(root/'artifacts').mkdir(exist_ok=True)
(root/'artifacts/vendor-native.json').write_text(json.dumps({'command':'python3 scripts/vendor-native.py','reason':'go mod vendor omits C include/source subdirectories required by cgo','files':records},indent=2)+'\n')
