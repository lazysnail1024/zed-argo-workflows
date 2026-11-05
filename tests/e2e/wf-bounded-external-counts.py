#!/usr/bin/env python3
"""Exercise the real five-second deadline without assuming a runner's throughput."""
import hashlib
import json
import pathlib
import subprocess
import sys
import time

binary = pathlib.Path(sys.argv[1]).resolve()
out = pathlib.Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
work = out / "input"
work.mkdir(exist_ok=False)
config = "schemaVersion: 1\ntargetVersion: \"3.6.0\"\nreferencePolicy: closed\n"
(work / ".argo-workflows-ls.yaml").write_text(config, encoding="utf-8")
refs_per_file = 15
# Keep the original stress fixture first. Faster runners may finish it, so grow
# only after verifying a complete result. These caps remain below the product's
# 50000-file / 128 MiB snapshot limits; a budget rejection is never acceptance.
stress_sizes = (4000, 8000, 16000, 32000)
maximum_bytes = 64 * 1024 * 1024
generated = 0
yaml_bytes = 0
attempts = []
deadline = None


def generate(count):
    global generated, yaml_bytes
    for index in range(generated, count):
        text = ("apiVersion: argoproj.io/v1alpha1\nkind: Workflow\n"
                f"metadata: {{name: dense-{index}}}\nspec:\n  entrypoint: main\n"
                "  templates:\n    - name: main\n      dag:\n        tasks:\n")
        text += "".join(
            f"          - name: task-{ref}\n"
            f"            templateRef: {{name: absent-{ref}, template: main}}\n"
            for ref in range(refs_per_file))
        data = text.encode()
        yaml_bytes += len(data)
        if count > stress_sizes[-1] or yaml_bytes > maximum_bytes or len(data) >= 5 * 1024 * 1024:
            raise ValueError("Deadline fixture exceeded its independent input cap")
        (work / f"{index:04d}.yaml").write_bytes(data)
    generated = count


def completed_checks(report, code, elapsed, count):
    diagnostics = report.get("diagnostics", [])
    expected = {
        ((work / f"{index:04d}.yaml").as_uri(),
         f"external template resource is not resolved: absent-{ref}")
        for index in range(count) for ref in range(refs_per_file)
    }
    actual = {(item.get("uri"), item.get("message")) for item in diagnostics}
    return {
        "completeExit": code == 0 and report.get("analysisComplete") is True,
        "exactCounts": report.get("filesAnalyzed") == count and report.get("resourcesAnalyzed") == count,
        "noIncompleteOrConfigErrors": report.get("notEvaluated") == [] and report.get("configurationErrors") == [],
        "everyExternalReferenceChecked": actual == expected and len(diagnostics) == len(expected)
            and all(item.get("code") == "WF_TEMPLATE_REF_UNRESOLVED"
                    and item.get("severity") == "Information"
                    and item.get("source") == "argo-workflows" for item in diagnostics),
        "bounded": elapsed < 5.5,
    }


def execute(count):
    command = [str(binary), "check", "--root", str(work), "--format", "json"]
    started = time.monotonic()
    killed = False
    try:
        process = subprocess.run(command, capture_output=True, timeout=9)
    except subprocess.TimeoutExpired as error:
        killed = True
        process = subprocess.CompletedProcess(command, -9, error.stdout or b"", error.stderr or b"")
    elapsed = time.monotonic() - started
    folder = out / f"attempt-{count}"
    folder.mkdir()
    for name, data in (("stdout.raw", process.stdout), ("stderr.raw", process.stderr)):
        (folder / name).write_bytes(data)
        (out / name).write_bytes(data)
    try:
        report = json.loads(process.stdout)
        if not isinstance(report, dict):
            report = {}
    except ValueError:
        report = {}
    common = {
        "reportIdentity": report.get("product") == "argo-workflows" and report.get("reportSchemaVersion") == 1,
        "notHarnessKilled": not killed,
    }
    return command, process.returncode, elapsed, report, common


failure = None
try:
    for count in (64, *stress_sizes):
        generate(count)
        command, code, elapsed, report, common = execute(count)
        if code == 3 and count != 64:
            reasons = report.get("notEvaluated", [])
            checks = common | {
                "deadlineExit": code == 3,
                "deadlineReason": any("deadline" in reason for reason in reasons),
                "honestIncomplete": report.get("analysisComplete") is False,
                "notAllCompleted": all(type(report.get(field)) is int and 0 <= report[field] < count
                                       for field in ("filesAnalyzed", "resourcesAnalyzed")),
                "externalProgressObserved": any(reason.startswith("external analysis incomplete: ") for reason in reasons),
                "noAdmissionFailure": report.get("configurationErrors") == [],
                "bounded": elapsed < 5.5,
            }
            kind = "deadline"
        else:
            checks = common | completed_checks(report, code, elapsed, count)
            kind = "complete-positive-control" if count == 64 else "complete-calibration"
        row = {"kind": kind, "files": count, "yamlBytes": yaml_bytes,
               "command": command, "exitCode": code, "seconds": elapsed,
               "assertions": checks, "pass": all(checks.values())}
        attempts.append(row)
        (out / f"attempt-{count}" / "measurement.json").write_text(json.dumps(row, indent=2) + "\n")
        if not row["pass"]:
            failure = "Attempt failed its explicit result contract"
            break
        if kind == "deadline":
            deadline = row
            break
except (OSError, ValueError, TypeError, AttributeError) as error:
    failure = str(error)

checks = {
    "positiveControlComplete": bool(attempts) and attempts[0]["kind"] == "complete-positive-control" and attempts[0]["pass"],
    "allAttemptsVerified": bool(attempts) and all(row["pass"] for row in attempts) and failure is None,
    "genuineDeadlineObserved": deadline is not None,
    "deadlineExit": deadline is not None and deadline["assertions"]["deadlineExit"],
    "deadlineReason": deadline is not None and deadline["assertions"]["deadlineReason"],
    "notAllCompleted": deadline is not None and deadline["assertions"]["notAllCompleted"],
    "bounded": deadline is not None and deadline["assertions"]["bounded"],
    "fixtureWithinCaps": generated <= stress_sizes[-1] and yaml_bytes <= maximum_bytes,
}
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
last = attempts[-1] if attempts else {}
result = {"command": last.get("command"), "exitCode": last.get("exitCode"),
          "seconds": last.get("seconds"), "attempts": attempts, "assertions": checks,
          "pass": all(checks.values()), "binarySha256": sha(binary),
          "testSha256": sha(pathlib.Path(__file__)), "failure": failure,
          "workload": {"referencesPerFile": refs_per_file, "maximumFiles": stress_sizes[-1],
                       "maximumBytes": maximum_bytes, "actualFiles": generated, "actualBytes": yaml_bytes}}
(out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
sys.exit(0 if result["pass"] else 1)
