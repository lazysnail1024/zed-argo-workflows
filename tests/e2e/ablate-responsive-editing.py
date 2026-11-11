#!/usr/bin/env python3
"""Require real protocol failures after removing each foreground protection."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('binary', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
OUT = args.output.resolve()
OUT.mkdir(parents=True, exist_ok=False)
ENV = os.environ | {'GOWORK': 'off', 'GOTOOLCHAIN': 'local', 'CGO_ENABLED': '1', 'GOPROXY': 'off', 'GOSUMDB': 'off'}
GO = str(Path(ENV['GOROOT']) / 'bin/go') if ENV.get('GOROOT') else 'go'
CONSUMER = ROOT / 'tests/e2e/wf-responsive-editing.py'


def command(folder, label, argv, cwd=ROOT):
    result = subprocess.run([str(x) for x in argv], cwd=cwd, env=ENV, capture_output=True, timeout=180)
    (folder / (label + '.stdout')).write_bytes(result.stdout)
    (folder / (label + '.stderr')).write_bytes(result.stderr)
    (folder / (label + '.command.json')).write_text(json.dumps({'argv': [str(x) for x in argv], 'exitCode': result.returncode}) + '\n')
    return result


def cases(path):
    rows = json.loads(path.read_text())
    assert rows and all(isinstance(row.get('assertions'), dict) and row['assertions'] for row in rows), rows
    return {row['name']: row for row in rows}


baseline = OUT / 'baseline'
baseline.mkdir()
run = command(baseline, 'consumer', [sys.executable, CONSUMER, args.binary.resolve(), baseline / 'e2e', '--cases', 'churn,lanes,fences'])
before = cases(baseline / 'e2e/results.json')
rows = [{'id': 'baseline', 'passed': run.returncode == 0 and all(row['pass'] is True for row in before.values())}]
variants = [
    ('lane-isolation', 'case "textDocument/hover", "textDocument/completion", "textDocument/documentSymbol":\n\t\treturn 0',
     'case "textDocument/hover", "textDocument/completion", "textDocument/documentSymbol":\n\t\treturn 1',
     'lanes', 'light_capacity_reserved_under_heavy_saturation'),
    ('unrelated-input-isolation', 'if !input.readOnly {', 'if true {',
     'churn', 'every_unrelated_edit_preserves_useful_hover'),
    ('buffer-fence', 'if opened != input.opened || opened && (b.snapshot.Revision() != input.revision || b.version != input.version || b.unavailable != input.unavailable) {',
     'if false && (opened != input.opened || opened && (b.snapshot.Revision() != input.revision || b.version != input.version || b.unavailable != input.unavailable)) {',
     'fences', 'own_buffer_change_rejects_active_and_queued_results'),
    ('root-input-fence', 'return root == input.rootState && (root == nil || root.inputEpoch == input.rootEpoch)',
     '_ = root\n\treturn true', 'fences', 'same_path_root_replacement_rejects_old_requests'),
]
if rows[0]['passed']:
    for name, old, new, case, required in variants:
        folder = OUT / name
        folder.mkdir()
        row = {'id': name, 'passed': False, 'requiredCase': case, 'requiredAssertion': required}
        try:
            with tempfile.TemporaryDirectory(prefix='wf-responsive-ablation-') as temporary:
                source = Path(temporary)
                for directory in ('cmd', 'internal', 'compat', 'schemas', 'vendor'):
                    shutil.copytree(ROOT / directory, source / directory)
                for filename in ('go.mod', 'go.sum'):
                    shutil.copyfile(ROOT / filename, source / filename)
                path = source / 'internal/lsp/request.go'
                original = path.read_text()
                assert original.count(old) == 1, 'mutation must match exactly once: ' + name
                changed = original.replace(old, new, 1)
                path.write_text(changed)
                (folder / 'mutation.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True), changed.splitlines(True), str(path), str(path))))
                binary = source / ('server.exe' if os.name == 'nt' else 'server')
                build = command(folder, 'build', [GO, 'build', '-mod=vendor', '-buildvcs=false', '-trimpath', '-o', binary, './cmd/argo-workflows-ls'], source)
                row['buildExit'] = build.returncode
                assert build.returncode == 0, 'mutant must compile'
                row['binarySha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
                probe = command(folder, 'probe', [sys.executable, CONSUMER, binary, folder / 'e2e', '--cases', case])
                after = cases(folder / 'e2e/results.json')
                row['probeExit'] = probe.returncode
                row['namedFailure'] = before[case]['assertions'].get(required) is True and after.get(case, {}).get('assertions', {}).get(required) is False
                row['completeAssertions'] = set(after.get(case, {}).get('assertions', {})) == set(before[case]['assertions'])
                row['passed'] = probe.returncode == 1 and row['namedFailure'] and row['completeAssertions']
        except Exception:
            row['error'] = traceback.format_exc()
        rows.append(row)
        print(json.dumps(row), flush=True)
        (OUT / 'report.json').write_text(json.dumps({'passed': False, 'rows': rows}, indent=2) + '\n')
passed = len(rows) == len(variants) + 1 and all(row['passed'] for row in rows)
(OUT / 'report.json').write_text(json.dumps({'passed': passed, 'rows': rows}, indent=2) + '\n')
raise SystemExit(0 if passed else 1)
