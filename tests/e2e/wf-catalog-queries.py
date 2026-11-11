#!/usr/bin/env python3
"""Real stdio consumers for closed-read repair and bounded resource candidates."""
import argparse
import importlib.util
import pathlib
import sys
import time
import traceback

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary", type=pathlib.Path)
parser.add_argument("output", type=pathlib.Path)
parser.add_argument("--cases", nargs="+", choices=("repair", "candidates", "truncation"),
                    default=("repair", "candidates", "truncation"))
args = parser.parse_args()
spec = importlib.util.spec_from_file_location("wire", pathlib.Path(__file__).with_name("wf-audit-gaps.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def ready(client):
    deadline = time.monotonic() + 30
    while b'"state":"Ready"' not in client.stderr_raw:
        if time.monotonic() >= deadline:
            raise TimeoutError("catalog did not finish the bounded fixture")
        time.sleep(.01)


def selection(text, needle, value):
    start = m.pos(text, needle, offset=len(needle) - len(value))
    return {"start": start, "end": dict(start, character=start["character"] + len(value))}


def repair():
    config = m.CONFIG + "defaultNamespace: argo\nscopes:\n  - name: selected\n    include: ['a/**']\n  - name: other\n    include: ['b/**']\n"
    dest, work = m.setup("closed-read-repair", {"a/caller.yaml": m.CALLER, "a/base.yaml": m.BASE,
                                               "b/ordinary.yaml": "ordinary: true\n"}, config)
    client = m.LSP(dest, work)
    caller, library = work / "a/caller.yaml", work / "a/base.yaml"
    try:
        client.open(caller, m.CALLER)
        ready(client)
        params = {"textDocument": {"uri": caller.as_uri()},
                  "position": m.pos(m.CALLER, "template: work", offset=len("template: "))}
        before = client.request("textDocument/definition", params)
        expected_before = [{"uri": library.as_uri(), "range": selection(m.BASE, "- name: work", "work")}]
        moved = "# moved without a watch notification 😀\r\n" + m.BASE
        library.write_bytes(moved.encode())
        started = time.monotonic()
        after = client.request("textDocument/definition", params)
        elapsed = time.monotonic() - started
        expected_after = [{"uri": library.as_uri(), "range": selection(moved, "- name: work", "work")}]
        m.result("closed-read-repair", {
            "baseline_definition_exact": before.get("result") == expected_before,
            "first_query_repairs_position": after.get("result") == expected_after,
            "no_self_content_modified": "error" not in after,
        }, {"before": before, "after": after, "seconds": elapsed})

        # A local query to an unopened file also keeps the actual bytes it read.
        moved_again = "# another move\n" + moved
        library.write_bytes(moved_again.encode())
        symbols = client.request("textDocument/documentSymbol", {"textDocument": {"uri": library.as_uri()}})
        work_symbols = [s for s in symbols.get("result", []) if s.get("name") == "work"]
        m.result("closed-current-file-repair", {
            "first_query_has_fresh_symbol": any(s.get("selectionRange") == selection(moved_again, "- name: work", "work") for s in work_symbols),
            "no_self_content_modified": "error" not in symbols,
        }, symbols)
    finally:
        client.close()


def candidates(truncated=False):
    name = "candidate-matching-budget" if truncated else "candidate-prefix-budget"
    caller = m.CALLER.replace("name: base, template:", "name: bas, template:")
    files = {"z-base.yaml": m.BASE, "caller.yaml": caller}
    for number in range(1001):
        text = m.BASE.replace("name: base,", f"name: other-{number:04d},")
        if not truncated:
            text = text.replace("namespace: argo", "namespace: other")
        files[f"a-unrelated/{number:04d}.yaml"] = text
    dest, work = m.setup(name, files)
    client = m.LSP(dest, work)
    path = work / "caller.yaml"
    try:
        diagnostics = client.open(path, caller)
        ready(client)
        p = m.pos(caller, "name: bas,", offset=len("name: "))
        response = client.request("textDocument/codeAction", {"textDocument": {"uri": path.as_uri()},
                                  "range": {"start": p, "end": p}, "context": {"diagnostics": diagnostics}})
        if truncated:
            m.result(name, {"matching_overflow_explicit": "1000 matching" in response.get("error", {}).get("message", ""),
                            "no_partial_candidate_edit": not response.get("result")}, response)
            return
        resolved = [client.request("codeAction/resolve", action) for action in response.get("result", [])]
        corrected = []
        for result in resolved:
            edit = result.get("result", {}).get("edit")
            if edit:
                corrected.append(m.apply(caller, edit, path.as_uri(), 1))
        expected = caller.replace("name: bas,", 'name: "base",')
        after = client.change(path, expected, 2) if expected in corrected else None
        m.result(name, {"unrelated_namespaces_do_not_hide_candidate": expected in corrected,
                        "no_protocol_error": "error" not in response,
                        "applied_fix_clears_missing_reference": after is not None and not any(d.get("code") == "WF_TEMPLATE_REF_UNRESOLVED" for d in after),
                        "disk_input_unchanged": path.read_text() == caller}, {"response": response, "resolved": resolved})
    finally:
        client.close()


for name, run in [("repair", repair), ("candidates", candidates), ("truncation", lambda: candidates(True))]:
    if name not in args.cases:
        continue
    try:
        run()
    except Exception as error:
        m.result(name + "-runner", {"completed": False}, {"error": repr(error), "traceback": traceback.format_exc()})
m.save(m.OUT / "manifest.json", {"command": sys.argv, "binarySha256": m.sha(m.BINARY),
                                 "testSha256": m.sha(pathlib.Path(__file__)), "cases": args.cases})
sys.exit(0 if m.RESULTS and all(row["pass"] for row in m.RESULTS) else 1)
