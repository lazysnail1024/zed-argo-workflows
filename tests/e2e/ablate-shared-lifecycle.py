#!/usr/bin/env python3
"""Reproduce seven fixed lifecycle negative controls without editing production files."""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
AUTHORITY = 'TestAcceptedDocumentsShareAuthorityAcrossRootsAndRefusal'
# file, unique original, replacement, existing test, required assertion
VARIANTS = {
    'root-removal-closes-editor-buffer': (
        'catalog.go',
        'func (c *catalog) remove(uri source.URI) {\n\tc.documents.Invalidate(uri)',
        'func (c *catalog) remove(uri source.URI) {\n\tc.documents.Close(uri)',
        AUTHORITY, 'accepted input must borrow one authoritative snapshot:'),
    'configuration-limit-removed': (
        'stdio.go',
        'if filepath.Base(name) == ".argo-workflows-ls.yaml" {\n\t\tlimit = 1024 * 1024',
        'if filepath.Base(name) == ".argo-workflows-ls.yaml" {\n\t\tlimit = 5 * 1024 * 1024',
        AUTHORITY, "configuration's 1 MiB limit was lost"),
    'obsolete-root-sweep': (
        'catalog_discovery.go',
        'if c.roots[root.header.Root] != root || root.scanEpoch != root.epoch || !root.done || root.reported {',
        'if !root.done || root.reported {',
        'TestCatalogObsoletePassCannotSweepCurrentRoot',
        'superseded root completion deleted current-root input or published readiness'),
    'refusal-without-input-fence': (
        'catalog.go',
        '\t\t\tfile.stamp++ // Revoke work that has not obtained an index ticket yet.\n', '',
        AUTHORITY, 'refused input did not revoke a captured file proof'),
    'close-without-observation': (
        'catalog.go',
        'file.opened, file.blocked = false, false\n\t\t\tfile.seen = c.roots[root].membership.Observe()',
        'file.opened, file.blocked = false, false',
        'TestCatalogObservesSavedBufferAfterScannedDirectory',
        'scan completion deleted a closed authoritative input before its disk read'),
    'incomplete-scan-sweep': (
        'catalog_discovery.go',
        'root.membership.Finish(root.scanPass, root.problem == "" && root.scanProblem == "")',
        'root.membership.Finish(root.scanPass, true)',
        'TestCatalogIncompleteScanDoesNotProveDeletion',
        'partial enumeration deleted a known member or established completeness'),
    'cancelled-done-treated-complete': (
        'catalog_discovery.go',
        '\tif batch.Done && !batch.Complete && root.scanProblem == "" {\n'
        '\t\troot.scanProblem = "workspace discovery is incomplete"\n\t}\n', '',
        'TestCatalogCanceledExhaustedScanDoesNotProveDeletion',
        'canceled exhausted scan established deletion or readiness'),
}

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', nargs='?', type=pathlib.Path, help='empty evidence directory; default: a retained temporary directory')
parser.add_argument('--variant', action='append', choices=VARIANTS, help='run only this variant; repeatable, defaults to all seven')
args = parser.parse_args()
selected = list(dict.fromkeys(args.variant or VARIANTS))
go = os.environ.get('GO') or (str(pathlib.Path(os.environ['GOROOT']) / 'bin' / ('go.exe' if os.name == 'nt' else 'go'))
                              if os.environ.get('GOROOT') else shutil.which('go'))
if not go:
    parser.error('Go is required via GO, GOROOT or PATH')
out = args.output.resolve() if args.output else pathlib.Path(tempfile.mkdtemp(prefix='wf-shared-lifecycle-'))
out.mkdir(parents=True, exist_ok=True)
if any(out.iterdir()):
    parser.error('evidence directory must be empty: ' + str(out))
print('Evidence: ' + str(out), flush=True)
report = {'go': go, 'variants': selected, 'positive': False, 'results': []}


def step(folder, label, command, timeout):
    with (folder / (label + '.stdout')).open('wb') as stdout, (folder / (label + '.stderr')).open('wb') as stderr:
        try:
            result = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=timeout)
            record = {'command': command, 'exitCode': result.returncode}
        except subprocess.TimeoutExpired:
            record = {'command': command, 'exitCode': None, 'timedOut': True}
    (folder / (label + '.json')).write_text(json.dumps(record, indent=2) + '\n')
    if record.get('timedOut'):
        raise RuntimeError(label + ' timed out; this is not a detected negative control')
    return record['exitCode'], (folder / (label + '.stdout')).read_text(errors='replace') + (folder / (label + '.stderr')).read_text(errors='replace')


try:
    tests = sorted({VARIANTS[name][3] for name in selected})
    baseline = out / ('positive.test.exe' if os.name == 'nt' else 'positive.test')
    code, _ = step(out, 'positive-build', [go, 'test', '-c', '-mod=vendor', '-o', str(baseline), './internal/lsp'], 180)
    if code != 0:
        raise RuntimeError('unmodified positive control did not compile')
    code, output = step(out, 'positive-run', [str(baseline), '-test.v', '-test.run', '^(' + '|'.join(tests) + ')$', '-test.timeout=30s'], 40)
    if code != 0 or not all('--- PASS: ' + test + ' (' in output for test in tests):
        raise RuntimeError('unmodified positive control did not pass every selected test')
    report['positive'] = True
    print('PASS positive: ' + str(len(tests)) + ' existing tests', flush=True)
    for name in selected:
        filename, before, after, test, assertion = VARIANTS[name]
        folder = out / name
        folder.mkdir()
        original = ROOT / 'internal/lsp' / filename
        source = original.read_text()
        if source.count(before) != 1:
            raise RuntimeError(name + ': mutation anchor must occur exactly once')
        mutated = folder / filename
        mutated.write_text(source.replace(before, after, 1))
        overlay = folder / 'overlay.json'
        overlay.write_text(json.dumps({'Replace': {str(original): str(mutated)}}, indent=2) + '\n')
        binary = folder / ('negative.test.exe' if os.name == 'nt' else 'negative.test')
        row = {'variant': name, 'test': test, 'assertion': assertion, 'compiled': False, 'detected': False}
        report['results'].append(row)
        code, _ = step(folder, 'build', [go, 'test', '-c', '-mod=vendor', '-overlay', str(overlay), '-o', str(binary), './internal/lsp'], 180)
        row['compiled'] = code == 0
        if code != 0:
            raise RuntimeError(name + ': mutation did not compile')
        code, output = step(folder, 'run', [str(binary), '-test.v', '-test.run', '^' + test + '$', '-test.timeout=30s'], 40)
        row['detected'] = (code == 1 and '--- FAIL: ' + test + ' (' in output
                           and re.search(r'catalog_test\.go:\d+: ' + re.escape(assertion), output) is not None
                           and not any(marker in output for marker in ('panic:', 'fatal error:', 'test timed out')))
        if not row['detected']:
            raise RuntimeError(name + ': expected behavioral assertion was not detected')
        print('DETECTED ' + name, flush=True)
finally:
    (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
