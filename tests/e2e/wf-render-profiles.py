#!/usr/bin/env python3
"""Independent saved-profile/owned-cache contract, against both real Helm majors.

The dev/prod/reverse names are predetermined from ordered values and a literal
template, not copied from production output. Every collected/resolved action is
observed through a real-Helm invocation logger. The same script is used before
the feature exists, retaining default behavior and missing-feature evidence.
"""
import copy
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
CASES = ['default', 'default-host-response', 'dev', 'prod', 'reverse', 'strict-config', 'conflicts', 'editor',
         'stale-config', 'stale-values', 'stale-order', 'unsaved-values', 'unsaved-alias',
         'same-version-reopen', 'selected-scope', 'cache-owned-custom', 'cache-refusals',
         'cancel', 'during-render-change', 'custom-output-rotation']
selected_cases = set(filter(None, os.environ.get('WF_RENDER_PROFILE_CASES', '').split(',')))
if selected_cases:
    if not selected_cases.issubset(CASES): raise SystemExit('unknown WF_RENDER_PROFILE_CASES selection')
    CASES = [name for name in CASES if name in selected_cases]

if len(sys.argv) == 5:
    out = pathlib.Path(sys.argv[2]).resolve(); out.mkdir(parents=True, exist_ok=True)
    rows, runs = [], []
    for label, tool in zip(['helm3', 'helm4'], sys.argv[3:]):
        command = [sys.executable, __file__, sys.argv[1], str(out / label), tool]
        result = subprocess.run(command, capture_output=True, timeout=180)
        (out / (label + '.stdout')).write_bytes(result.stdout); (out / (label + '.stderr')).write_bytes(result.stderr)
        path = out / label / 'results.json'
        child = json.loads(path.read_bytes()) if path.exists() else []
        rows.extend(dict(row, case=label + '/' + row['case']) for row in child)
        runs.append({'command': command, 'exitCode': result.returncode, 'cases': len(child)})
    sha = lambda path: hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
    (out / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')
    (out / 'manifest.json').write_text(json.dumps({'command': sys.argv, 'selectedCases': CASES, 'pythonVersion': sys.version,
        'runs': runs, 'binarySha256': sha(sys.argv[1]), 'testSha256': sha(__file__),
        'helmSha256': {label: sha(tool) for label, tool in zip(['helm3', 'helm4'], sys.argv[3:])}}, indent=2) + '\n')
    raise SystemExit(0 if len(rows) == 2 * len(CASES) and all(r['pass'] for r in rows) and all(r['exitCode'] == 0 for r in runs) else 1)

HELPER = HERE / 'wf-audit-gaps.py'
spec = importlib.util.spec_from_file_location('wire', HELPER)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
HELM = pathlib.Path(sys.argv[3]).resolve()
TEMPLATE = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  name: {{ printf "%s-%s" .Release.Name .Values.environment }}
  namespace: {{ .Release.Namespace }}
  labels:
    capability: {{ .Capabilities.APIVersions.Has "example.com/v1" | quote }}
    kube: {{ .Capabilities.KubeVersion.Version | quote }}
spec:
  entrypoint: main
  templates:
    - name: main
      container: {image: {{ .Values.image | quote }}}
'''
FILES = {'charts/demo/Chart.yaml': 'apiVersion: v2\nname: profile-fixture\nversion: 0.1.0\n',
         'charts/demo/values.yaml': 'environment: default\nimage: alpine\n',
         'charts/demo/templates/workflow.yaml': TEMPLATE,
         'env/common.yaml': 'environment: base\nimage: busybox\n',
         'env/dev.yaml': 'environment: dev\nimage: nginx:dev\n',
         'env/prod.yaml': 'environment: prod\nimage: nginx:prod\n'}
PROFILES = {name: {'chart': 'charts/demo', 'values': values, 'releaseName': 'acceptance',
                  'namespace': 'testing', 'kubeVersion': '1.31.0', 'apiVersions': ['example.com/v1']}
            for name, values in [('dev', ['env/common.yaml', 'env/dev.yaml']),
                                 ('prod', ['env/common.yaml', 'env/prod.yaml']),
                                 ('reverse', ['env/dev.yaml', 'env/common.yaml'])]}
CONFIG = {'schemaVersion': 2, 'targetVersion': 'auto', 'renderProfiles': PROFILES}


def encode(value): return json.dumps(value, indent=2) + '\n'
def parsed(raw):
    try: return json.loads(raw)
    except (ValueError, TypeError): return {}


def setup(name, config=None):
    dest, work = m.setup(name, FILES, encode(CONFIG if config is None else config))
    helpers = dest / 'helpers'; helpers.mkdir(exist_ok=True)
    log = dest / 'helm-invocations.jsonl'; tool = helpers / 'helm'
    tool.write_text('#!/usr/bin/env python3\nimport os,sys,json,pathlib\np=pathlib.Path(' + repr(str(log)) + ')\nwith p.open("a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\nos.execv(' + repr(str(HELM)) + ',[' + repr(str(HELM)) + ']+sys.argv[1:])\n')
    tool.chmod(0o755)
    env = os.environ | {'PATH': str(helpers) + os.pathsep + os.environ['PATH'], 'XDG_CACHE_HOME': str(dest / 'cache')}
    return dest, work, env, log, tool


def command(dest, work, env, label, profile=None, output=None, extra=()):
    argv = [m.BINARY, 'render-check', '--root', work, '--file', work / 'charts/demo/templates/workflow.yaml']
    if profile is not None: argv += ['--profile', profile]
    if output is not None: argv += ['--output', output]
    argv += list(extra)
    return m.command(dest, label, argv, env=env)


def cli_case(name):
    cfg = {'schemaVersion': 1} if name == 'default' else CONFIG
    dest, work, env, log, tool = setup(name, cfg)
    generated = dest / 'generated'
    before = {p: m.sha(work / p) for p in FILES}
    p = command(dest, work, env, 'render', None if name == 'default' else name, generated)
    report = parsed((generated / 'render-report.json').read_bytes()) if (generated / 'render-report.json').exists() else {}
    raw = (generated / 'rendered.yaml').read_text() if (generated / 'rendered.yaml').exists() else ''
    expected = {'default': 'argo-ls-default', 'dev': 'acceptance-dev', 'prod': 'acceptance-prod', 'reverse': 'acceptance-base'}[name]
    expected_values = [] if name == 'default' else PROFILES[name]['values']
    checks = {'success': p.returncode == 0 and parsed(p.stdout).get('analysisComplete') is True,
              'independent_output': 'name: ' + expected + '\n' in raw and ('namespace: default\n' if name == 'default' else 'namespace: testing\n') in raw,
              'source_unchanged': before == {p: m.sha(work / p) for p in FILES}}
    if name != 'default':
        checks.update({'ordered_provenance': report.get('profile') == name and report.get('values') == [{'path': str(work / p), 'sha256': m.sha(work / p)} for p in expected_values],
                       'helm_identity': report.get('helmIdentity', {}).get('sha256') == m.sha(tool) and report.get('helmIdentity', {}).get('path') == str(tool),
                       'capabilities': 'capability: "true"' in raw and 'kube: "v1.31.0"' in raw,
                       'argv_order': [report['command'][i + 1] for i, value in enumerate(report.get('command', [])[:-1]) if value == '--values'] == [str(work / p) for p in expected_values]})
    return checks, {'stdout': parsed(p.stdout), 'report': report, 'expectedName': expected}


def strict_config():
    dest, work, env, log, _ = setup('strict-config')
    configs = {'v1-empty': {'schemaVersion': 1, 'renderProfiles': {}}, 'v1-null': {'schemaVersion': 1, 'renderProfiles': None},
               'unknown-root': CONFIG | {'misspelled': True}, 'unknown-profile': CONFIG | {'renderProfiles': {'bad': PROFILES['dev'] | {'shell': 'echo forbidden'}}},
               'flag-release': CONFIG | {'renderProfiles': {'bad': PROFILES['dev'] | {'releaseName': '--post-renderer=anything'}}},
               'escape-chart': CONFIG | {'renderProfiles': {'bad': PROFILES['dev'] | {'chart': '../other'}}},
               'escape-values': CONFIG | {'renderProfiles': {'bad': PROFILES['dev'] | {'values': ['../private.yaml']}}}}
    for name in ['', '-bad', '.bad', '_bad', 'two words', 'x' * 65, '环境', 'line\nname']:
        configs['invalid-name-' + str(len(configs))] = CONFIG | {'renderProfiles': {name: PROFILES['dev']}}
    observed = {}
    for label, config in configs.items():
        (work / '.argo-workflows-ls.yaml').write_text(encode(config))
        p = m.command(dest, label, [m.BINARY, 'explain-config', '--root', work], env=env)
        observed[label] = p.returncode
    (work / '.argo-workflows-ls.yaml').write_text('schemaVersion: 2\nrenderProfiles:\n  dev: {chart: charts/demo}\n  dev: {chart: charts/demo}\n')
    duplicate = m.command(dest, 'duplicate', [m.BINARY, 'explain-config', '--root', work], env=env)
    (work / '.argo-workflows-ls.yaml').write_text(encode(CONFIG))
    valid = m.command(dest, 'valid-v2', [m.BINARY, 'explain-config', '--root', work], env=env)
    portable = CONFIG | {'renderProfiles': {name: PROFILES['dev'] for name in ['a', '0', 'dev.prod-1_A', 'a' * 64]}}
    (work / '.argo-workflows-ls.yaml').write_text(encode(portable))
    portable_valid = m.command(dest, 'portable-profile-names', [m.BINARY, 'explain-config', '--root', work], env=env)
    identity = parsed(m.command(dest, 'identity', [m.BINARY, 'version', '--json']).stdout)
    return {'invalid_rejected': all(code == 2 for code in observed.values()) and duplicate.returncode == 2,
            'v2_supported': valid.returncode == 0 and parsed(valid.stdout).get('configuration', {}).get('renderProfiles') == PROFILES and portable_valid.returncode == 0,
            'identity_contract': identity.get('configSchemaVersions') == [1, 2],
            'no_helm': not log.exists()}, observed


def conflicts():
    dest, work, env, log, _ = setup('conflicts')
    flags = [['--chart', str(work / 'charts/demo')], ['--values', str(work / 'env/dev.yaml')], ['--release-name', 'acceptance'],
             ['--namespace', 'testing'], ['--kube-version', '1.31.0'], ['--api-version', 'example.com/v1']]
    seen = [command(dest, work, env, 'conflict-' + str(i), 'dev', dest / ('out-' + str(i)), args).returncode for i, args in enumerate(flags)]
    return {'overrides_rejected': all(code == 2 for code in seen), 'no_helm': not log.exists()}, {'exitCodes': seen}


class Client(m.LSP):
    def send(self, method, params=None, ident=None):
        if method == 'initialize': params['capabilities']['window'] = {'showDocument': {'support': True}}
        return super().send(method, params, ident)
    def respond(self, ident, result):
        value = {'jsonrpc': '2.0', 'id': ident, 'result': result}; self.sent.append(value)
        data = json.dumps(value).encode(); self.p.stdin.write(f'Content-Length: {len(data)}\r\n\r\n'.encode() + data); self.p.stdin.flush()


def actions(client, file):
    response = client.request('textDocument/codeAction', {'textDocument': {'uri': file.as_uri()},
        'range': {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 0}}, 'context': {'diagnostics': []}})
    return [a for a in response.get('result') or [] if a.get('command', {}).get('command') == 'argo-workflows.renderCheck']


def selected(items, profile):
    return next((a for a in items if a.get('command', {}).get('arguments', [{}])[0].get('profile') == profile), {}) if all(isinstance(a.get('command', {}).get('arguments', [None])[0], dict) for a in items) else {}


def default_host_response():
    dest, work, env, log, _ = setup('default-host-response', {'schemaVersion': 1})
    old_env = dict(os.environ); os.environ.update(env); c = None; details = {}; assertions = {'no_helm_until_execute': False, 'shown_generated': False, 'response_then_request': False, 'natural_exit': False}
    try:
        c = Client(dest, work); file = work / 'charts/demo/templates/workflow.yaml'; c.open(file, TEMPLATE, 1, language_id='helm')
        offered = actions(c, file); action = offered[0] if offered else {}
        resolved = c.request('codeAction/resolve', action) if action else {}
        assertions['no_helm_until_execute'] = bool(action) and resolved.get('result') == action and not log.exists()
        response = c.request('workspace/executeCommand', action['command']) if action else {}
        shows = [e for e in c.received if e.get('method') == 'window/showDocument']
        if not shows and 'result' in response: shows = [c.wait(lambda e: e.get('method') == 'window/showDocument')]
        assertions['shown_generated'] = bool(shows) and bool(response.get('result', {}).get('generatedFile'))
        details.update({'action': action, 'response': response, 'shows': shows})
        if shows:
            c.respond(shows[-1]['id'], {'success': True})
            response = c.request('workspace/symbol', {'query': 'definitely-absent'})
            details['afterHostResponse'] = response; assertions['response_then_request'] = 'result' in response
    except Exception as error: details['error'] = repr(error)
    finally:
        if c:
            try: c.close(); assertions['natural_exit'] = c.p.returncode == 0
            except Exception as error: details['shutdownError'] = repr(error)
        os.environ.clear(); os.environ.update(old_env)
    return assertions, details


def editor_case(name):
    dest, work, env, log, _ = setup(name)
    old_env = dict(os.environ); os.environ.update(env)
    c = None
    try:
        c = Client(dest, work); file = work / 'charts/demo/templates/workflow.yaml'; c.open(file, TEMPLATE, 1, language_id='helm')
        dirty = work / 'env/dev.yaml'
        if name == 'unsaved-alias':
            alias = work / 'env/alias.yaml'; alias.symlink_to('dev.yaml'); dirty = alias
        if name in ('unsaved-values', 'unsaved-alias'): c.open(dirty, 'environment: unsaved\nimage: alpine\n', 1)
        offered = actions(c, file); action = selected(offered, 'dev')
        if not action: return {'profile_action': False}, {'actions': offered}
        resolved = c.request('codeAction/resolve', action)
        if name == 'editor':
            no_execution = not log.exists()
            response = c.request('workspace/executeCommand', resolved.get('result', action)['command'])
            shows = [m for m in c.received if m.get('method') == 'window/showDocument']
            if not shows and 'result' in response: shows = [c.wait(lambda m: m.get('method') == 'window/showDocument')]
            next_response = {}
            if shows:
                c.respond(shows[-1]['id'], {'success': True})
                next_response = c.request('workspace/symbol', {'query': 'definitely-absent'})
            generated = pathlib.Path(response.get('result', {}).get('generatedFile', '/missing'))
            return {'profile_action': len(offered) == 4 and bool(selected(offered, 'prod')) and bool(selected(offered, 'reverse')),
                    'list_resolve_no_helm': no_execution and resolved.get('result') == action,
                    'explicit_output': generated.is_file() and 'name: acceptance-dev\n' in generated.read_text(),
                    'show_document_response': bool(shows) and 'result' in next_response,
                    'source_unchanged': file.read_text() == TEMPLATE}, {'offered': offered, 'response': response, 'next': next_response}
        if name == 'stale-config':
            cfg = copy.deepcopy(CONFIG); cfg['renderProfiles']['dev']['namespace'] = 'changed'; (work / '.argo-workflows-ls.yaml').write_text(encode(cfg))
        elif name == 'stale-values': (work / 'env/dev.yaml').write_text('environment: changed\nimage: alpine\n')
        elif name == 'stale-order':
            cfg = copy.deepcopy(CONFIG); cfg['renderProfiles']['dev']['values'].reverse(); (work / '.argo-workflows-ls.yaml').write_text(encode(cfg))
        elif name == 'same-version-reopen':
            c.send('textDocument/didClose', {'textDocument': {'uri': file.as_uri()}}); c.open(file, TEMPLATE, 1, language_id='helm')
        response = c.request('workspace/executeCommand', action['command'])
        if name == 'unsaved-values': c.request('workspace/symbol', {'query': 'wire-order-barrier'})
        checks = {'profile_action': True, 'refused': response.get('error', {}).get('code') == -32801,
                  'no_helm': not log.exists(), 'no_generated_view': not any(m.get('method') == 'window/showDocument' for m in c.received)}
        if name == 'unsaved-values':
            checks['actionable_refusal_message'] = any(e.get('method') == 'window/showMessage' and
                'save all chart buffers' in e.get('params', {}).get('message', '').lower() and str(dirty) in e.get('params', {}).get('message', '') for e in c.received)
        if name in ('unsaved-values', 'unsaved-alias'):
            dirty.write_text('environment: unsaved\nimage: alpine\n')
            c.send('textDocument/didSave', {'textDocument': {'uri': dirty.as_uri()}})
        fresh = selected(actions(c, file), 'dev')
        recovery = c.request('workspace/executeCommand', fresh['command']) if fresh else {}
        recovered_file = pathlib.Path(recovery.get('result', {}).get('generatedFile', '/missing'))
        checks['fresh_action_recovers'] = not recovery.get('error') and recovered_file.is_file()
        return checks, {'refusal': response, 'recovery': recovery}
    finally:
        if c: c.close()
        os.environ.clear(); os.environ.update(old_env)


def selected_scope():
    cfg = copy.deepcopy(CONFIG); cfg['scopes'] = [{'name': 'selected', 'include': ['charts/demo/**'], 'targetVersion': '3.6.0'}, {'name': 'unrelated', 'include': ['other/**'], 'targetVersion': '4.1.0'}]
    dest, work, env, log, _ = setup('selected-scope', cfg)
    p = command(dest, work, env, 'matching', 'dev', dest / 'generated')
    report = parsed((dest / 'generated/render-report.json').read_bytes()) if (dest / 'generated/render-report.json').exists() else {}
    before = log.read_bytes() if log.exists() else b''
    bad = command(dest, work, env, 'borrowed', 'dev', dest / 'other', ['--scope', 'unrelated'])
    no_scope_borrowing = bad.returncode == 2 and (log.read_bytes() if log.exists() else b'') == before
    implicit = m.command(dest, 'profile-chart-scope', [m.BINARY, 'render-check', '--root', work, '--profile', 'dev', '--output', dest / 'without-file'], env=env)
    implicit_report = parsed((dest / 'without-file/render-report.json').read_bytes()) if (dest / 'without-file/render-report.json').exists() else {}
    before_implicit = log.read_bytes() if log.exists() else b''
    implicit_bad = m.command(dest, 'profile-chart-borrowed', [m.BINARY, 'render-check', '--root', work, '--profile', 'dev', '--scope', 'unrelated', '--output', dest / 'without-file-other'], env=env)
    return {'matching_scope': p.returncode == 0 and report.get('targetVersion') == '3.6.0',
            'no_scope_borrowing': no_scope_borrowing,
            'profile_chart_scope': implicit.returncode == 0 and implicit_report.get('scope') == 'selected' and implicit_report.get('targetVersion') == '3.6.0',
            'no_chart_scope_borrowing': implicit_bad.returncode == 2 and (log.read_bytes() if log.exists() else b'') == before_implicit}, report


def owned_output(dest, work, env, label):
    p = command(dest, work, env, label, 'dev')
    reports = list((pathlib.Path(env['XDG_CACHE_HOME']) / 'argo-workflows-ls/render').glob('chart-*/render-report.json'))
    report = max(reports, key=lambda p: p.stat().st_mtime_ns) if reports else None
    return p, report.parent if report else dest / 'missing'


def cleanup(dest, env, path, label): return m.command(dest, label, [m.BINARY, 'render-cache', '--remove', path], env=env)


def cache_owned_custom():
    dest, work, env, _, _ = setup('cache-owned-custom'); p, owned = owned_output(dest, work, env, 'owned')
    listed = m.command(dest, 'list', [m.BINARY, 'render-cache'], env=env)
    custom = pathlib.Path(env['XDG_CACHE_HOME']) / 'argo-workflows-ls/render/chart-custom'
    custom_result = command(dest, work, env, 'custom', 'dev', custom)
    reject = cleanup(dest, env, custom, 'reject-custom'); removed = cleanup(dest, env, owned, 'remove-owned')
    source_output = work / 'charts/demo/empty-output'; source_output.mkdir()
    output_alias = dest / 'output-alias'; output_alias.symlink_to(source_output, target_is_directory=True)
    alias_result = command(dest, work, env, 'output-alias', 'dev', output_alias)
    return {'created_owned': p.returncode == 0 and listed.returncode == 0 and any(x.get('directory') == str(owned) and x.get('owned') is True for x in parsed(listed.stdout).get('entries', [])),
            'custom_preserved': custom_result.returncode == 0 and reject.returncode == 2 and (custom / 'rendered.yaml').exists(),
            'owned_removed': removed.returncode == 0 and parsed(removed.stdout).get('removed') is True and not owned.exists(),
            'output_alias_preserves_source': alias_result.returncode == 2 and list(source_output.iterdir()) == []}, {'list': parsed(listed.stdout), 'remove': parsed(removed.stdout), 'reject': parsed(reject.stdout)}


def cache_refusals():
    dest, work, env, _, _ = setup('cache-refusals'); p, owned = owned_output(dest, work, env, 'owned')
    if p.returncode != 0: return {'owned_fixture': False}, parsed(p.stdout)
    parent = owned.parent; sentinel = dest / 'outside'; sentinel.mkdir(); (sentinel / 'important.txt').write_text('preserve\n')
    variants = {}
    for name in ['edited', 'extra', 'nested-link']:
        result, target = owned_output(dest, work, env, 'owned-' + name)
        if result.returncode != 0: return {'owned_fixture': False}, parsed(result.stdout)
        if name == 'edited': (target / 'rendered.yaml').write_text('user edits\n')
        elif name == 'extra': (target / 'user.txt').write_text('user file\n')
        elif name == 'nested-link':
            outside = sentinel / 'rendered.yaml'; outside.write_bytes((target / 'rendered.yaml').read_bytes())
            (target / 'rendered.yaml').unlink(); (target / 'rendered.yaml').symlink_to(outside)
        variants[name] = target
    copied = parent / 'chart-copied'; shutil.copytree(owned, copied); variants['copied-marker'] = copied
    link = parent / 'chart-linked'; link.symlink_to(sentinel, target_is_directory=True); variants['root-link'] = link
    foreign = parent / 'chart-foreign'; foreign.mkdir(); (foreign / 'user.txt').write_text('user file\n'); variants['foreign'] = foreign
    responses = {name: cleanup(dest, env, target, 'reject-' + name).returncode for name, target in variants.items()}
    return {'all_refused': all(code == 2 for code in responses.values()),
            'outside_preserved': (sentinel / 'important.txt').read_text() == 'preserve\n',
            'all_inputs_preserved': all(p.exists() for p in variants.values()) and (variants['edited'] / 'rendered.yaml').read_text() == 'user edits\n'}, responses


def live(pid):
    try: return pathlib.Path('/proc', str(pid), 'stat').read_text().split()[2] != 'Z'
    except FileNotFoundError: return False


def custom_output_rotation():
    dest, work, env, log, tool = setup('custom-output-rotation'); marker = dest / 'started'; release = dest / 'release'
    tool.write_text('#!/usr/bin/env python3\nimport os,sys,pathlib,time\nif sys.argv[1]=="template":\n pathlib.Path(' + repr(str(marker)) + ').write_text("started")\n while not pathlib.Path(' + repr(str(release)) + ').exists():time.sleep(.02)\nos.execv(' + repr(str(HELM)) + ',[' + repr(str(HELM)) + ']+sys.argv[1:])\n'); tool.chmod(0o755)
    output = dest / 'generated'; detached = dest / 'detached-output'
    before = {name: m.sha(work / name) for name in FILES}
    argv = [str(m.BINARY), 'render-check', '--root', str(work), '--file', str(work / 'charts/demo/templates/workflow.yaml'), '--profile', 'dev', '--output', str(output)]
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        deadline = time.monotonic() + 8
        while not marker.exists() and time.monotonic() < deadline: time.sleep(.02)
        assert marker.exists() and output.is_dir(), 'render did not reach held tool'
        output.rename(detached); output.symlink_to(work / 'charts/demo', target_is_directory=True); release.write_text('go')
        stdout, stderr = process.communicate(timeout=15)
        (dest / 'render.stdout').write_bytes(stdout); (dest / 'render.stderr').write_bytes(stderr)
        m.save(dest / 'render.command.json', {'argv': argv, 'exitCode': process.returncode})
        report = parsed(stdout)
        return {'refused': process.returncode == 2 and bool(report.get('error')),
                'source_unchanged': before == {name: m.sha(work / name) for name in FILES} and not any((work / 'charts/demo' / name).exists() for name in ['rendered.yaml', 'render-report.json', 'helm.stdout', 'helm.stderr']),
                'no_redirected_writes': list(detached.iterdir()) == [],
                'raw_failure_retained': 'kind: Workflow' in report.get('stdout', '')}, report
    finally:
        if process.poll() is None: process.kill(); process.communicate()


def held_render(name):
    dest, work, env, log, tool = setup(name); marker = dest / 'started.json'; release = dest / 'release'
    tool.write_text('#!/usr/bin/env python3\nimport os,sys,pathlib,subprocess,json,time\nif sys.argv[1]=="version":os.execv(' + repr(str(HELM)) + ',[' + repr(str(HELM)) + ']+sys.argv[1:])\nchild=subprocess.Popen(["/usr/bin/sleep","600"])\np=pathlib.Path(' + repr(str(marker)) + ');q=p.with_suffix(".tmp");q.write_text(json.dumps({"helm":os.getpid(),"child":child.pid,"group":os.getpgrp()}));q.replace(p)\nprint("partial held output",flush=True)\nwhile not pathlib.Path(' + repr(str(release)) + ').exists():time.sleep(.02)\nchild.terminate();child.wait()\nos.execv(' + repr(str(HELM)) + ',[' + repr(str(HELM)) + ']+sys.argv[1:])\n'); tool.chmod(0o755)
    old_env = dict(os.environ); os.environ.update(env); c = None; pids = {}
    try:
        c = Client(dest, work); file = work / 'charts/demo/templates/workflow.yaml'; c.open(file, TEMPLATE, 1, language_id='helm')
        action = selected(actions(c, file), 'dev')
        if not action: return {'profile_action': False}, {}
        c.send('workspace/executeCommand', action['command'], 900)
        deadline = time.monotonic() + 8
        while not marker.exists() and time.monotonic() < deadline: time.sleep(.02)
        assert marker.exists(), 'Helm did not start'; pids = parsed(marker.read_bytes())
        if name == 'cancel': c.send('$/cancelRequest', {'id': 900})
        else:
            (work / 'env/dev.yaml').write_text('environment: changed\nimage: alpine\n'); release.write_text('go\n')
        response = c.wait(lambda event: event.get('id') == 900)
        deadline = time.monotonic() + 3
        while any(live(pids[k]) for k in ['helm', 'child']) and time.monotonic() < deadline: time.sleep(.02)
        c.request('workspace/symbol', {'query': 'none'})
        reports = list((pathlib.Path(env['XDG_CACHE_HOME']) / 'argo-workflows-ls/render').glob('chart-*/render-report.json'))
        raw_retained = bool(reports) and any((r.parent / 'helm.stdout').exists() and b'partial held output' in (r.parent / 'helm.stdout').read_bytes() for r in reports)
        listed = parsed(m.command(dest, 'failed-cache-list', [m.BINARY, 'render-cache'], env=env).stdout)
        owned = bool(reports) and all(any(e.get('directory') == str(r.parent) and e.get('owned') is True for e in listed.get('entries', [])) for r in reports)
        removals = []
        for i, report in enumerate(reports):
            shutil.copytree(report.parent, dest / ('retained-failed-cache-' + str(i)))
            removals.append(cleanup(dest, env, report.parent, 'remove-failed-' + str(i)).returncode == 0 and not report.parent.exists())
        return {'profile_action': True, 'refused': response.get('error', {}).get('code') == (-32800 if name == 'cancel' else -32801),
                'descendants_reaped': not any(live(pids[k]) for k in ['helm', 'child']), 'raw_retained': raw_retained,
                'failed_cache_owned': owned, 'failed_cache_removable': bool(removals) and all(removals),
                'no_result_view': not any(e.get('method') in ['window/showMessage', 'window/showDocument'] for e in c.received)}, {'response': response, 'pids': pids}
    finally:
        if pids and any(live(pids[k]) for k in ['helm', 'child']):
            try: os.killpg(pids['group'], signal.SIGKILL)
            except ProcessLookupError: pass
        if c: c.close()
        os.environ.clear(); os.environ.update(old_env)


RUNNERS = {name: (lambda name=name: cli_case(name)) for name in ['default', 'dev', 'prod', 'reverse']}
RUNNERS.update({name: (lambda name=name: editor_case(name)) for name in ['editor', 'stale-config', 'stale-values', 'stale-order', 'unsaved-values', 'unsaved-alias', 'same-version-reopen']})
RUNNERS.update({'strict-config': strict_config, 'conflicts': conflicts, 'selected-scope': selected_scope,
                'default-host-response': default_host_response,
                'cache-owned-custom': cache_owned_custom, 'cache-refusals': cache_refusals, 'custom-output-rotation': custom_output_rotation,
                'cancel': lambda: held_render('cancel'), 'during-render-change': lambda: held_render('during-render-change')})
for name in CASES:
    try:
        assertions, details = RUNNERS[name](); m.result(name, assertions, details)
    except Exception as error: m.result(name, {'completed': False}, {'error': repr(error), 'traceback': traceback.format_exc()})
m.save(m.OUT / 'manifest.json', {'command': sys.argv, 'selectedCases': CASES, 'pythonVersion': sys.version,
    'binarySha256': m.sha(m.BINARY), 'testSha256': m.sha(pathlib.Path(__file__)), 'helperSha256': m.sha(HELPER), 'helmSha256': m.sha(HELM),
    'expectedCases': CASES, 'inputs': {str(p.relative_to(m.OUT)): m.sha(p) for p in m.OUT.rglob('*') if p.is_file() and '/input/' in str(p)}})
raise SystemExit(0 if len(m.RESULTS) == len(CASES) and all(r['pass'] for r in m.RESULTS) else 1)
