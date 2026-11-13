#!/usr/bin/env python3
"""A supported target completes a real resource; an unknown target is refused."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
binary = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT / 'artifacts/wf2-targets'
output.mkdir(parents=True, exist_ok=True)
expected_path = ROOT / 'tests/e2e/wf2.expected.json'
expected = json.loads(expected_path.read_text())
rows = []
with tempfile.TemporaryDirectory(prefix='wf-target-contract-') as temporary:
    work = Path(temporary)
    fixture = ROOT / 'tests/fixtures/schema/valid.yaml'
    (work / 'valid.yaml').write_bytes(fixture.read_bytes())
    for name, target, unknown in [('known', '3.6.0', False), ('unknown', expected['unknownTarget'], True)]:
        command = [str(binary), 'check', '--root', str(work), '--format', 'json', '--target-version', target]
        process = subprocess.run(command, capture_output=True, timeout=10)
        (output / (name + '.stdout')).write_bytes(process.stdout)
        (output / (name + '.stderr')).write_bytes(process.stderr)
        report = json.loads(process.stdout)
        checks = {
            'exit': process.returncode == (expected['unknownExit'] if unknown else 0),
            'completeness': report['analysisComplete'] is (not unknown),
            'catalog': isinstance(report['catalogHash'], str) and len(report['catalogHash']) == 64,
            'targetSelection': any('unknown target' in value for value in report['configurationErrors']) is unknown,
            'reportIdentity': report['product'] == expected['product'] and report['reportSchemaVersion'] == 1,
            'counts': (report['filesAnalyzed'], report['resourcesAnalyzed']) == ((0, 0) if unknown else (1, 1)),
            'diagnostics': report['diagnostics'] == [],
            'targetState': (all(value.get('analysisComplete') is False for value in report['targets']) if unknown
                            else len(report['targets']) == 1 and report['targets'][0]['version'] == target
                            and report['targets'][0]['analysisComplete'] is True),
        }
        rows.append({'name': name, 'command': command, 'exitCode': process.returncode,
                     'assertions': checks, 'pass': all(checks.values())})
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
(output / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')
(output / 'manifest.json').write_text(json.dumps({'command': sys.argv, 'binarySha256': sha(binary),
    'testSha256': sha(Path(__file__)), 'expectedSha256': sha(expected_path),
    'fixtureSha256': sha(fixture)}, indent=2) + '\n')
raise SystemExit(0 if len(rows) == 2 and all(row['pass'] for row in rows) else 1)
