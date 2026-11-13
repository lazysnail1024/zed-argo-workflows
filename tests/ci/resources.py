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

from run import ROOT, E2E, command, save, summary, validate_results, verify_consumer_identity, verify_result_contract


def limits_snapshot(cgroup, relative):
    actual = {name: (cgroup / name).read_text().strip()
              for name in ("memory.max", "memory.swap.max", "cpu.max", "memory.events")}
    actual.update({"cgroup": relative, "affinity": sorted(os.sched_getaffinity(0)),
                   "platform": platform.platform(), "python": sys.version})
    return actual


def validate_limits(actual, memory):
    quota, period = actual["cpu.max"].split()
    if actual["memory.max"] != str(memory * 1024 ** 2):
        raise ValueError("the requested memory cgroup was not applied")
    if (quota == "max" or int(period) <= 0 or int(quota) <= 0 or int(quota) / int(period) > 4
            or len(actual["affinity"]) != 4 or len(set(actual["affinity"])) != 4):
        raise ValueError("resource gates require four allowed CPUs with at most four CPUs of quota")
    if actual["memory.swap.max"] != "0":
        raise ValueError("swap must be disabled for the measured memory budget")
    events = dict(line.split() for line in actual["memory.events"].splitlines())
    if any(int(events.get(name, -1)) != 0 for name in ("oom", "oom_kill")):
        raise ValueError("an OOM or killed descendant cannot count as resource acceptance")


def verify_resource_receipt(output, memory):
    receipt = json.loads((output / "limits.json").read_text(encoding="utf-8"))
    for phase in ("before", "after"):
        validate_limits(receipt[phase], memory)
    for key in ("cgroup", "memory.max", "memory.swap.max", "cpu.max", "affinity"):
        if receipt["before"][key] != receipt["after"][key]:
            raise ValueError("resource limits changed during the consumer: " + key)
    if receipt.get("pass") is not True or type(receipt.get("exitCode")) is not int or receipt["exitCode"] != 0:
        raise ValueError("resource consumer did not finish with a verified post-run receipt")


def worker(spec_path):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    cgroup = Path("/sys/fs/cgroup") / relative.lstrip("/")
    receipt = {"before": limits_snapshot(cgroup, relative), "pass": False, "exitCode": None}
    output = Path(spec["output"])
    save(output / "limits.json", receipt)
    validate_limits(receipt["before"], spec["memoryMiB"])
    env = os.environ | spec.get("env", {}) | {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run(spec["command"], env=env, check=False)
        receipt["exitCode"] = result.returncode
    finally:
        receipt["after"] = limits_snapshot(cgroup, relative)
        save(output / "limits.json", receipt)
    validate_limits(receipt["after"], spec["memoryMiB"])
    receipt["pass"] = receipt["exitCode"] == 0
    save(output / "limits.json", receipt)
    verify_resource_receipt(output, spec["memoryMiB"])
    return 0


def result_count(output, filename, single=False, pass_key="pass"):
    count = validate_results(output / filename, pass_key=pass_key)
    verify_result_contract(output.name, output / filename)
    if single and count != 1:
        raise ValueError("expected exactly one resource result")
    return count


def execute(name, argv, output, memory, timeout=300, extra_env=None):
    if output.exists():
        raise ValueError(f"refusing stale resource evidence: {output}")
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
        receipt = command(systemd, output.parent / "logs" / name, timeout=timeout + 30)
        try:
            verify_resource_receipt(output, memory)
        except (OSError, ValueError, KeyError, TypeError) as error:
            receipt.update({"pass": False, "resourceReceiptError": str(error)})
        return receipt
    finally:
        # Also reclaim children if the consumer itself crashed or an outer
        # watchdog timed out. A failed systemd startup cannot count as a pass.
        subprocess.run(["sudo", "-n", "systemctl", "stop", unit], capture_output=True, timeout=30, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=("adversarial", "load", "index", "queries"), required=True)
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
                verify_consumer_identity(script, destination, binary)
                row["caseCount"] = result_count(destination, evidence, single, key)
            except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
                row.update({"pass": False, "evidenceError": str(error)})
                print(f"FAIL semantic evidence {name}: {error}"[:2000], flush=True)
            rows.append(row)
            summary(output, rows, "Linux adversarial cgroup E2E")
    elif args.suite == "queries":
        # Independent Linux query-density and retention dimensions. Each process
        # retains its original latency/memory assertions inside the same hard
        # four-CPU, 8-GiB/no-swap envelope as the load lane.
        workload = output / "workload"
        row = command([sys.executable, E2E / "wf-bounded-workload.py", workload], output / "logs/workload")
        row["name"] = "workload-generator"
        rows.append(row)
        if not row["pass"]:
            return summary(output, rows, "Linux query/retention cgroup E2E")
        for script, timeout, mode in (
                ("wf-bounded-lsp-probe", 90, "work-first"),
                ("wf-external-candidate-density", 90, "standalone"),
                ("wf-inherited-parameter-density", 90, "standalone"),
                ("wf-exit-query-density", 90, "standalone"),
                ("wf-remediation-load", 180, "standalone"),
                ("wf-remediation-retention", 90, "standalone"),
                ("wf-query-handoff-retention", 600, "work-last")):
            destination = output / script
            arguments = [workload, destination] if mode == "work-first" else [destination, workload] if mode == "work-last" else [destination]
            row = execute(script, [sys.executable, E2E / (script + ".py"), binary, *arguments],
                          destination, 8192, timeout=timeout)
            row["name"] = script
            try:
                verify_consumer_identity(script, destination, binary)
                row["caseCount"] = result_count(destination, "results.json")
            except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
                row.update({"pass": False, "evidenceError": str(error)})
                print(f"FAIL semantic evidence {script}: {error}"[:2000], flush=True)
            rows.append(row)
            summary(output, rows, "Linux query/retention cgroup E2E")
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
                    verify_consumer_identity("wf-persistent-workspace", destination, binary)
                    row["caseCount"] = validate_results(destination / "results.json", assertion_key="checks", require_assertions=True)
                    verify_result_contract("wf-persistent-workspace-50000" if count == 50000 else "wf-persistent-workspace", destination / "results.json")
                except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
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
                verify_consumer_identity(script, destination, binary)
                row["caseCount"] = result_count(destination, evidence, single, key)
            except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
                row.update({"pass": False, "evidenceError": str(error)})
                print(f"FAIL semantic evidence {script}: {error}"[:2000], flush=True)
            rows.append(row)
            summary(output, rows, "Linux constrained load E2E")
    return summary(output, rows, "Linux " + args.suite + " cgroup E2E")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_worker":
        raise SystemExit(worker(sys.argv[2]))
    else:
        raise SystemExit(0 if main() else 1)
