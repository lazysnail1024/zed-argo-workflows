#!/usr/bin/env python3
"""A completed CLI report keeps its exit status when stdout crosses the deadline."""
import hashlib
import json
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

binary = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
file_count, references_per_file = 64, 15
# Start this pause only after observing output: analysis has finished, and the
# report is being published. Keep the product's real five-second deadline.
output_pause_seconds = 5.25
results = []


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


for name, output_format, entrypoint, expected_exit in (
    ("json-success", "json", "main", 0),
    ("json-error", "json", "absent", 1),
    ("human-success", "human", "main", 0),
):
    folder = out / name
    folder.mkdir()
    work = folder / "input"
    work.mkdir()
    (work / ".argo-workflows-ls.yaml").write_text(
        'schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\n', encoding="utf-8")
    expected = set()
    for index in range(file_count):
        path = work / f"{index:04d}.yaml"
        text = ("apiVersion: argoproj.io/v1alpha1\nkind: Workflow\n"
                f"metadata: {{name: report-{index}}}\nspec:\n  entrypoint: {entrypoint}\n"
                "  templates:\n    - name: main\n      dag:\n        tasks:\n")
        for ref in range(references_per_file):
            text += (f"          - name: task-{ref}\n"
                     f"            templateRef: {{name: absent-{ref}, template: main}}\n")
            expected.add((path.as_uri(), "WF_TEMPLATE_REF_UNRESOLVED",
                          f"external template resource is not resolved: absent-{ref}"))
        if entrypoint == "absent":
            expected.add((path.as_uri(), "WF_LOCAL_TEMPLATE_MISSING",
                          "local template does not exist: absent"))
        path.write_text(text, encoding="utf-8")

    command = [str(binary), "check", "--root", str(work), "--format", output_format]
    checks = {key: False for key in (
        "parse", "analysisFinishedBeforeDeadline", "outputHeldPastDeadline", "writerBlocked",
        "everyDiagnosticPublished", "noIncompleteOrConfigErrors", "exactCounts",
        "exitMatchesPublishedOutcome", "stderrEmpty",
    )}
    started = time.monotonic()
    process = None
    first = stdout = stderr = b""
    timed_out = harness_killed = False
    first_byte_seconds = pause_seconds = None
    failure = None
    try:
        # Unbuffered reading is essential: an eager buffered reader could drain
        # the pipe and allow the product to exit before the intended pause.
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        first_byte = queue.Queue()

        def read_first():
            try:
                first_byte.put(process.stdout.read(1))
            except OSError as error:
                first_byte.put(error)

        reader = threading.Thread(target=read_first, daemon=True)
        reader.start()
        first = first_byte.get(timeout=10)
        reader.join(timeout=1)
        if isinstance(first, OSError):
            raise first
        first_byte_seconds = time.monotonic() - started
        if first:
            pause_started = time.monotonic()
            time.sleep(output_pause_seconds)
            pause_seconds = time.monotonic() - pause_started
            checks["outputHeldPastDeadline"] = pause_seconds > 5
            checks["writerBlocked"] = process.poll() is None
        rest, stderr = process.communicate(timeout=10)
        stdout = first + rest
        checks["stderrEmpty"] = stderr == b""
        checks["exitMatchesPublishedOutcome"] = process.returncode == expected_exit
        checks["analysisFinishedBeforeDeadline"] = bool(first) and first_byte_seconds < 5
        if output_format == "json":
            report = json.loads(stdout)
            checks["parse"] = (isinstance(report, dict) and report.get("product") == "argo-workflows"
                               and report.get("reportSchemaVersion") == 1)
            diagnostics = report.get("diagnostics", [])
            actual = {(d.get("uri"), d.get("code"), d.get("message")) for d in diagnostics}
            checks["everyDiagnosticPublished"] = (
                actual == expected and len(diagnostics) == len(expected)
                and all(d.get("source") == "argo-workflows" and d.get("severity") == (
                    "Error" if d.get("code") == "WF_LOCAL_TEMPLATE_MISSING" else "Information")
                        for d in diagnostics))
            checks["noIncompleteOrConfigErrors"] = (
                report.get("analysisComplete") is True and report.get("notEvaluated") == []
                and report.get("configurationErrors") == [] and bool(report.get("targets"))
                and all(target.get("analysisComplete") is True for target in report["targets"]))
            checks["exactCounts"] = (
                report.get("filesAnalyzed") == file_count and report.get("resourcesAnalyzed") == file_count)
            checks["analysisFinishedBeforeDeadline"] &= (
                type(report.get("elapsedMilliseconds")) is int and 0 <= report["elapsedMilliseconds"] < 5000)
        else:
            lines = stdout.decode("utf-8").splitlines()
            parsed = [re.fullmatch(r"(.*):([1-9][0-9]*):([1-9][0-9]*): ([A-Z0-9_]+) (.*)", line)
                      for line in lines]
            checks["parse"] = bool(lines) and all(match is not None for match in parsed)
            actual = {(match[1], match[4], match[5]) for match in parsed if match is not None}
            checks["everyDiagnosticPublished"] = actual == expected and len(lines) == len(expected)
            checks["noIncompleteOrConfigErrors"] = stderr == b""
            checks["exactCounts"] = len({item[0] for item in actual}) == file_count
    except (subprocess.TimeoutExpired, queue.Empty) as error:
        timed_out = True
        failure = type(error).__name__
    except (OSError, ValueError, TypeError, AttributeError) as error:
        failure = str(error)
    finally:
        if process is not None and process.poll() is None:
            harness_killed = True
            process.kill()
            rest, stderr = process.communicate(timeout=10)
            stdout = first + rest if isinstance(first, bytes) else rest
        elapsed = time.monotonic() - started
        (folder / "stdout.raw").write_bytes(stdout)
        (folder / "stderr.raw").write_bytes(stderr)
        exit_code = process.returncode if process is not None else None
        checks["naturalExit"] = not harness_killed and exit_code in (0, 1, 2, 3)
        checks["exception"] = failure is None
        save(folder / "check.command.json", {
            "command": command, "exitCode": exit_code, "timedOut": timed_out,
            "seconds": elapsed, "firstByteSeconds": first_byte_seconds,
            "outputPauseSeconds": pause_seconds,
        })
        results.append({"case": name, "assertions": checks, "exitCode": exit_code,
                        "timedOut": timed_out, "harnessKilled": harness_killed, "failure": failure,
                        "firstByteSeconds": first_byte_seconds, "outputPauseSeconds": pause_seconds,
                        "seconds": elapsed, "pass": all(checks.values())})
        save(out / "results.json", results)

save(out / "provenance.json", {
    "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    "testSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "files": file_count, "referencesPerFile": references_per_file,
})
sys.exit(0 if all(row["pass"] for row in results) else 1)
