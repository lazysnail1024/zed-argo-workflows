#!/usr/bin/env python3
"""Exercise real dependency preparation in an isolated module cache, not a mock."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def inventory(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'passed': False, 'checks': {}, 'commands': []}

    def invoke(name, source, env, refusal=None):
        process = subprocess.run([sys.executable, source/'scripts/vendor-native.py'], cwd=source,
                                 env=env, capture_output=True, timeout=600)
        (output/(name+'.stdout')).write_bytes(process.stdout)
        (output/(name+'.stderr')).write_bytes(process.stderr)
        text = (process.stdout+process.stderr).decode(errors='replace')
        passed = process.returncode == 0 if refusal is None else process.returncode != 0 and refusal in text
        report['commands'].append({'name': name, 'exit': process.returncode, 'expectedRefusal': refusal, 'passed': passed})
        if not passed:
            raise AssertionError(name+': '+text[-4000:])

    try:
        with tempfile.TemporaryDirectory(prefix='workflow-module-sources-') as temporary:
            base = Path(temporary)
            source = base/'source'
            source.mkdir()
            names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
            for name in sorted(set(names)-{''}):
                path = ROOT/name
                if name.startswith(('vendor/', 'editors/')) or not path.is_file():
                    continue
                if path.is_symlink():
                    raise ValueError('source fixture must contain regular files')
                target = source/name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            cache = base/'modules'
            env = os.environ | {'GOENV': 'off', 'GOWORK': 'off', 'GOTOOLCHAIN': 'local', 'GOFLAGS': '', 'GOMODCACHE': str(cache)}
            locks = {name: (source/name).read_bytes() for name in ('go.mod', 'go.sum')}
            report['checks']['noInitialVendorOrModuleCache'] = not (source/'vendor').exists() and not cache.exists()
            invoke('cold-cache', source, env)
            baseline = inventory(source/'vendor')
            cached = inventory(cache)
            offline = env | {'GOPROXY': 'off'}
            invoke('offline-regeneration', source, offline)
            report['checks']['deterministicSourceBytes'] = baseline == inventory(source/'vendor')
            report['checks']['moduleCacheUnmodifiedByPatches'] = cached == inventory(cache)
            poisoned = source/'vendor/github.com/expr-lang/expr/file/error.go'
            poisoned.write_text('invalid Go source must never be compiled\n')
            invoke('poisoned-generated-source', source, offline)
            report['checks']['poisonedVendorRegenerated'] = baseline == inventory(source/'vendor')
            outside = base/'outside-vendor'
            outside.mkdir()
            (outside/'sentinel').write_bytes(b'external content must not change')
            outside.chmod(0o500)
            outside_mode = outside.stat().st_mode
            previous = base/'previous-vendor'
            (source/'vendor').rename(previous)
            (source/'vendor').symlink_to(outside, target_is_directory=True)
            try:
                invoke('vendor-root-symlink', source, offline, 'symlinks and junctions are not accepted')
            finally:
                (source/'vendor').unlink()
                previous.rename(source/'vendor')
            link = source/'vendor/poisoned-link'
            link.symlink_to(outside, target_is_directory=True)
            try:
                invoke('vendor-entry-symlink', source, offline, 'symlinks and junctions are not accepted')
            finally:
                link.unlink()
            report['checks']['externalSentinelBytesAndModeUnchanged'] = outside.stat().st_mode == outside_mode and (outside/'sentinel').read_bytes() == b'external content must not change'

            def negative(name, relative, content, refusal):
                path = source/relative
                original = path.read_bytes()
                try:
                    if content is None:
                        path.unlink()
                    else:
                        path.write_bytes(content)
                    invoke(name, source, offline, refusal)
                finally:
                    path.write_bytes(original)

            negative('replacement', 'go.mod', locks['go.mod']+b'\nreplace github.com/expr-lang/expr => ../untrusted\n', 'replacements are not accepted')
            lines = locks['go.sum'].splitlines(keepends=True)
            at = next(i for i, line in enumerate(lines) if line.startswith(b'github.com/expr-lang/expr v1.16.9 h1:'))
            fields = lines[at].split()
            fields[-1] = b'h1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
            lines[at] = b' '.join(fields)+b'\n'
            negative('checksum-mismatch', 'go.sum', b''.join(lines), 'checksum mismatch')
            patch = json.loads((source/'contracts/expr-patch.json').read_bytes())
            patch['upstreamSha256'] = '0'*64
            negative('patch-input-mismatch', 'contracts/expr-patch.json', json.dumps(patch).encode(), 'patch input hash differs')
            patch = json.loads((source/'contracts/expr-patch.json').read_bytes())
            patch['patchedSha256'] = '0'*64
            negative('patch-output-mismatch', 'contracts/expr-patch.json', json.dumps(patch).encode(), 'patch output hash differs')
            negative('missing-patch', 'contracts/expr-linear-error.patch', None, 'No such file or directory')
            lock = json.loads((source/'contracts/toolkit.lock.json').read_bytes())
            lock['implementationCommit'] = '0'*40
            negative('toolkit-commit-mismatch', 'contracts/toolkit.lock.json', json.dumps(lock).encode(), 'Toolkit source commit differs')
            negative('missing-original-notice', 'licenses/modules/github.com/expr-lang/expr/LICENSE', None, 'legal notices differ')
            # Only this disposable cache is altered; never write to the user's cache.
            cached_source = cache/'github.com/expr-lang/expr@v1.16.9/file/error.go'
            cached_source.chmod(cached_source.stat().st_mode | 0o600)
            cached_source.write_bytes(cached_source.read_bytes()+b'\n// unexpected cache modification\n')
            invoke('modified-module-cache', source, offline, 'dir has been modified')
            report['checks']['lockedInputsUnchanged'] = all((source/name).read_bytes() == value for name, value in locks.items())
            report['generatedFiles'] = len(baseline)
            report['passed'] = all(report['checks'].values()) and all(row['passed'] for row in report['commands'])
    except Exception as error:
        report['error'] = repr(error)
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
