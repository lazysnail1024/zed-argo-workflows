#!/usr/bin/env python3
"""Inject slow analysis, retaining the real deadline and publication machinery."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'internal/lsp/serve.go'
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(name, argv, timeout):
    result = subprocess.run([str(x) for x in argv], cwd=ROOT, capture_output=True, timeout=timeout)
    (OUT / (name + '.stdout')).write_bytes(result.stdout)
    (OUT / (name + '.stderr')).write_bytes(result.stderr)
    (OUT / (name + '.command.json')).write_text(json.dumps({
        'argv': [str(x) for x in argv], 'exitCode': result.returncode,
        'timeoutSeconds': timeout,
    }, indent=2) + '\n', encoding='utf-8')
    return result


original = SOURCE.read_text(encoding='utf-8')
needle = 'err = state.publish(uri)'
assert original.count(needle) == 1, 'analysis boundary changed; review the control'
injected = OUT / 'serve.go'
injected.write_text(original.replace(needle, '<-ctx.Done()\n\t\t\t\t\t' + needle), encoding='utf-8')
overlay = OUT / 'overlay.json'
overlay.write_text(json.dumps({'Replace': {str(SOURCE): str(injected)}}), encoding='utf-8')
binary = OUT / ('deadline-control.exe' if os.name == 'nt' else 'deadline-control')
build = command('build', ['go', 'build', '-mod=vendor', '-overlay', overlay, '-o', binary, './cmd/argo-workflows-ls'], 120)
if build.returncode != 0:
    sys.stderr.buffer.write(build.stderr)
    raise SystemExit(build.returncode)
run = command('control', [sys.executable, ROOT / 'tests/e2e/wf-diagnostic-ownership.py',
                          binary, OUT / 'control', '--deadline'], 40)
sys.stdout.buffer.write(run.stdout)
sys.stderr.buffer.write(run.stderr)
rows = json.loads((OUT / 'control/results.json').read_text(encoding='utf-8'))
assert [row['case'] for row in rows] == ['deadline-isolation', 'deadline-edit-recovery']
(OUT / 'results.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
(OUT / 'manifest.json').write_text(json.dumps({
    'command': sys.argv, 'binarySha256': sha(Path(sys.argv[1])),
    'controlBinarySha256': sha(binary), 'testSha256': sha(Path(__file__)),
    'originalSourceSha256': sha(SOURCE), 'injectedSourceSha256': sha(injected),
    'injection': 'Wait for the existing context deadline immediately before expensive publication.',
    'boundary': 'Fault-injected stdio LSP control, not the unmodified release binary or Zed GUI.',
}, indent=2) + '\n', encoding='utf-8')
raise SystemExit(0 if run.returncode == 0 and all(row['pass'] is True for row in rows) else 1)
