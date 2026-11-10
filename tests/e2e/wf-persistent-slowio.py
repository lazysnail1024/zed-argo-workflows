#!/usr/bin/env python3
"""Real stdio discovery across the unchanged five-second deadline under slow reads."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'internal/workspace/stream.go'
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(name, argv, timeout):
    result = subprocess.run([str(x) for x in argv], cwd=ROOT, capture_output=True, timeout=timeout)
    (OUT / (name + '.stdout')).write_bytes(result.stdout)
    (OUT / (name + '.stderr')).write_bytes(result.stderr)
    (OUT / (name + '.command.json')).write_text(json.dumps({
        'argv': [str(x) for x in argv], 'exitCode': result.returncode, 'timeoutSeconds': timeout,
    }, indent=2) + '\n')
    return result


original = SOURCE.read_text()
needle = 'func ReadText(ctx context.Context, filename string) (string, error) {'
assert original.count(needle) == 1 and original.count('"strings"') == 1
injected = OUT / 'stream.go'
slow = '''
    timer := time.NewTimer(15 * time.Millisecond)
    defer timer.Stop()
    select {
    case <-ctx.Done():
        return "", ctx.Err()
    case <-timer.C:
    }
'''
injected.write_text(original.replace('"strings"', '"strings"\n"time"').replace(needle, needle + slow))
overlay = OUT / 'overlay.json'
overlay.write_text(json.dumps({'Replace': {str(SOURCE): str(injected)}}))
binary = OUT / ('slowio-control.exe' if os.name == 'nt' else 'slowio-control')
build = command('build', ['go', 'build', '-mod=vendor', '-overlay', overlay, '-o', binary, './cmd/argo-workflows-ls'], 120)
if build.returncode:
    sys.stderr.buffer.write(build.stderr)
    raise SystemExit(build.returncode)
run = command('control', [sys.executable, ROOT / 'tests/e2e/wf-persistent-workspace.py', binary,
                          OUT / 'control', '--count', '1024', '--padding', '64',
                          '--resource-every', '8', '--deadline', '75', '--require-slow'], 180)
actual = json.loads((OUT / 'control/results.json').read_text())
row = {'case': 'slow-io-resumable-discovery', 'assertions': actual['checks'], 'pass': actual['pass']}
(OUT / 'results.json').write_text(json.dumps([row], indent=2) + '\n')
(OUT / 'manifest.json').write_text(json.dumps({
    'command': sys.argv, 'binarySha256': sha(Path(sys.argv[1])), 'controlBinarySha256': sha(binary),
    'testSha256': sha(Path(__file__)), 'originalSourceSha256': sha(SOURCE),
    'injectedSourceSha256': sha(injected),
    'injection': '15 ms cancellable delay per actual single-file read; all production deadlines and scanner state unchanged.',
    'boundary': 'Fault-injected native stdio, not disk bandwidth emulation or Zed GUI.',
}, indent=2) + '\n')
print(json.dumps(row, indent=2), flush=True)
raise SystemExit(0 if run.returncode == 0 and row['pass'] else 1)
