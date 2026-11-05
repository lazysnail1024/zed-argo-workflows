#!/usr/bin/env python3
"""Exercise the Linux workflow's checksum command and README from real layouts."""
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import traceback
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
if len(sys.argv) != 2:
    raise SystemExit('usage: wf-bundle-checksums.py OUTPUT_DIRECTORY (requires bash and sha256sum)')
OUT = pathlib.Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
WORKFLOW = ROOT / '.github/workflows/development-bundle.yml'
README = ROOT / 'README.md'
README_COMMAND = ['sha256sum', '-c', 'SHA256SUMS']
LEGACY_COMMAND = 'sha256sum dist/*.zip > dist/SHA256SUMS'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def result(name, assertions, **details):
    passed = bool(assertions) and all(value is True for value in assertions.values())
    RESULTS.append({'case': name, 'assertions': assertions, 'pass': passed, 'details': details})
    save(OUT / 'results.json', RESULTS)
    print(name, 'PASS' if passed else 'FAIL', flush=True)


def command(name, argv, cwd):
    argv = [str(value) for value in argv]
    locale = {'LANG': 'C', 'LC_ALL': 'C'}
    save(OUT / (name + '.command.json'), {
        'argv': argv, 'cwd': str(cwd), 'timeoutSeconds': 15, 'environmentOverrides': locale,
    })
    process = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=15, env=os.environ | locale)
    (OUT / (name + '.stdout')).write_bytes(process.stdout)
    (OUT / (name + '.stderr')).write_bytes(process.stderr)
    save(OUT / (name + '.exit.json'), {'exitCode': process.returncode})
    return process


def shell(name, text, cwd):
    return command(name, ['bash', '-eu', '-o', 'pipefail', '-c', text], cwd)


try:
    if shutil.which('bash') is None or shutil.which('sha256sum') is None:
        raise RuntimeError('this Linux workflow acceptance requires bash and sha256sum')
    # Run the production workflow line rather than a second implementation of
    # its path handling. The expected layout and verifier are independent.
    generators = [line.strip() for line in WORKFLOW.read_text(encoding='utf-8').splitlines()
                  if re.search(r'\bsha256sum\b.*>\s*(?:dist/)?SHA256SUMS\b', line)]
    if len(generators) != 1:
        raise AssertionError('expected one checksum-generation command in development-bundle.yml')
    documented = 'sha256sum -c SHA256SUMS' in README.read_text(encoding='utf-8')
    work = OUT / 'layout'
    dist = work / 'dist'
    dist.mkdir(parents=True, exist_ok=False)
    names = ['zed-argo-workflows-dev-linux-amd64.zip', 'zed-argo-workflows-dev-linux-arm64.zip']
    for index, name in enumerate(names):
        with zipfile.ZipFile(dist / name, 'w') as archive:
            archive.writestr('fixture.txt', 'checksum layout fixture ' + str(index) + '\n')
    expected = {name: sha(dist / name) for name in names}
    generated = shell('workflow-generation', generators[0], work)
    manifest = dist / 'SHA256SUMS'
    rows = []
    if manifest.is_file():
        for line in manifest.read_text(encoding='utf-8').splitlines():
            digest, separator, name = line.partition('  ')
            rows.append((digest, name, bool(separator)))
    portable = bool(rows) and all(
        separated and re.fullmatch('[0-9a-f]{64}', digest) is not None
        and not pathlib.PurePosixPath(name).is_absolute()
        and pathlib.PurePosixPath(name).parent == pathlib.PurePosixPath('.')
        for digest, name, separated in rows)
    actual = {pathlib.PurePosixPath(name).name: digest for digest, name, _ in rows}
    result('workflow-layout', {
        'productionCommandSucceeded': generated.returncode == 0,
        'readmeCommandDocumented': documented,
        'portableNames': portable,
        'exactZipInventory': len(rows) == len(expected) and actual == expected,
        'allFilesDelivered': bool(rows) and all((dist / name).is_file() for _, name, _ in rows),
    }, command=generators[0], manifest=manifest.read_text(encoding='utf-8') if manifest.exists() else None)

    checked = command('readme-check', README_COMMAND, dist)
    result('readme-check', {'accepted': checked.returncode == 0, 'noWarnings': checked.stderr == b''},
           exitCode=checked.returncode)

    relocated = OUT / 'relocated bundle'
    relocated.mkdir(exist_ok=False)
    for name in names + ['SHA256SUMS']:
        shutil.copy2(dist / name, relocated / name)
    moved = command('relocated-check', README_COMMAND, relocated)
    result('relocated-check', {
        'sameBytes': all((dist / name).read_bytes() == (relocated / name).read_bytes()
                         for name in names + ['SHA256SUMS']),
        'accepted': moved.returncode == 0,
    }, exitCode=moved.returncode)

    tampered = relocated / names[0]
    tampered.write_bytes(tampered.read_bytes() + b'\x00')
    rejected = command('tampered-check', README_COMMAND, relocated)
    result('tampered-check', {
        'bytesChanged': sha(tampered) != expected[names[0]],
        'checksumRejected': rejected.returncode == 1 and b'FAILED' in rejected.stdout,
        'filesStillPresent': all((relocated / name).is_file() for name in names),
    }, exitCode=rejected.returncode)

    # A negative control must execute the old command successfully and fail at
    # the documented verification step, not at fixture creation or tool launch.
    legacy = OUT / 'legacy-layout'
    (legacy / 'dist').mkdir(parents=True, exist_ok=False)
    for name in names:
        shutil.copy2(dist / name, legacy / 'dist' / name)
    legacy_generated = shell('legacy-generation', LEGACY_COMMAND, legacy)
    legacy_checked = command('legacy-readme-check', README_COMMAND, legacy / 'dist')
    result('legacy-layout-rejected', {
        'oldCommandExecuted': legacy_generated.returncode == 0,
        'documentedCheckRejected': legacy_checked.returncode == 1,
        'pathFailureObserved': b'FAILED open or read' in legacy_checked.stdout,
        'unchangedDeliveredBytes': all(sha(legacy / 'dist' / name) == expected[name] for name in names),
    }, command=LEGACY_COMMAND, exitCode=legacy_checked.returncode)
except Exception as error:
    result('runner', {'completed': False}, error=repr(error), traceback=traceback.format_exc())
finally:
    save(OUT / 'manifest.json', {
        'command': sys.argv, 'testSha256': sha(pathlib.Path(__file__)),
        'workflowSha256': sha(WORKFLOW), 'readmeSha256': sha(README),
        'expectedCases': ['workflow-layout', 'readme-check', 'relocated-check',
                          'tampered-check', 'legacy-layout-rejected'],
        'acceptance': 'Linux checksum command and file layout; no hosted run or Zed installation claimed',
    })
sys.exit(0 if len(RESULTS) == 5 and all(row['pass'] for row in RESULTS) else 1)
