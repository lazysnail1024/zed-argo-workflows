#!/usr/bin/env python3
"""Real stdio symbol consumers: low matches, cap, cancellation, and live edits."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import statistics
import sys
import time
import traceback


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--files", type=int, default=10001)
args = parser.parse_args()
assert args.files >= 10001, "the acceptance workload must cross the 20,000 display-symbol cap"
HERE = Path(__file__).resolve().parent
OUT = args.output.resolve()
OUT.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location("wire", HERE / "wf-lifecycle-gaps.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.BINARY, m.OUT = args.binary.resolve(), OUT

TEXT = """apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata: {name: library-NUMBER, namespace: argo}
spec:
  parallelism: 1
  templates:
    - name: main
      inputs:
        parameters:
          - name: PARAMETER
      container: {image: alpine}
"""


def position(text, needle):
    head = text[:text.index(needle)]
    return {"line": head.count("\n"), "character": len(head.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2}


def index_ready(client, work, since=0):
    started = time.monotonic()
    while time.monotonic() - started < 90:
        for line in bytes(client.stderrraw)[since:].splitlines():
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if record.get("state") == "Ready" and record.get("root") == str(work) and record.get("files") == args.files:
                return record
        if client.p.poll() is not None:
            raise RuntimeError("server exited during index discovery")
        time.sleep(.01)
    raise TimeoutError("real symbol fixture did not reach complete discovery")


def meaningful_hover(response):
    return "error" not in response and "parallelism" in json.dumps(response.get("result")).lower()


def run():
    files = {}
    for n in range(args.files):
        parameter = "needle-final" if n == args.files - 1 else "input"
        files["file-%05d.yaml" % n] = TEXT.replace("NUMBER", str(n)).replace("PARAMETER", parameter)
    dest, work = m.setup("symbol-query-work", files)
    client = m.Client(dest, work)
    current = work / "file-00000.yaml"
    current_text = files[current.name]
    try:
        client.open(current, current_text)
        ready = index_ready(client, work)
        started = time.monotonic()
        all_symbols = client.request("workspace/symbol", {"query": ""})
        full_seconds = time.monotonic() - started
        rows = all_symbols.get("result", [])
        expected_files = [(work / ("file-%05d.yaml" % n)).as_uri() for n in range(10000)]
        expected_order = [(uri, name) for uri in expected_files for name in ("main", "input")]
        actual_order = [(row.get("location", {}).get("uri"), row.get("name")) for row in rows]
        late = client.request("workspace/symbol", {"query": "needle-final"})
        absent = client.request("workspace/symbol", {"query": "no-such-display-symbol"})
        m.result("stable-display-symbols", {
            "complete_fixture_indexed": ready.get("files") == args.files,
            "same_first_20000_display_symbols": "error" not in all_symbols and actual_order == expected_order,
            "template_and_parameter_kinds_preserved": {row.get("kind") for row in rows} == {12, 13},
            "low_match_beyond_cap_not_hidden": len(late.get("result", [])) == 1 and late["result"][0]["location"]["uri"] == (work / ("file-%05d.yaml" % (args.files - 1))).as_uri(),
            "zero_match_is_successful_empty_list": "error" not in absent and absent.get("result") == [],
            "full_query_within_five_seconds": full_seconds <= 5,
        }, {"files": args.files, "symbols": len(rows), "fullQuerySeconds": full_seconds,
            "late": late, "zeroMatch": absent})

        # Exercise explicit cancellation without a didChange/epoch abort first.
        cancel_ids = list(range(9000, 9008))
        client.batch([("workspace/symbol", {"query": "no-such-display-symbol"}, ident) for ident in cancel_ids] +
                     [("$/cancelRequest", {"id": ident}, None) for ident in cancel_ids])
        cancel_only = [client.response(ident) for ident in cancel_ids]
        cancel_barrier = client.request("workspace/symbol", {"query": "needle-final"})
        fence_ids = [9100, 9101]
        changed = "# current edit\n" + current_text.replace("name: input", "name: new-parameter")
        client.batch([("workspace/symbol", {"query": ""}, ident) for ident in fence_ids] +
                     [("textDocument/didChange", {"textDocument": {"uri": current.as_uri(), "version": 2},
                       "contentChanges": [{"text": changed}]}, None)])
        fenced = [client.response(ident) for ident in fence_ids]
        client.diagnostics(current, 2)
        timings, cancellation_counts = [], []
        for attempt in range(10):
            ids = list(range(10000 + attempt * 100, 10008 + attempt * 100))
            hover_id = ids[-1] + 1
            changed = "# current edit %d\n" % attempt + current_text.replace("name: input", "name: new-parameter")
            version = attempt + 3
            requests = [("workspace/symbol", {"query": "no-such-display-symbol"}, ident) for ident in ids]
            requests += [("textDocument/didChange", {"textDocument": {"uri": current.as_uri(), "version": version},
                          "contentChanges": [{"text": changed}]}, None),
                         ("textDocument/hover", {"textDocument": {"uri": current.as_uri()},
                          "position": position(changed, "parallelism")}, hover_id)]
            requests += [("$/cancelRequest", {"id": ident}, None) for ident in ids]
            started = time.monotonic()
            client.batch(requests)
            hover = client.response(hover_id)
            timings.append(time.monotonic() - started)
            terminals = [client.response(ident) for ident in ids]
            cancellation_counts.append(sum(row.get("error", {}).get("code") in (-32800, -32801) for row in terminals))
            assert meaningful_hover(hover), "current hover failed during a symbol-query cancellation burst"
            assert all(row.get("result") == [] or row.get("error", {}).get("code") in (-32800, -32801, -32803) for row in terminals), terminals
            client.diagnostics(current, version)
        # A heavy-lane barrier drains real canceled workers, not only their
        # immediate cancellation replies. Fresh queries must still work.
        recovered = client.request("workspace/symbol", {"query": "needle-final"})
        selected = client.request("textDocument/documentSymbol", {"textDocument": {"uri": current.as_uri()}})
        current_parameters = [row for row in selected.get("result", []) if row.get("name") == "new-parameter"]
        expected_position = position(changed, "new-parameter")
        deadline = time.monotonic() + 5
        while True:
            workspace_parameters = client.request("workspace/symbol", {"query": "new-parameter"})
            if workspace_parameters.get("result") or time.monotonic() >= deadline:
                break
            time.sleep(.01)
        all_ids = cancel_ids + fence_ids + [10000 + attempt * 100 + n for attempt in range(10) for n in range(8)]
        terminals = [row["message"] for row in client.received]
        p95 = sorted(timings)[math.ceil(.95 * len(timings)) - 1]
        m.result("cancellation-and-current-input", {
            "explicit_cancellation_without_input_change": any(row.get("error", {}).get("code") == -32800 for row in cancel_only),
            "explicit_cancel_lane_recovers": len(cancel_barrier.get("result", [])) == 1,
            "active_and_queued_old_positions_refused": all(row.get("error", {}).get("code") == -32801 for row in fenced),
            "cancellation_path_exercised": all(count > 0 for count in cancellation_counts),
            "one_terminal_per_query": all(sum(row.get("id") == ident for row in terminals) == 1 for ident in all_ids),
            "foreground_hover_p95_within_500ms": p95 <= .5,
            "heavy_lane_recovers": len(recovered.get("result", [])) == 1,
            "new_parameter_position_is_current": len(current_parameters) == 1 and current_parameters[0]["selectionRange"]["start"] == expected_position,
            "workspace_parameter_position_is_current": len(workspace_parameters.get("result", [])) == 1 and workspace_parameters["result"][0]["location"]["range"]["start"] == expected_position,
        }, {"hoverSeconds": timings, "p95Seconds": p95, "medianSeconds": statistics.median(timings),
            "quantile": "nearest-rank ceil(0.95 * N); 10 samples",
            "explicitCancelResponses": cancel_only, "canceledByBurst": cancellation_counts, "fenced": fenced,
            "recovered": recovered, "currentParameters": current_parameters, "workspaceParameters": workspace_parameters})
    finally:
        code, elapsed = client.close()
        m.result("clean-shutdown", {"exit_zero": code == 0, "within_five_seconds": elapsed <= 5}, {"exitCode": code, "seconds": elapsed})


try:
    run()
except Exception as error:
    m.result("symbol-query-runner", {"completed": False}, {"error": repr(error), "traceback": traceback.format_exc()})
m.save(OUT / "manifest.json", {"command": sys.argv, "binarySha256": m.sha(m.BINARY),
                               "testSha256": m.sha(Path(__file__)), "helperSha256": m.sha(HERE / "wf-lifecycle-gaps.py"),
                               "files": args.files, "displaySymbols": args.files * 2,
                               "scope": "Native real stdio; resource-cgroup 10000-file/20000-symbol cold-index acceptance remains unchanged"})
raise SystemExit(0 if len(m.RESULTS) == 3 and all(row["pass"] for row in m.RESULTS) else 1)
