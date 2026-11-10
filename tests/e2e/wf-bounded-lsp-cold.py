#!/usr/bin/env python3
"""Five resumable cold indexes; fast partial queries and all 20,000 final symbols."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]


def evaluate_response(response, elapsed):
    """Keep protocol failures distinct from incomplete or slow symbol results."""
    symbols = response.get("result")
    is_symbol_list = isinstance(symbols, list)
    by_file = {}
    if is_symbol_list:
        for symbol in symbols:
            by_file.setdefault(symbol["location"]["uri"], []).append(symbol["name"])
    checks = {
        "noProtocolError": "error" not in response,
        "symbolListReturned": is_symbol_list,
        "allFilesIndexed": len(by_file) == 10000,
        "allDeclarations": (
            is_symbol_list and len(symbols) == 20000
            and all(sorted(names) == ["main", "unused"] for names in by_file.values())
        ),
        "withinFiveSeconds": elapsed <= 5,
    }
    return {
        "protocolError": response.get("error"),
        "observedFiles": len(by_file),
        "observedDeclarations": len(symbols) if is_symbol_list else None,
        "assertions": checks,
    }


def main():
    binary, work, out = (Path(arg).resolve() for arg in sys.argv[1:4])
    out.mkdir(parents=True, exist_ok=True)
    cgpath = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    cg = Path("/sys/fs/cgroup") / cgpath.lstrip("/")
    quota, period = (cg / "cpu.max").read_text().split()
    assert quota != "max" and int(quota) / int(period) <= 4
    assert len(os.sched_getaffinity(0)) == 4
    assert int((cg / "memory.max").read_text()) <= 8 * 1024 ** 3
    assert (cg / "memory.swap.max").read_text().strip() == "0"

    sys.argv = [sys.argv[0], str(binary), str(out)]
    spec = importlib.util.spec_from_file_location("client", ROOT / "tests/e2e/wf-audit-gaps.py")
    client_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client_module)
    rows = []
    for sample in range(5):
        folder = out / str(sample)
        folder.mkdir(exist_ok=True)
        client = None
        row = {"sample": sample, "pass": False}
        start = time.monotonic()
        try:
            client = client_module.LSP(folder, work)
            initialized = time.monotonic()
            first = client.request("workspace/symbol", {"query": ""})
            first_received = time.monotonic()
            deadline = start + 60
            ready = None
            while time.monotonic() < deadline and ready is None:
                for line in bytes(client.stderr_raw).splitlines():
                    try:
                        state = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if state.get("phase") == "index" and state.get("state") == "Ready" and state.get("root") == str(work):
                        ready = state
                if ready is None:
                    time.sleep(.02)
            ready_at = time.monotonic()
            response = client.request("workspace/symbol", {"query": ""})
            received = time.monotonic()
            row.update({"seconds": received - start, "startupSeconds": initialized - start,
                        "firstQuerySeconds": first_received - initialized,
                        "readySeconds": ready_at - start, "querySeconds": received - ready_at,
                        "firstQuery": first, "ready": ready})
            row.update(evaluate_response(response, received - ready_at))
            row["assertions"].update({
                "initialQueryIsResponsive": first_received - initialized <= 5 and isinstance(first.get("result"), list),
                "discoveryCompletesWithinSixtySeconds": ready is not None and ready_at - start <= 60,
                "completeDiscoveryCount": ready is not None and ready.get("files") == 10000,
            })
        except Exception as error:
            row.update({"seconds": time.monotonic() - start, "error": repr(error),
                        "assertions": {"completed": False}})
        finally:
            if client is not None:
                try:
                    client.close()
                    row["assertions"]["cleanShutdown"] = True
                except Exception as error:
                    row["shutdownError"] = repr(error)
                    row["assertions"]["cleanShutdown"] = False
            row["pass"] = bool(row["assertions"]) and all(row["assertions"].values())
            rows.append(row)
            client_module.save(out / "results.json", rows)

    client_module.save(out / "manifest.json", {
        "binarySha256": client_module.sha(binary),
        "testSha256": client_module.sha(Path(__file__)),
        "helperSha256": client_module.sha(ROOT / "tests/e2e/wf-audit-gaps.py"),
        "workloadManifestSha256": client_module.sha(work / "workload-manifest.json"),
        "cgroup": cgpath,
        "cpu.max": (cg / "cpu.max").read_text().strip(),
        "memory.max": (cg / "memory.max").read_text().strip(),
        "memory.swap.max": (cg / "memory.swap.max").read_text().strip(),
        "affinity": sorted(os.sched_getaffinity(0)),
        "timingBoundary": "First workspace/symbol may return known candidates. Ready must observe all 10,000 files within 60s; the full 20,000-symbol query then completes within 5s. Shutdown excluded.",
    })
    return 0 if len(rows) == 5 and all(row["pass"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
