#!/usr/bin/env python3
"""Package a verified server beside an independent, binary-free Zed dev extension."""
import argparse
import hashlib
import io
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN_PACKAGE = 'github.com/lazysnail1024/zed-argo-workflows/cmd/argo-workflows-ls'
ADAPTER_FILES = ('Cargo.toml', 'Cargo.lock', 'extension.toml', 'rust-toolchain.toml',
                 'LICENSE', 'README.md', 'DEVELOPMENT.md')
PROVENANCE_FILES = ('go.mod', 'go.sum', 'contracts/toolkit.lock.json',
                    'contracts/expr-patch.json', 'compat/catalog.lock.json',
                    'scripts/windows-runtime.json')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def windows_runtime(spec, env=None):
    """Verify the pinned compiler inputs and return its original runtime notices.

    The native builder calls this before and after linking. The packager calls
    the same helper against the pin from the exact supplied source archive.
    """
    if not isinstance(spec, dict):
        raise ValueError('Windows runtime pin must be an object')
    environment = dict(os.environ if env is None else env)
    compiler = environment.get('CC') or subprocess.check_output(
        ['go', 'env', 'CC'], env=environment, text=True, timeout=30).strip()
    resolved = shutil.which(compiler, path=environment.get('PATH'))
    if resolved is None:
        raise ValueError('pinned Windows compiler is unavailable: ' + compiler)
    compiler_path = pathlib.Path(resolved).resolve()
    if compiler_path.name.lower() != 'gcc.exe':
        raise ValueError('pinned Windows runtime requires gcc.exe')
    tool_root = compiler_path.parent.parent
    notices = {}
    for category, directory in (('runtimeFiles', tool_root), ('legalFiles', tool_root / 'licenses')):
        inventory = spec.get(category)
        if not isinstance(inventory, dict) or not inventory:
            raise ValueError('Windows runtime pin has no ' + category)
        for name, expected in inventory.items():
            relative = pathlib.PurePosixPath(name)
            if (not relative.parts or relative.is_absolute() or '..' in relative.parts
                    or '\\' in name or ':' in name
                    or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)):
                raise ValueError('Windows runtime pin has an invalid file or checksum')
            path = directory.joinpath(*relative.parts)
            if not path.is_file() or path.is_symlink():
                raise ValueError('Windows runtime input differs from the pinned distribution: ' + name)
            value = path.read_bytes()
            if digest(value) != expected:
                raise ValueError('Windows runtime input differs from the pinned distribution: ' + name)
            if category == 'legalFiles':
                notices['licenses/windows-runtime/' + name] = value
    if 'bin/gcc.exe' not in spec['runtimeFiles']:
        raise ValueError('Windows runtime pin does not identify gcc.exe')
    return notices


def verify_windows_notices(spec, files):
    """Check original notices against the committed runtime distribution pin."""
    if not isinstance(spec, dict):
        raise ValueError('Windows runtime pin must be an object')
    inventory = spec.get('legalFiles')
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError('Windows runtime pin has no legalFiles')
    expected = {'licenses/windows-runtime/' + name: value for name, value in inventory.items()}
    actual = {name: digest(value) for name, value in files.items()
              if name.startswith('licenses/windows-runtime/')}
    if expected != actual:
        raise ValueError('Windows runtime notices differ from the pinned distribution')


def source_files(payload):
    """Read every packaged source file from one archive, without extracting it."""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:*') as archive:
        members = {}
        for member in archive.getmembers():
            path = pathlib.PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name or not path.parts:
                raise ValueError('unsafe source archive member: ' + member.name)
            if member.name in members or (not member.isdir() and not member.isfile()):
                raise ValueError('duplicate or non-regular source archive member: ' + member.name)
            members[member.name] = member
        for name in ADAPTER_FILES + PROVENANCE_FILES:
            if name not in members or not members[name].isfile():
                raise ValueError('source archive is missing required file: ' + name)
        for name, member in members.items():
            if not member.isfile():
                continue
            path = pathlib.PurePosixPath(name)
            destination = None
            if name in ADAPTER_FILES or path.parts[0] in ('src', 'snippets', 'examples', 'docs'):
                destination = 'extension/' + name
            elif name in PROVENANCE_FILES:
                destination = 'provenance/' + name
            elif path.parts[:2] == ('licenses', 'windows-runtime'):
                destination = name
            elif path.parts[0] in ('vendor', 'schemas') and any(
                    token in path.name.upper() for token in ('LICENSE', 'NOTICE', 'COPYING', 'COPYRIGHT', 'PATENTS')):
                destination = 'licenses/' + name
            if destination is not None:
                files[destination] = archive.extractfile(member).read()
        commit = archive.pax_headers.get('comment')
    if commit is not None and not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', commit):
        raise ValueError('source archive has an invalid Git commit comment')
    for name in ('extension/src/lib.rs', 'extension/snippets/yaml.json', 'extension/examples/zed-tasks.json', 'licenses/schemas/LICENSE'):
        if name not in files:
            raise ValueError('source archive is missing required file: ' + name)
    if not any(name.startswith('licenses/vendor/') for name in files):
        raise ValueError('native source archive lacks required notices')
    verify_windows_notices(json.loads(files['provenance/scripts/windows-runtime.json']), files)
    return files, commit


def build_metadata(native):
    build = subprocess.check_output(['go', 'version', '-m', str(native)], text=True, timeout=30)
    lines = build.splitlines()
    if not lines or ': ' not in lines[0]:
        raise ValueError('native has no Go build metadata')
    go_version = lines[0].rsplit(': ', 1)[1]
    settings = {}
    main_package = None
    for line in lines[1:]:
        fields = line.split('\t')
        if len(fields) >= 3 and fields[1] == 'path':
            main_package = fields[2]
        if len(fields) >= 3 and fields[1] == 'build' and '=' in fields[2]:
            key, value = fields[2].split('=', 1)
            settings[key] = value
    if main_package != MAIN_PACKAGE:
        raise ValueError('native Go main package mismatch')
    if subprocess.check_output(['go', 'env', 'GOVERSION'], text=True, timeout=30).strip() != go_version:
        raise ValueError('active Go toolchain must match native build for license provenance')
    return build, go_version, settings


def source_identity(settings, archive_commit):
    revision = settings.get('vcs.revision')
    modified = settings.get('vcs.modified')
    if revision:
        if settings.get('vcs') != 'git' or modified != 'false':
            raise ValueError('native VCS metadata must describe a clean Git source snapshot')
        if revision != archive_commit:
            raise ValueError('native VCS revision does not match the source archive commit')
    # An unstamped local build may still be packaged, but must not claim that its
    # source provenance was verified. CI requires verified-vcs for its artifacts.
    return {'status': 'verified-vcs' if revision else 'unavailable',
            'archiveCommit': archive_commit, 'nativeCommit': revision,
            'nativeDirty': modified == 'true' if modified is not None else None}


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
    identity_process = subprocess.run([str(native),'version','--json'],capture_output=True,check=True,timeout=30)
    identity = json.loads(identity_process.stdout)
    if not isinstance(identity, dict) or identity.get('product') != 'argo-workflows' or identity.get('adapterContractVersion') != 1 or identity.get('analysisReady') is not True or identity.get('acceptedTargetVersions') != ['3.6.0'] or identity.get('defaultTargetVersion') != '3.6.0':
        raise ValueError('native product, readiness, contract, or accepted target mismatch')
    build, go_version, settings = build_metadata(native)
    os_name, arch = args.platform.split('/')
    if settings.get('GOOS') != os_name or settings.get('GOARCH') != arch:
        raise ValueError('native build platform mismatch')
    files, archive_commit = source_files(source)
    provenance = source_identity(settings, archive_commit)
    windows = None
    if os_name == 'windows':
        windows = json.loads(files['provenance/scripts/windows-runtime.json'])
        actual_notices = windows_runtime(windows)
        if any(files.get(name) != value for name, value in actual_notices.items()):
            raise ValueError('Windows compiler notices do not match the source archive')
    if identity.get('catalogHash') != digest(files['provenance/compat/catalog.lock.json']):
        raise ValueError('native catalog hash does not match the source archive')
    go_root = pathlib.Path(subprocess.check_output(['go','env','GOROOT'],text=True,timeout=30).strip())
    for name in ('LICENSE', 'PATENTS'):
        files['licenses/go/'+name] = (go_root/name).read_bytes()
    files['provenance/native-build.txt'] = ('native: '+ '\n'.join(build.splitlines()[0].split(': ')[1:])+'\n'+'\n'.join(build.splitlines()[1:])+'\n').encode()
    files['provenance/native-version.stdout'] = identity_process.stdout
    files['provenance/native-version.stderr'] = identity_process.stderr
    native_name = 'server/argo-workflows-ls' + ('.exe' if os_name == 'windows' else '')
    files[native_name] = payload
    sample_path = ('C:/absolute/path/to/package/' if os_name == 'windows' else '/absolute/path/to/package/') + native_name
    files['settings.example.json'] = (json.dumps({'lsp': {'argo-workflows-ls': {'settings': {'serverPath': sample_path}}}}, indent=2) + '\n').encode()
    files['INSTALL.md'] = ("# Developer installation\n\nSelect the `extension/` folder in Zed's Install Dev Extension action. "
        "The native server is separate in `" + native_name + "`. Set the absolute path in `settings.example.json` in Zed settings. "
        "On POSIX hosts, restore executable permissions with `chmod +x " + native_name + "` if needed. "
        "The extension contains no embedded server. Removing serverPath enables PATH discovery or a verified release download. "
        "The first managed installation needs a matching published release and network access.\n").encode()
    manifest = {'schemaVersion':1,'product':'argo-workflows','platform':args.platform,'binarySha256':args.sha256,'binarySize':len(payload),'identity':identity,'sourceArchiveSha256':args.source_sha256,'sourceIdentity':provenance,'toolchain':{'goVersion':go_version,'rustToolchain':files['extension/rust-toolchain.toml'].decode()},'filesSha256':{name:digest(data) for name,data in sorted(files.items())}}
    if windows is not None:
        manifest['windowsRuntime'] = windows
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
            path.chmod(0o755 if relative==native_name else 0o644)
        staged_zip=pathlib.Path(temp)/(name+'.zip')
        with zipfile.ZipFile(staged_zip,'w',compression=zipfile.ZIP_STORED) as archive:
            for relative,data in sorted(files.items()):
                info=zipfile.ZipInfo(name+'/'+relative,(1980,1,1,0,0,0)); info.create_system=3
                info.external_attr=(0o100755 if relative==native_name else 0o100644)<<16
                archive.writestr(info,data)
        shutil.move(stage,folder); shutil.move(staged_zip,zip_path)
    print(json.dumps({'folder':str(folder),'extension':str(folder/'extension'),'binary':str(folder/native_name),'zip':str(zip_path),'zipSha256':digest(zip_path.read_bytes()),'binarySha256':args.sha256},sort_keys=True))

if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as error:
        raise SystemExit('Packaging failed: '+str(error))
