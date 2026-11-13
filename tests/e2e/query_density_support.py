"""Observe the actual complete-scope precondition before timing fixed queries."""
import json
import time


def _ready_index(client, root, file_count, opened_bytes):
    if client.p.poll() is not None:
        raise RuntimeError('server exited before complete query scope')
    latest = None
    for line in bytes(client.stderr_raw).splitlines():
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if row.get('phase') == 'index' and row.get('root') == str(root):
            latest = row
    if latest is not None and latest.get('state') == 'Limited':
        raise AssertionError(f'query scope was not complete: {latest}')
    if (latest is not None and latest.get('state') == 'Ready'
            and latest.get('files') == file_count and latest.get('queue') == 0
            and latest.get('retainedTextBytes') == opened_bytes):
        return latest
    return None


def wait_for_index_ready(client, root, file_count, opened_bytes, timeout=5):
    """Observe readiness without opening a buffer or issuing a warm-up query."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = _ready_index(client, root, file_count, opened_bytes)
        if ready is not None:
            return ready
        time.sleep(.005)
    raise TimeoutError(f'complete index was not published within {timeout} seconds')


def wait_for_query_ready(client, root, path, text, expected_diagnostics, file_count):
    """Wait for current-open Ready evidence and version-correct diagnostics.

    The first publishDiagnostics may be a valid local publication while the
    catalog is still building. This barrier sends no query and never retries a
    measured request. Ready must include the current open buffer, so an earlier
    initialize-only Ready notification cannot satisfy it.
    """
    wanted_bytes = len(text.encode('utf-8'))
    uri = path.as_uri()
    deadline = time.monotonic() + 5

    def normalize(rows):
        return sorted(json.dumps({key: row[key] for key in ('range', 'code', 'severity', 'source')}, sort_keys=True)
                      for row in rows)

    wanted = normalize(expected_diagnostics)
    while time.monotonic() < deadline:
        ready = _ready_index(client, root, file_count, wanted_bytes)
        if ready is not None:
            latest = None
            for index, message in enumerate(client.received):
                params = message.get('params', {})
                if (message.get('method') == 'textDocument/publishDiagnostics'
                        and params.get('uri') == uri and params.get('version') == 1):
                    latest = index, params
            if latest is not None and normalize(latest[1]['diagnostics']) == wanted:
                index, params = latest
                return params['diagnostics'], {'index': ready, 'diagnosticMessageIndex': index,
                                              'uri': uri, 'version': 1, 'openedUTF8Bytes': wanted_bytes}
        # Poll already emitted notifications; this is not a query retry or a
        # fixed warm-up delay. Missing readiness fails at the bounded deadline.
        time.sleep(.005)
    raise TimeoutError('complete current-open query scope and diagnostics were not published within 5 seconds')
