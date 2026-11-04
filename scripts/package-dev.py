#!/usr/bin/env python3
"""Package a verified, locally runnable prebuilt server as a Zed dev extension."""
import argparse
import hashlib
import json
import pathlib
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
def digest(data):
    return hashlib.sha256(data).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('binary', 'sha256', 'platform', 'source-archive', 'source-sha256'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--output', default=str(ROOT/'dist'))
    args = parser.parse_args()
    for value in (args.sha256, args.source_sha256):
        if not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('SHA-256 must be 64 lowercase hexadecimal characters')
    native = pathlib.Path(args.binary).resolve()
    payload = native.read_bytes()
    source = pathlib.Path(args.source_archive).read_bytes()
    if digest(payload) != args.sha256 or digest(source) != args.source_sha256:
        raise ValueError('native or source archive SHA-256 mismatch')
    host = {'Linux':'linux', 'Darwin':'darwin', 'Windows':'windows'}[platform.system()] + '/' + {'x86_64':'amd64','AMD64':'amd64','aarch64':'arm64','arm64':'arm64'}[platform.machine()]
    if args.platform != host or host not in ('linux/amd64','linux/arm64','darwin/amd64','darwin/arm64','windows/amd64'):
        raise ValueError('package on the target native host; requested platform does not match')
    identity_process = subprocess.run([str(native),'version','--json'],capture_output=True,check=True)
    identity = json.loads(identity_process.stdout)
    if identity.get('product') != 'argo-workflows' or identity.get('adapterContractVersion') != 1 or identity.get('analysisReady') is not True or identity.get('acceptedTargetVersions') != ['3.6.0'] or identity.get('defaultTargetVersion') != '3.6.0':
        raise ValueError('native product, readiness, contract, or accepted target mismatch')
    build = subprocess.check_output(['go','version','-m',str(native)],text=True)
    go_version = build.splitlines()[0].rsplit(': ',1)[1]
    if subprocess.check_output(['go','env','GOVERSION'],text=True).strip() != go_version:
        raise ValueError('active Go toolchain must match native build for license provenance')
    os_name, arch = args.platform.split('/')
    if '\tbuild\tGOOS='+os_name not in build or '\tbuild\tGOARCH='+arch not in build:
        raise ValueError('native build platform mismatch')
    files = {}
    for name in ('Cargo.toml','Cargo.lock','extension.toml','rust-toolchain.toml','LICENSE','README.md','DEVELOPMENT.md'):
        files[name] = (ROOT/name).read_bytes()
    for directory in ('src','snippets'):
        for path in sorted((ROOT/directory).rglob('*')):
            if path.is_file(): files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    cargo = files['Cargo.toml'].decode()
    if 'default = []' not in cargo: raise ValueError('source Cargo features must default to empty')
    files['Cargo.toml'] = cargo.replace('default = []','default = ["bundled-server"]').encode()
    # Read licenses from the exact native source snapshot, never from sibling repositories.
    with tarfile.open(args.source_archive,'r:*') as archive:
        for member in archive.getmembers():
            path=pathlib.PurePosixPath(member.name)
            if member.isfile() and '..' not in path.parts and not path.is_absolute() and (path.parts[0] in ('vendor','schemas')) and any(token in path.name.upper() for token in ('LICENSE','NOTICE','COPYING','COPYRIGHT','PATENTS')):
                files['licenses/'+str(path)] = archive.extractfile(member).read()
        for name in ('go.mod','go.sum','contracts/toolkit.lock.json','contracts/expr-patch.json','compat/catalog.lock.json'):
            destination = name if name == 'contracts/expr-patch.json' else 'provenance/'+name
            files[destination] = archive.extractfile(name).read()
    if 'licenses/schemas/LICENSE' not in files or not any(p.startswith('licenses/vendor/') for p in files):
        raise ValueError('native source archive lacks required notices')
    go_root = pathlib.Path(subprocess.check_output(['go','env','GOROOT'],text=True).strip())
    for name in ('LICENSE', 'PATENTS'):
        files['licenses/go/'+name] = (go_root/name).read_bytes()
    files['provenance/native-build.txt'] = ('native: '+ '\n'.join(build.splitlines()[0].split(': ')[1:])+'\n'+'\n'.join(build.splitlines()[1:])+'\n').encode()
    files['provenance/native-version.stdout'] = identity_process.stdout
    files['provenance/native-version.stderr'] = identity_process.stderr
    files['native/server.bin'] = payload
    manifest = {'schemaVersion':1,'product':'argo-workflows','platform':args.platform,'binarySha256':args.sha256,'binarySize':len(payload),'identity':identity,'sourceArchiveSha256':args.source_sha256,'toolchain':{'goVersion':go_version,'rustToolchain':files['rust-toolchain.toml'].decode()},'filesSha256':{name:digest(data) for name,data in sorted(files.items())}}
    files['native/manifest.json'] = (json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode()
    output=pathlib.Path(args.output).resolve()
    name='zed-argo-workflows-dev-'+args.platform.replace('/','-')
    folder=output/name; zip_path=output/(name+'.zip')
    if folder.exists() or zip_path.exists(): raise ValueError('output already exists; choose a fresh output directory')
    # Validate every input before producing any success output.
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dev-bundle-',dir=output) as temp:
        stage=pathlib.Path(temp)/name; stage.mkdir()
        for relative,data in sorted(files.items()):
            path=stage/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
            path.chmod(0o755 if relative=='native/server.bin' else 0o644)
        staged_zip=pathlib.Path(temp)/(name+'.zip')
        with zipfile.ZipFile(staged_zip,'w',compression=zipfile.ZIP_STORED) as archive:
            for relative,data in sorted(files.items()):
                info=zipfile.ZipInfo(name+'/'+relative,(1980,1,1,0,0,0)); info.create_system=3
                info.external_attr=(0o100755 if relative=='native/server.bin' else 0o100644)<<16
                archive.writestr(info,data)
        shutil.move(stage,folder); shutil.move(staged_zip,zip_path)
    print(json.dumps({'folder':str(folder),'zip':str(zip_path),'zipSha256':digest(zip_path.read_bytes()),'binarySha256':args.sha256},sort_keys=True))

if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as error:
        raise SystemExit('Packaging failed: '+str(error))
