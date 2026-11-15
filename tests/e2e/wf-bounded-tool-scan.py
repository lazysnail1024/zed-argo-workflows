#!/usr/bin/env python3
"""Cancel observed input acquisition before either external tool is launched."""
import ctypes
import hashlib
import json
import os
import pathlib
import select
import shutil
import signal
import struct
import subprocess
import sys
import time

binary = pathlib.Path(sys.argv[1]).resolve()
work = pathlib.Path(sys.argv[2]).resolve()
out = pathlib.Path(sys.argv[3]).resolve()
out.mkdir(parents=True, exist_ok=True)
rows = []


def watch_read(path):
    # Observe an actual input close independently of product tracing. Watching
    # a data file, rather than the config/Chart header, proves scanning began.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.inotify_add_watch.restype = ctypes.c_int
    fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if fd < 0:
        raise OSError(ctypes.get_errno(), 'inotify_init1')
    if libc.inotify_add_watch(fd, os.fsencode(path), 0x10) < 0:
        error = ctypes.get_errno()
        os.close(fd)
        raise OSError(error, 'inotify_add_watch')
    return fd


def await_read(fd):
    if not select.select([fd], [], [], 5)[0]:
        return []
    data = os.read(fd, 65536)
    masks, offset = [], 0
    while offset < len(data):
        descriptor, mask, cookie, length = struct.unpack_from('iIII', data, offset)
        offset += 16 + length
        if mask & 0x4000:
            raise RuntimeError('inotify overflow; scan observation is not trustworthy')
        masks.append(mask)
    return masks


inputs = sorted(work.glob('group-*/*.yaml'))
assert len(inputs) == 10000 and sum(path.stat().st_size for path in inputs) == 50 * 1024 * 1024
for tool in ['helm', 'argo']:
    folder = out / tool
    folder.mkdir(exist_ok=True)
    marker, helper = folder / 'launched', folder / 'tool'
    helper.write_text('#!/usr/bin/python3\nimport pathlib,time\npathlib.Path(' + repr(str(marker)) + ').write_text("started")\ntime.sleep(30)\n')
    helper.chmod(0o755)
    if tool == 'helm':
        # RenderRequest scans the chosen chart and ordered values, not unrelated
        # workspace inputs. Keep a legal explicit target in this 100-scope root
        # and put the fixed 50 MiB input workload in the actual chart scan.
        chart = folder / 'chart'
        chart.mkdir(exist_ok=True)
        (chart / 'Chart.yaml').write_text('apiVersion: v2\nname: sample\nversion: 0.1.0\n')
        for path in inputs:
            target = chart / 'inputs' / path.relative_to(work)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        watched = chart / 'inputs' / inputs[0].relative_to(work)
        cmd = [str(binary), 'render-check', '--root', str(work), '--scope', 'group-000',
               '--chart', str(chart), '--helm-binary', str(helper), '--output', str(folder / 'output')]
    else:
        watched = inputs[0]
        cmd = [str(binary), 'lint-official', '--root', str(work), '--argo-binary', str(helper)]
    fd = watch_read(watched)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        masks = await_read(fd)
        start = time.monotonic()
        if p.poll() is None:
            p.send_signal(signal.SIGINT)
        try:
            so, se = p.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            so, se = p.communicate()
        elapsed = time.monotonic() - start
    finally:
        os.close(fd)
    (folder / 'stdout.raw').write_bytes(so)
    (folder / 'stderr.raw').write_bytes(se)
    try:
        report = json.loads(so)
    except ValueError:
        report = {}
    checks = {'scanReadObserved': any(mask & 0x10 for mask in masks),
              'exit3': p.returncode == 3,
              'beforeTool': not marker.exists(),
              'terminalJSON': report.get('analysisComplete') is False and 'cancel' in report.get('error', ''),
              'prompt': elapsed <= .2}
    rows.append({'case': tool, 'command': cmd, 'exitCode': p.returncode,
                 'assertions': checks, 'pass': all(checks.values()),
                 'scanObservation': {'path': str(watched), 'masks': masks,
                                     'inputFiles': len(inputs), 'inputBytes': 50 * 1024 * 1024},
                 'cancelToExitSeconds': elapsed})

(out / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')
(out / 'manifest.json').write_text(json.dumps({
    'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
    'testSha256': hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
    'synchronization': 'Linux IN_CLOSE_NOWRITE on the first real scan input, before SIGINT; no fixed startup sleep.'}, indent=2) + '\n')
sys.exit(0 if all(row['pass'] for row in rows) else 1)
