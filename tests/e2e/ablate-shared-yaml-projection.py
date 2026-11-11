#!/usr/bin/env python3
"""Compile removal of each caller-owned YAML guard; require named LSP failures."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]).resolve()
ENV = os.environ | {'GOWORK': 'off', 'GOTOOLCHAIN': 'local', 'CGO_ENABLED': '1'}
rows = []
for name, required in [('original-source-check', 'selected-duplicate-key'), ('explicit-tag-policy', 'explicit-standard-tag')]:
    dest = OUT / name
    source = dest / 'source'
    source.mkdir(parents=True, exist_ok=False)
    for folder in ['cmd', 'internal', 'compat', 'schemas', 'vendor']:
        shutil.copytree(ROOT / folder, source / folder)
    for filename in ['go.mod', 'go.sum']:
        shutil.copyfile(ROOT / filename, source / filename)
    if name == 'original-source-check':
        path = source / 'internal/analysis/query_context.go'
        before = path.read_text()
        needle = 'if err != nil {\n\t\t\t\treturn fieldContext{}, err\n\t\t\t}\n\t\t\tshared = sharedOrigin(projected.Shared, span)'
        assert before.count(needle) == 1, 'original-source guard no longer matches'
        path.write_text(before.replace(needle, needle.replace('if err != nil {', 'if err != nil && false {', 1)))
    else:
        for filename in ['projection.go', 'expansion.go', 'query_context.go']:
            path = source / 'internal/analysis' / filename
            before = path.read_text()
            assert before.count('RejectExplicitTags: true') == 1, 'tag policy no longer matches'
            path.write_text(before.replace('RejectExplicitTags: true', 'RejectExplicitTags: false'))
    binary = dest / ('server.exe' if os.name == 'nt' else 'server')
    command = [str(Path(ENV['GOROOT']) / 'bin/go'), 'build', '-mod=vendor', '-trimpath', '-buildvcs=false', '-o', str(binary), './cmd/argo-workflows-ls']
    build = subprocess.run(command, cwd=source, env=ENV, capture_output=True, timeout=180)
    (dest / 'build.stderr').write_bytes(build.stderr)
    row = {'name': name, 'buildExit': build.returncode, 'requiredFailure': required, 'detected': False}
    if build.returncode == 0:
        row['binarySHA256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        consumer = subprocess.run([sys.executable, str(ROOT / 'tests/e2e/wf-shared-yaml-projection.py'), str(binary), str(dest / 'consumer')], cwd=ROOT, env=ENV, capture_output=True, timeout=90)
        (dest / 'consumer.stdout').write_bytes(consumer.stdout)
        (dest / 'consumer.stderr').write_bytes(consumer.stderr)
        cases = json.loads((dest / 'consumer/results.json').read_text())
        row['consumerExit'] = consumer.returncode
        row['failedCases'] = [case['case'] for case in cases if not case['pass']]
        row['detected'] = consumer.returncode == 1 and len(cases) == 4 and required in row['failedCases']
    rows.append(row)
    (OUT / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')
    print(json.dumps(row), flush=True)
sys.exit(0 if len(rows) == 2 and all(row['buildExit'] == 0 and row['detected'] for row in rows) else 1)
