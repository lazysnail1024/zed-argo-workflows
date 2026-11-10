#!/usr/bin/env python3
"""Run existing Linux resource consumers under real, recorded cgroup limits."""

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid

from run import ROOT, E2E, command, save, summary, validate_results


def worker(spec_path):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    cgroup = Path("/sys/fs/cgroup") / relative.lstrip("/")
    actual = {name: (cgroup / name).read_text().strip()
              for name in ("memory.max", "memory.swap.max", "cpu.max", "memory.events")}
    actual.update({"cgroup": relative, "affinity": sorted(os.sched_getaffinity(0)),
                   "platform": platform.platform(), "python": sys.version})
    save(Path(spec["output"]) / "limits.json", actual)
    quota, period = actual["cpu.max"].split()
    if actual["memory.max"] != str(spec["memoryMiB"] * 1024 ** 2):
        raise RuntimeError("the requested memory cgroup was not applied")
    if quota == "max" or int(quota) / int(period) > 4 or len(actual["affinity"]) != 4:
        raise RuntimeError("resource gates require four allowed CPUs with at most four CPUs of quota")
    if actual["memory.swap.max"] != "0":
        raise RuntimeError("swap must be disabled for the measured memory budget")
    env = os.environ | spec.get("env", {}) | {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    os.execvpe(spec["command"][0], spec["command"], env)


def result_count(output, filename, single=False, pass_key="pass"):
    if not single:
        return validate_results(output / filename)
    row = json.loads((output / filename).read_text(encoding="utf-8"))
    checks = row.get("assertions")
    if row.get(pass_key) is not True or not checks or not all(value is True for value in checks.values()):
        raise ValueError(f"failed semantic resource assertions: {row}")
    return 1


def execute(name, argv, output, memory, timeout=300, extra_env=None):
    cpus = sorted(os.sched_getaffinity(0))[:4]
    if len(cpus) != 4:
        raise RuntimeError("resource acceptance requires a runner with at least four CPUs")
    spec = {"command": [str(x) for x in argv], "output": str(output),
            "memoryMiB": memory, "env": extra_env or {}}
    spec_path = output.parent / "specs" / (name + ".json")
    save(spec_path, spec)
    unit = "wf-ci-" + uuid.uuid4().hex
    systemd = ["sudo", "-n", "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
               "--unit=" + unit, "--uid=" + str(os.getuid()), "--gid=" + str(os.getgid()),
               "--working-directory=" + str(ROOT), "--property=MemoryMax=" + str(memory * 1024 ** 2),
               "--property=MemorySwapMax=0", "--property=CPUQuota=400%",
               "--property=AllowedCPUs=" + ",".join(str(cpu) for cpu in cpus),
               "--property=RuntimeMaxSec=" + str(timeout), "--property=KillMode=control-group"]
    # setup-python's interpreter can depend on a private libpython directory;
    # this must be available before the worker interpreter starts.
    if os.environ.get("LD_LIBRARY_PATH"):
        systemd += ["--setenv=LD_LIBRARY_PATH=" + os.environ["LD_LIBRARY_PATH"]]
    systemd += [sys.executable, Path(__file__).resolve(), "_worker", spec_path]
    try:
        return command(systemd, output.parent / "logs" / name, timeout=timeout + 30)
    finally:
        # Also reclaim children if the consumer itself crashed or an outer
        # watchdog timed out. A failed systemd startup cannot count as a pass.
        subprocess.run(["sudo", "-n", "systemctl", "stop", unit], capture_output=True, timeout=30, check=False)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "_worker":
        worker(sys.argv[2])
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=("adversarial", "load", "index"), required=True)
    args = parser.parse_args()
    binary, output = args.binary.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if platform.system() != "Linux":
        raise RuntimeError("resource gates require Linux cgroup v2")
    rows = []
    if args.suite == "adversarial":
        cases = [("wf-bounded-aggregate-input", 512, {}, "results.json", True, "pass"),
                 ("wf-bounded-expressions", 512, {}, "results.json", False, "pass"),
                 ("wf-bounded-expression-lsp", 512, {}, "results.json", False, "pass"),
                 ("wf-bounded-process", 512, {}, "results.json", False, "pass"),
                 ("wf-bounded-render-deadline", 512, {}, "results.json", True, "pass"),
                 ("wf-bounded-snapshot-budget", 512, {"WF_BUDGET_CASES": "refusal"}, "results.json", False, "pass"),
                 ("wf-bounded-snapshot-budget", 1024, {"WF_BUDGET_CASES": "exact"}, "results.json", False, "pass"),
                 ("wf-semantic-origin-budget", 512, {}, "results.json", False, "pass")]
        for script, memory, env, evidence, single, key in cases:
            name = script + ("-" + env["WF_BUDGET_CASES"] if env else "")
            destination = output / name
            row = execute(name, [sys.executable, E2E / (script + ".py"), binary, destination],
                          destination, memory, extra_env=env)
            row["name"] = name
            try:
                row["caseCount"] = result_count(destination, evidence, single, key)
            except (OSError, ValueError, TypeError) as error:
                row.update({"pass": False, "evidenceError": str(error)})
                print(f"FAIL semantic evidence {name}: {error}"[:2000], flush=True)
            rows.append(row)
            summary(output, rows, "Linux adversarial cgroup E2E")
    elif args.suite == "index":
        # Each sample has a fresh process and full on-disk fixture. The consumer
        # measures server VmHWM separately from the 8 GiB process-group envelope.
        for count, deadline in ((10000, 60), (50000, 300)):
            for sample in range(5):
                name = f"persistent-{count}-{sample}"
                destination = output / name
                argv = [sys.executable, E2E / "wf-persistent-workspace.py", binary, destination,
                        "--count", str(count), "--padding", "4096", "--resource-every", "1",
                        "--deadline", str(deadline)]
                row = execute(name, argv, destination, 8192, timeout=2 * deadline + 90)
                row["name"] = name
                try:
                    row["caseCount"] = validate_results(destination / "results.json", assertion_key="checks", require_assertions=True)
                except (OSError, ValueError, TypeError) as error:
                    row.update({"pass": False, "evidenceError": str(error)})
                rows.append(row)
                summary(output, rows, "Linux persistent index cgroup E2E")
    else:
        workload = output / "workload"
        row = command([sys.executable, E2E / "wf-bounded-workload.py", workload], output / "logs/workload")
        row["name"] = "workload-generator"
        rows.append(row)
        if not row["pass"]:
            return summary(output, rows, "Linux constrained load E2E")
        for script, evidence, single, key in (
                ("wf-bounded-full", "results.json", False, "pass"),
                ("wf-bounded-cancel", "report.json", True, "passed"),
                ("wf-bounded-lsp-cold", "results.json", False, "pass"),
                ("wf-bounded-lsp-load", "results.json", False, "pass"),
                ("wf-bounded-active-latency", "results.json", False, "pass"),
                ("wf-bounded-tool-scan", "results.json", False, "pass"),
                ("wf-schema-query-load", "results.json", False, "pass"),
                ("wf-domain-external-load", "results.json", False, "pass")):
            destination = output / script
            row = execute(script, [sys.executable, E2E / (script + ".py"), binary, workload, destination],
                          destination, 8192, timeout=600)
            row["name"] = script
            try:
                row["caseCount"] = result_count(destination, evidence, single, key)
            except (OSError, ValueError, TypeError) as error:
                row.update({"pass": False, "evidenceError": str(error)})
                print(f"FAIL semantic evidence {script}: {error}"[:2000], flush=True)
            rows.append(row)
            summary(output, rows, "Linux constrained load E2E")
    return summary(output, rows, "Linux " + args.suite + " cgroup E2E")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_worker":
        worker(sys.argv[2])
    else:
        raise SystemExit(0 if main() else 1)
