#!/usr/bin/env python3
"""Process acceptance: deterministic installable bundle, identity and fail-closed inputs."""
import hashlib, json, pathlib, subprocess, sys, zipfile, platform, tarfile
root = pathlib.Path(__file__).resolve().parents[2]
binary, source, out = map(lambda x: pathlib.Path(x).resolve(), sys.argv[1:4])
out.mkdir(parents=True, exist_ok=False)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
platform_id = {'Linux':'linux', 'Darwin':'darwin', 'Windows':'windows'}[platform.system()] + '/' + {'x86_64':'amd64','AMD64':'amd64','aarch64':'arm64','arm64':'arm64'}[platform.machine()]
base = [sys.executable, str(root/'scripts/package-dev.py'), '--binary', str(binary), '--sha256', sha(binary), '--platform', platform_id, '--source-archive', str(source), '--source-sha256', sha(source)]
results=[]
def run(name, args, valid):
    target=out/name
    command=args+['--output',str(target)]
    p=subprocess.run(command,capture_output=True)
    (out/(name+'.stdout')).write_bytes(p.stdout); (out/(name+'.stderr')).write_bytes(p.stderr)
    ok=p.returncode==0 if valid else p.returncode!=0 and not p.stdout and not target.exists()
    results.append({'case':name,'command':command,'exitCode':p.returncode,'passed':ok})
    if not ok: raise AssertionError(name)
    return target
try:
    a=run('first',base,True); b=run('second',base,True)
    za=next(a.glob('*.zip')); zb=next(b.glob('*.zip'))
    assert sha(za)==sha(zb)
    folder=next(p for p in a.iterdir() if p.is_dir())
    required=['extension.toml','Cargo.toml','Cargo.lock','rust-toolchain.toml','src/lib.rs','snippets/yaml.json','LICENSE','native/server.bin','native/manifest.json','licenses/go/LICENSE','licenses/go/PATENTS','licenses/schemas/LICENSE','licenses/vendor/github.com/tree-sitter/go-tree-sitter/src/unicode/LICENSE','licenses/vendor/go.yaml.in/yaml/v2/NOTICE']
    assert all((folder/p).is_file() for p in required)
    # The native source snapshot, not the packager inventory, is the legal-text oracle.
    with tarfile.open(source, 'r:*') as archive:
        patents=archive.extractfile('vendor/golang.org/x/text/PATENTS').read()
    assert (folder/'licenses/vendor/golang.org/x/text/PATENTS').read_bytes()==patents
    assert 'default = ["bundled-server"]' in (folder/'Cargo.toml').read_text()
    assert sha(folder/'native/server.bin')==sha(binary)
    with zipfile.ZipFile(za) as z:
        assert all(n.startswith(folder.name+'/') and '..' not in n.split('/') for n in z.namelist())
        assert not any('/AGENTS.md' in n or '/DESIGN.md' in n or '/artifacts/' in n for n in z.namelist())
    p=subprocess.run([str(folder/'native/server.bin'),'version','--json'],capture_output=True)
    (out/'native.stdout').write_bytes(p.stdout); (out/'native.stderr').write_bytes(p.stderr)
    v=json.loads(p.stdout); assert p.returncode==0 and v['product']=='argo-workflows' and v['analysisReady'] is True and v['acceptedTargetVersions']==['3.6.0']
    results.append({'case':'installable-assets-licenses-identity-determinism','passed':True,'zipSha256':sha(za),'nativeExitCode':p.returncode})
    wrong_product=out/'wrong-product-server'
    wrong_identity=dict(v,product='argocd')
    wrong_product.write_text('#!'+sys.executable+'\nprint('+repr(json.dumps(wrong_identity))+')\n')
    wrong_product.chmod(0o755)
    for name, flag, value in [('bad-sha','--sha256','0'*64),('bad-platform','--platform','windows/amd64' if platform_id!='windows/amd64' else 'linux/amd64'),('missing-native','--binary',str(out/'absent')),('wrong-product','--binary',str(wrong_product))]:
        args=base.copy(); args[args.index(flag)+1]=value
        if name=='wrong-product': args[args.index('--sha256')+1]=sha(pathlib.Path(value))
        run(name,args,False)
    args=base.copy(); i=args.index('--sha256'); del args[i:i+2]; run('missing-sha',args,False)
finally:
    manifest={'command':sys.argv,'python':sys.version,'platform':platform.platform(),'nativeSha256':sha(binary),'sourceSha256':sha(source),'testSha256':sha(pathlib.Path(__file__)),'packagerSha256':sha(root/'scripts/package-dev.py') if (root/'scripts/package-dev.py').exists() else None,'expected':['installable root and required notices','real ready Workflows 3.6.0 identity','byte-identical archives for identical inputs','invalid inputs fail without outputs']}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n'); (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
