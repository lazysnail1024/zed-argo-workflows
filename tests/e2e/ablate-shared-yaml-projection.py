#!/usr/bin/env python3
"""Compile removal of each caller-owned YAML guard; require named LSP failures."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import difflib
import subprocess
import sys
import tempfile
from mutation_support import classify_results, read_results

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path)
parser.add_argument('--binary', type=Path, help='already-built unchanged native baseline')
args = parser.parse_args()
OUT = args.output.resolve()
OUT.mkdir(parents=True, exist_ok=False)
ENV = os.environ | {'GOWORK': 'off', 'GOTOOLCHAIN': 'local', 'CGO_ENABLED': '1', 'GOPROXY': 'off', 'GOSUMDB': 'off'}
GO = str(Path(ENV['GOROOT']) / 'bin/go') if ENV.get('GOROOT') else 'go'
rows = []
baseline = args.binary.resolve() if args.binary else OUT / 'baseline-server'
if not args.binary:
    build = subprocess.run([GO, 'build', '-mod=vendor', '-trimpath', '-buildvcs=false', '-o', str(baseline), './cmd/argo-workflows-ls'], cwd=ROOT, env=ENV, capture_output=True, timeout=180)
    (OUT / 'baseline-build.stdout').write_bytes(build.stdout)
    (OUT / 'baseline-build.stderr').write_bytes(build.stderr)
    if build.returncode != 0:
        raise SystemExit('baseline must compile before any mutation')
control = subprocess.run([sys.executable, str(ROOT / 'tests/e2e/wf-shared-yaml-projection.py'), str(baseline), str(OUT / 'baseline')], cwd=ROOT, env=ENV, capture_output=True, timeout=90)
(OUT / 'baseline.stdout').write_bytes(control.stdout)
(OUT / 'baseline.stderr').write_bytes(control.stderr)
before_cases = read_results(OUT / 'baseline')
if control.returncode != 0 or len(before_cases) != 4 or not all(row['pass'] for row in before_cases.values()):
    raise SystemExit('complete unchanged consumer baseline must pass')
for name, required in [('original-source-check', 'selected-duplicate-key'), ('explicit-tag-policy', 'explicit-standard-tag')]:
    dest = OUT / name
    dest.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='wf-yaml-projection-ablation-') as temporary:
        temp = Path(temporary)
        replace_map = {}
        diffs = []
        if name == 'original-source-check':
            original = ROOT / 'internal/analysis/query_context.go'
            before = original.read_text()
            needle = 'if err != nil {\n\t\t\t\treturn fieldContext{}, err\n\t\t\t}\n\t\t\tshared = sharedOrigin(projected.Shared, span)'
            assert before.count(needle) == 1, 'original-source guard no longer matches'
            after = before.replace(needle, needle.replace('if err != nil {', 'if err != nil && false {', 1))
            mutated = temp / 'query_context.go'
            mutated.write_text(after)
            replace_map[str(original)] = str(mutated)
            diffs.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True), 'a/internal/analysis/query_context.go', 'b/internal/analysis/query_context.go'))
        else:
            for filename in ['projection.go', 'expansion.go', 'query_context.go']:
                original = ROOT / 'internal/analysis' / filename
                before = original.read_text()
                assert before.count('RejectExplicitTags: true') == 1, 'tag policy no longer matches'
                after = before.replace('RejectExplicitTags: true', 'RejectExplicitTags: false')
                mutated = temp / filename
                mutated.write_text(after)
                replace_map[str(original)] = str(mutated)
                diffs.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True), 'a/internal/analysis/' + filename, 'b/internal/analysis/' + filename))
        (dest / 'mutation.diff').write_text(''.join(diffs))
        overlay = temp / 'overlay.json'
        overlay.write_text(json.dumps({'Replace': replace_map}, indent=2) + '\n')
        binary = temp / ('server.exe' if os.name == 'nt' else 'server')
        command = [GO, 'build', '-mod=vendor', '-trimpath', '-buildvcs=false', '-overlay', str(overlay), '-o', str(binary), './cmd/argo-workflows-ls']
        build = subprocess.run(command, cwd=ROOT, env=ENV, capture_output=True, timeout=180)
        (dest / 'build.stderr').write_bytes(build.stderr)
        row = {'name': name, 'buildExit': build.returncode, 'requiredFailure': required, 'detected': False}
        if build.returncode == 0:
            row['binarySHA256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
            consumer = subprocess.run([sys.executable, str(ROOT / 'tests/e2e/wf-shared-yaml-projection.py'), str(binary), str(dest / 'consumer')], cwd=ROOT, env=ENV, capture_output=True, timeout=90)
            (dest / 'consumer.stdout').write_bytes(consumer.stdout)
            (dest / 'consumer.stderr').write_bytes(consumer.stderr)
            cases = read_results(dest / 'consumer')
            row['consumerExit'] = consumer.returncode
            row['failedCases'] = [name for name, case in cases.items() if not case['pass']]
            row.update(classify_results(((required, 'explicitRefusal'),), before_cases, cases, {'exitCode': consumer.returncode}))
    rows.append(row)
    (OUT / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')
    print(json.dumps(row), flush=True)
sys.exit(0 if len(rows) == 2 and all(row['buildExit'] == 0 and row['detected'] for row in rows) else 1)
