#!/usr/bin/env python3
"""Compile an isolated, explicitly synthetic schema-contract consumer."""
import pathlib,sys,shutil,json,subprocess,os,hashlib
root=pathlib.Path(__file__).resolve().parents[2];out=pathlib.Path(sys.argv[1]).resolve();source=out/'source';source.mkdir(parents=True,exist_ok=False)
for name in ['cmd','internal','compat','schemas','vendor']:shutil.copytree(root/name,source/name,dirs_exist_ok=True)
for name in ['go.mod','go.sum']:shutil.copy2(root/name,source/name)
asset='internal/assets/schemas/96631f9b6d683112ccd0dd0dd7c28b3a59310400e3b2550e8cadb0a370410ac7.json';fixture=root/'tests/fixtures/schema-query/contract.json';original=(source/asset).read_bytes();(source/asset).write_bytes(fixture.read_bytes());env=os.environ|{'GOWORK':'off','GOTOOLCHAIN':'local','CGO_ENABLED':'1'};go=pathlib.Path(env['GOROOT'])/'bin/go';cmd=[str(go),'build','-mod=vendor','-trimpath','-buildvcs=false','-o',str(out/'server'),'./cmd/argo-workflows-ls']
with (out/'build.stdout').open('wb') as so,(out/'build.stderr').open('wb') as se:r=subprocess.run(cmd,cwd=source,env=env,stdout=so,stderr=se)
sha=lambda b:hashlib.sha256(b).hexdigest();record={'command':cmd,'exitCode':r.returncode,'classification':'Synthetic schema consumer only; not upstream CRD or version acceptance.','replacedAsset':asset,'originalAssetSha256':sha(original),'syntheticAssetSha256':sha(fixture.read_bytes()),'GOROOT':env['GOROOT'],'GOCACHE':env.get('GOCACHE'),'testSha256':sha(pathlib.Path(__file__).read_bytes())}
if r.returncode==0:record['binarySha256']=sha((out/'server').read_bytes())
(out/'build.json').write_text(json.dumps(record,indent=2)+'\n');sys.exit(r.returncode)
