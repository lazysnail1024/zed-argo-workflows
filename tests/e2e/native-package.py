#!/usr/bin/env python3
"""Process acceptance of bundle bytes/provenance; this does not launch Zed."""
import hashlib
import io
import json
import os
import pathlib
import platform
import runpy
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
import zipfile

root = pathlib.Path(__file__).resolve().parents[2]
if len(sys.argv) != 4:
    raise SystemExit('usage: native-package.py NATIVE_BINARY SOURCE_ARCHIVE OUTPUT_DIRECTORY')
binary, source, out = (pathlib.Path(value).resolve() for value in sys.argv[1:])
out.mkdir(parents=True, exist_ok=False)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
platform_id = {'Linux': 'linux', 'Darwin': 'darwin', 'Windows': 'windows'}[platform.system()] + '/' + {
    'x86_64': 'amd64', 'AMD64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}[platform.machine()]
base = [sys.executable, str(root / 'scripts/package-native.py'), '--binary', str(binary),
        '--sha256', sha(binary), '--platform', platform_id,
        '--source-archive', str(source), '--source-sha256', sha(source)]
results = []


def save(name, value):
    (out / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def require(name, checks, **evidence):
    passed = bool(checks) and all(value is True for value in checks.values())
    results.append({'case': name, 'checks': checks, 'passed': passed, **evidence})
    save('results.json', results)
    if not passed:
        raise AssertionError(name + ': ' + ', '.join(key for key, value in checks.items() if value is not True))


def command(name, argv, timeout=120):
    save(name + '.command.json', {'command': argv, 'timeoutSeconds': timeout})
    started = time.monotonic()
    try:
        process = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        (out / (name + '.stdout')).write_bytes(error.stdout or b'')
        (out / (name + '.stderr')).write_bytes(error.stderr or b'')
        require(name + '-process', {'completedWithinDeadline': False})
    (out / (name + '.stdout')).write_bytes(process.stdout)
    (out / (name + '.stderr')).write_bytes(process.stderr)
    save(name + '.exit.json', {'exitCode': process.returncode, 'elapsedSeconds': time.monotonic() - started})
    return process


def package(name, args, expected_error=None):
    target = out / name
    process = command(name, args + ['--output', str(target)])
    if expected_error is not None:
        require(name, {'nonzeroExit': process.returncode > 0,
                       'expectedReason': expected_error in process.stderr.decode('utf-8', errors='replace'),
                       'noSuccessOutput': process.stdout == b'', 'noPublishedFiles': not target.exists()},
                exitCode=process.returncode, expectedError=expected_error)
        return None
    require(name + '-process', {'exit': process.returncode == 0, 'noStderr': process.stderr == b''},
            exitCode=process.returncode)
    metadata = json.loads(process.stdout)
    version = json.loads(subprocess.check_output([str(binary), 'version', '--json']))['serverVersion']
    folder = target / ('argo-workflows-ls-' + version + '-' + platform_id.replace('/', '-'))
    archive = pathlib.Path(str(folder) + '.zip')
    require(name + '-output', {'folder': metadata.get('folder') == str(folder) and folder.is_dir(),
                              'binary': metadata.get('binary') == str(folder / ('argo-workflows-ls.exe' if platform.system() == 'Windows' else 'argo-workflows-ls')),
                              'zip': metadata.get('zip') == str(archive) and archive.is_file(),
                              'zipSha256': metadata.get('zipSha256') == sha(archive),
                              'binarySha256': metadata.get('binarySha256') == sha(binary)})
    return folder, archive


def changed(flag, value, args=None):
    argv = (base if args is None else args).copy()
    argv[argv.index(flag) + 1] = str(value)
    return argv


def write_archive(name, files, commit):
    path = out / (name + '.tar')
    with tarfile.open(path, 'w', format=tarfile.PAX_FORMAT, pax_headers={'comment': commit} if commit else {}) as archive:
        for member_name, data in sorted(files.items()):
            info = tarfile.TarInfo(member_name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return path


def archive_args(path):
    return changed('--source-sha256', sha(path), changed('--source-archive', path))


def fake_identities(identity):
    """Use a real native program on Windows too; exec-format failure is not a kill."""
    helper = out / ('identity-fixture.exe' if platform.system() == 'Windows' else 'identity-fixture')
    code = '''package main
import ("encoding/json"; "fmt"; "os"; "path/filepath"; "strings")
func main() {
    var value map[string]any
    if err := json.Unmarshal([]byte(IDENTITY_JSON), &value); err != nil { panic(err) }
    name := filepath.Base(os.Args[0])
    switch {
    case strings.HasPrefix(name, "wrong-product"): value["product"] = "argocd"
    case strings.HasPrefix(name, "unready"): value["analysisReady"] = false
    case strings.HasPrefix(name, "wrong-contract"): value["adapterContractVersion"] = 99
    case strings.HasPrefix(name, "wrong-target"): value["acceptedTargetVersions"] = []string{}
    }
    encoded, err := json.Marshal(value); if err != nil { panic(err) }; fmt.Println(string(encoded))
}
'''.replace('IDENTITY_JSON', json.dumps(json.dumps(identity)))
    path = out / 'identity-fixture.go'
    path.write_text(code, encoding='utf-8')
    built = command('identity-fixture-build', ['go', 'build', '-trimpath', '-buildvcs=false', '-o', str(helper), str(path)])
    require('identity-fixture-build', {'compiled': built.returncode == 0, 'nativeExists': helper.is_file()}, exitCode=built.returncode)
    modes = [('wrong-product', {'product': 'argocd'}), ('unready', {'analysisReady': False}),
             ('wrong-contract', {'adapterContractVersion': 99}), ('wrong-target', {'acceptedTargetVersions': []}),
             ('wrong-go-package', {})]
    for name, update in modes:
        fake = out / (name + '-server' + ('.exe' if platform.system() == 'Windows' else ''))
        shutil.copy2(helper, fake)
        probe = command(name + '-identity', [str(fake), 'version', '--json'])
        require(name + '-fixture', {'executes': probe.returncode == 0,
                                  'exactIdentity': json.loads(probe.stdout) == (identity | update)},
                nativeSha256=sha(fake))
        expected = 'native Go main package mismatch' if not update else 'native product, readiness, contract, or accepted target mismatch'
        package(name, changed('--sha256', sha(fake), changed('--binary', fake)), expected)


def runtime_pin_controls(spec, notice_bytes):
    """Synthetic input-integrity controls; no Windows compiler is executed."""
    helper = runpy.run_path(str(root / 'scripts/package-native.py'))['windows_runtime']
    fixture = out / 'synthetic-runtime-inputs'
    fixture_spec = json.loads(json.dumps(spec))
    for name in fixture_spec['runtimeFiles']:
        target = fixture / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'Synthetic input-integrity fixture: ' + name.encode())
        target.chmod(0o755)
        fixture_spec['runtimeFiles'][name] = sha(target)
    for name in fixture_spec['legalFiles']:
        target = fixture / 'licenses' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(notice_bytes['licenses/windows-runtime/' + name])
    environment = os.environ | {'CC': str(fixture / 'bin/gcc.exe')}
    require('synthetic-runtime-input-control', {
        'exactOriginalNotices': helper(fixture_spec, environment) == notice_bytes})
    for name, relative in (('static-runtime', 'x86_64-w64-mingw32/lib/libwinpthread.a'),
                           ('runtime-notice', 'licenses/winpthreads/COPYING')):
        path = fixture / relative
        original = path.read_bytes()
        path.write_bytes(original + b'changed')
        refused = False
        try:
            helper(fixture_spec, environment)
        except ValueError as error:
            refused = 'differs from the pinned distribution' in str(error)
        finally:
            path.write_bytes(original)
        require('changed-' + name + '-refused', {'specificIntegrityRefusal': refused})


try:
    first, first_zip = package('first', base)
    second, second_zip = package('second', base)
    require('deterministic-archive', {'byteIdentical': first_zip.read_bytes() == second_zip.read_bytes()}, zipSha256=sha(first_zip))
    manifest = json.loads((first / 'native/manifest.json').read_text(encoding='utf-8'))
    hashes = manifest.get('filesSha256', {})
    native_name = 'argo-workflows-ls' + ('.exe' if platform.system() == 'Windows' else '')
    required = ['LICENSE', native_name, 'native/manifest.json', 'README.txt',
                'licenses/go/LICENSE', 'licenses/go/PATENTS', 'licenses/schemas/LICENSE',
                'provenance/VERSION', 'provenance/scripts/windows-runtime.json',
                'licenses/vendor/github.com/tree-sitter/go-tree-sitter/src/unicode/LICENSE',
                'licenses/vendor/go.yaml.in/yaml/v2/NOTICE']
    folder_paths = {path.relative_to(first).as_posix() for path in first.rglob('*') if path.is_file()}
    require('complete-manifest', {'required': all((first / path).is_file() for path in required),
                                  'nonemptyHashes': isinstance(hashes, dict) and len(hashes) >= len(required) - 1,
                                  'exactInventory': folder_paths == set(hashes) | {'native/manifest.json'},
                                  'allFileHashes': all(sha(first / path) == expected for path, expected in hashes.items()),
                                  'product': manifest.get('schemaVersion') == 1 and manifest.get('product') == 'argo-workflows',
                                  'platform': manifest.get('platform') == platform_id,
                                  'native': manifest.get('binarySha256') == sha(binary) == sha(first / native_name),
                                  'nativeSize': manifest.get('binarySize') == binary.stat().st_size,
                                  'sourceHash': manifest.get('sourceArchiveSha256') == sha(source)})
    with zipfile.ZipFile(first_zip) as archive:
        names = archive.namelist()
        prefix = first.name + '/'
        wanted = {prefix + path for path in folder_paths}
        require('zip-integrity', {'noDuplicates': len(names) == len(set(names)), 'exactInventory': set(names) == wanted,
                                 'safePaths': all(name.startswith(prefix) and '..' not in name.split('/') and '\\' not in name for name in names),
                                 'noDevelopmentEvidence': not any('/AGENTS.md' in name or '/DESIGN.md' in name or '/artifacts/' in name for name in names),
                                 'allBytesMatchFolder': all(archive.read(prefix + path) == (first / path).read_bytes() for path in folder_paths),
                                 'allHashesMatch': all(hashlib.sha256(archive.read(prefix + path)).hexdigest() == expected for path, expected in hashes.items()),
                                 'nativeExecutableMode': (archive.getinfo(prefix + native_name).external_attr >> 16) & 0o777 == 0o755,
                                 'noCorruptMember': archive.testzip() is None})
        # Execute the separately supplied native server. Python's ZIP extraction
        # does not restore Unix execute bits, so apply the verified archive mode.
        extracted = out / ('extracted-server.exe' if platform.system() == 'Windows' else 'extracted-server')
        extracted.write_bytes(archive.read(prefix + native_name))
        extracted.chmod(0o755)
    executed = command('extracted-native', [str(extracted), 'version', '--json'])
    identity = json.loads(executed.stdout)
    require('extracted-native-identity', {'exit': executed.returncode == 0, 'noStderr': executed.stderr == b'',
                                        'binaryBytes': sha(extracted) == sha(binary),
                                        'manifestIdentity': identity == manifest.get('identity'),
                                        'readyWorkflows': identity.get('product') == 'argo-workflows' and identity.get('analysisReady') is True,
                                        'contract': identity.get('adapterContractVersion') == 1,
                                        'targets': bool(identity.get('acceptedTargetVersions')) and identity.get('defaultTargetVersion') == 'auto'})
    # Compare source/notice bytes with the supplied archive, independently of the
    # packager's inventory and hashes. Retain a compact archive for refusal cases.
    compact = {}
    source_checks = {}
    expected_source_paths = set()
    direct_files = {'LICENSE'}
    provenance_files = {'VERSION', 'go.mod', 'go.sum', 'contracts/toolkit.lock.json', 'contracts/expr-patch.json',
                        'compat/catalog.lock.json', 'scripts/windows-runtime.json'}
    with tarfile.open(source, 'r:*') as archive:
        source_commit = archive.pax_headers.get('comment')
        for member in archive.getmembers():
            if not member.isfile():
                continue
            name = member.name
            parts = pathlib.PurePosixPath(name).parts
            destination = None
            if name in direct_files:
                destination = name
            elif name in provenance_files:
                destination = 'provenance/' + name
            elif parts[:2] == ('licenses', 'windows-runtime'):
                destination = name
            elif parts[0] in ('vendor', 'schemas') and any(
                    token in parts[-1].upper() for token in ('LICENSE', 'NOTICE', 'COPYING', 'COPYRIGHT', 'PATENTS')):
                destination = 'licenses/' + name
            if destination is None:
                continue
            original = archive.extractfile(member).read()
            compact[name] = original
            expected_source_paths.add(destination)
            wanted = original
            source_checks[name] = (first / destination).is_file() and (first / destination).read_bytes() == wanted
    require('source-assets-and-notices', source_checks)
    windows_spec = json.loads(compact['scripts/windows-runtime.json'])
    windows_notices = {'licenses/windows-runtime/' + name: expected
                       for name, expected in windows_spec['legalFiles'].items()}
    require('pinned-windows-runtime-notices', {
        'originalNoticeHashes': all(sha(first / name) == expected for name, expected in windows_notices.items()),
        'exactNoticeInventory': {name for name in folder_paths if name.startswith('licenses/windows-runtime/')} == set(windows_notices),
        'platformBuildProvenance': (manifest.get('windowsRuntime') == windows_spec if platform.system() == 'Windows'
                                    else 'windowsRuntime' not in manifest)},
        distribution=windows_spec['distribution'], noticeCount=len(windows_notices))
    runtime_pin_controls(windows_spec, {name: compact[name] for name in windows_notices})
    generated = {native_name, 'README.txt', 'native/manifest.json', 'licenses/go/LICENSE', 'licenses/go/PATENTS',
                 'provenance/native-build.txt', 'provenance/native-version.stdout', 'provenance/native-version.stderr'}
    toolchain = command('go-root', ['go', 'env', 'GOROOT'])
    go_root = pathlib.Path(toolchain.stdout.decode().strip())
    require('complete-source-inventory', {'exactInventory': folder_paths == expected_source_paths | generated,
                                         'goToolchainRead': toolchain.returncode == 0,
                                         'goLicenses': all((first / 'licenses/go' / name).read_bytes() == (go_root / name).read_bytes()
                                                           for name in ('LICENSE', 'PATENTS'))})
    require('editor-independent-assets', {
        'noAdapterFiles': not any(path.startswith('extension/') or 'Cargo.' in path for path in folder_paths),
        'standardLSPCommand': 'serve --stdio' in (first / 'README.txt').read_text(),
        'catalogIdentity': identity.get('catalogHash') == sha(first / 'provenance/compat/catalog.lock.json')})
    metadata = command('native-build', ['go', 'version', '-m', str(binary)])
    settings = dict(line.split('\t')[-1].split('=', 1) for line in metadata.stdout.decode().splitlines()
                    if line.startswith('\tbuild\t') and '=' in line)
    expected_provenance = {'status': 'verified-vcs' if settings.get('vcs.revision') else 'unavailable',
                           'archiveCommit': source_commit, 'nativeCommit': settings.get('vcs.revision'),
                           'nativeDirty': settings['vcs.modified'] == 'true' if 'vcs.modified' in settings else None}
    require('source-identity', {'metadataRead': metadata.returncode == 0,
                              'accurateProvenance': manifest.get('sourceIdentity') == expected_provenance,
                              'matchingRevision': not settings.get('vcs.revision') or (source_commit == settings['vcs.revision'] and settings.get('vcs.modified') == 'false')},
            sourceIdentity=expected_provenance)
    for name, flag, value, reason in [
            ('bad-sha', '--sha256', '0' * 64, 'native or source archive SHA-256 mismatch'),
            ('bad-source-sha', '--source-sha256', '0' * 64, 'native or source archive SHA-256 mismatch'),
            ('bad-sha-syntax', '--sha256', 'not-a-sha', 'SHA-256 must be 64 lowercase hexadecimal characters'),
            ('bad-platform', '--platform', 'windows/amd64' if platform_id != 'windows/amd64' else 'linux/amd64', 'requested platform does not match'),
            ('missing-native', '--binary', out / 'absent-native', '[Errno 2]'),
            ('missing-source', '--source-archive', out / 'absent-source', '[Errno 2]')]:
        package(name, changed(flag, value), reason)
    args = base.copy()
    index = args.index('--sha256')
    del args[index:index + 2]
    package('missing-sha', args, 'the following arguments are required: --sha256')
    missing = dict(compact)
    del missing['VERSION']
    package('incomplete-source', archive_args(write_archive('incomplete', missing, source_commit)),
            'source archive is missing required file: VERSION')
    missing_pin = dict(compact)
    del missing_pin['scripts/windows-runtime.json']
    package('missing-windows-runtime-pin', archive_args(write_archive('missing-runtime-pin', missing_pin, source_commit)),
            'source archive is missing required file: scripts/windows-runtime.json')
    runtime_notice = 'licenses/windows-runtime/winpthreads/COPYING'
    missing_notice = dict(compact)
    del missing_notice[runtime_notice]
    package('missing-windows-runtime-notice', archive_args(write_archive('missing-runtime-notice', missing_notice, source_commit)),
            'Windows runtime notices differ from the pinned distribution')
    altered_notice = dict(compact)
    altered_notice[runtime_notice] += b'Changed despite a rehashed source archive.\n'
    package('altered-windows-runtime-notice', archive_args(write_archive('altered-runtime-notice', altered_notice, source_commit)),
            'Windows runtime notices differ from the pinned distribution')
    altered = dict(compact)
    altered['compat/catalog.lock.json'] += b' '
    package('catalog-mismatch', archive_args(write_archive('bad-catalog', altered, source_commit)),
            'native catalog hash does not match the source archive')
    if settings.get('vcs.revision'):
        package('revision-mismatch', archive_args(write_archive('bad-revision', compact, '0' * 40)),
                'native VCS revision does not match the source archive commit')
    unsafe = dict(compact)
    unsafe['../outside'] = b'not permitted'
    package('unsafe-source', archive_args(write_archive('unsafe', unsafe, source_commit)), 'unsafe source archive member')
    fake_identities(identity)
except Exception:
    (out / 'failure.txt').write_text(traceback.format_exc(), encoding='utf-8')
    if not results or results[-1]['passed']:
        results.append({'case': 'unexpected-test-failure', 'passed': False, 'traceback': traceback.format_exc()})
    raise
finally:
    save('manifest.json', {'command': sys.argv, 'python': sys.version, 'platform': platform.platform(),
                          'nativeSha256': sha(binary), 'sourceSha256': sha(source),
                          'testSha256': sha(pathlib.Path(__file__)), 'packagerSha256': sha(root / 'scripts/package-native.py'),
                          'caseCount': len(results), 'passed': bool(results) and all(row['passed'] for row in results),
                          'acceptanceBoundary': 'Packaging and extracted native CLI startup. No real Zed editor was launched.'})
    save('results.json', results)
