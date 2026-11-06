#!/usr/bin/env python3
"""Verify adapter independence and prove installer/package guards detect regressions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.absolute()
    out.mkdir(parents=True, exist_ok=False)
    report = {"command": sys.argv, "passed": False, "rows": []}
    try:
        cargo = tomllib.loads((ROOT / "Cargo.toml").read_text())
        name, version = cargo["package"]["name"], cargo["package"]["version"]
        report["nativePin"] = (ROOT / "native-server-version").read_text().strip()
        report["sourceSHA256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for folder in (ROOT / "src", ROOT / "tests") for path in folder.rglob("*")
                                  if path.is_file() and "__pycache__" not in path.parts}
        for filename in ("Cargo.toml", "Cargo.lock", "extension.toml", "rust-toolchain.toml", "native-server-version", "package.py", "licenses/upstream.json"):
            report["sourceSHA256"][filename] = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
        env = os.environ.copy()
        channel = tomllib.loads((ROOT / "rust-toolchain.toml").read_text())["toolchain"]["channel"]
        cargo_command = ["cargo", "+" + channel]
        with tempfile.TemporaryDirectory(prefix="zed-independent-adapter-") as temporary:
            # A fresh target per run prevents a cached mutation from masquerading
            # as the baseline after copytree preserves older source timestamps.
            env["CARGO_TARGET_DIR"] = str(Path(temporary) / "target")
            tree = Path(temporary) / "adapter"
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns("target", ".git", "artifacts", "__pycache__"))
            assert not any(tree.rglob("*.go")) and not (tree.parent / "go.mod").exists()
            for filename in ("Cargo.toml", "extension.toml"):
                path = tree / filename
                path.write_text(path.read_text().replace(f'version = "{version}"', 'version = "9.9.9-alpha.1"', 1))
            path = tree / "Cargo.lock"
            path.write_text(path.read_text().replace(f'name = "{name}"\nversion = "{version}"', f'name = "{name}"\nversion = "9.9.9-alpha.1"', 1))

            def run(label, command, expected=0):
                result = subprocess.run(command, cwd=tree, env=env, capture_output=True, text=True, timeout=300)
                (out / (label + ".stdout")).write_text(result.stdout)
                (out / (label + ".stderr")).write_text(result.stderr)
                row = {"variant": label, "command": command, "expectedExitCode": expected,
                       "exitCode": result.returncode, "passed": result.returncode == expected}
                report["rows"].append(row)
                assert row["passed"], f"{label}: {result.stderr[-2000:]}"
                return result, row

            run("standalone-new-adapter-version-tests", [*cargo_command, "test", "--locked", "--offline"])
            run("standalone-new-adapter-version-wasm", [*cargo_command, "build", "--locked", "--offline", "--release", "--target", "wasm32-wasip2"])
            wasm = Path(env["CARGO_TARGET_DIR"]) / "wasm32-wasip2/release" / (name.replace("-", "_") + ".wasm")
            testcase = ("pinned_alpha_is_verified_cached_and_rejects_another_release_identity" if name == "zed-argocd"
                        else "default_native_release_is_pinned_separately_from_the_adapter_version")
            distribution = tree / "src/distribution.rs"
            original_distribution = distribution.read_text()
            publication = '''        fs::create_dir(stage.join(COMPLETE)).map_err(|error| {
            format!(
                "Cannot publish verified server cache {}: {error}",
                stage.display()
            )
        })?;
'''
            probe = '        crate::identity::validate(&host.identity(&binary)?, Some(version))?;\n'
            mutations = [
                ("restore-cargo-server-version-coupling",
                 [('include_str!("../native-server-version").trim()', 'env!("CARGO_PKG_VERSION")')],
                 testcase, "assertion `left == right` failed"),
                ("restore-post-probe-directory-rename",
                 [("        Ok(binary)\n    })();", '''        let destination = root.join(format!("{slot}.gen2-moved-{}", nonce()?));
        fs::rename(&stage, &destination).map_err(|error| error.to_string())?;
        Ok(destination.join(&asset))
    })();''')],
                 "installation_keeps_the_identity_probe_path",
                 "Do not move a directory after executing its server"),
                ("remove-new-generation-completion-check",
                 [("&& (!needs_marker || existing_directory(&generation.join(COMPLETE))?)", "&& true")],
                 "complete_bytes_are_invisible_until_the_identity_probe_finishes",
                 "An unfinished generation must not be selected"),
                ("publish-completion-before-identity",
                 [(publication, ""), (probe, publication + probe)],
                 "complete_bytes_are_invisible_until_the_identity_probe_finishes",
                 "Publish completion only after the native identity has passed"),
                ("remove-download-digest-check",
                 [("if !regular(&binary, MAX_BINARY)? || digest(&binary)? != expected {",
                   "if !regular(&binary, MAX_BINARY)? {")],
                 "digest_mismatch_never_chmods_or_probes_download",
                 "called `Result::unwrap_err()` on an `Ok` value"),
            ]
            for label, replacements, testcase, assertion in mutations:
                mutant = original_distribution
                for anchor, replacement in replacements:
                    assert mutant.count(anchor) == 1, f"{label}: expected one live mutation anchor"
                    mutant = mutant.replace(anchor, replacement, 1)
                distribution.write_text(mutant)
                try:
                    qualified = f"distribution::tests::{testcase}"
                    result, row = run(label, [*cargo_command, "test", "--locked", "--offline", "--lib",
                                             qualified, "--", "--exact", "--nocapture", "--color", "never"], 101)
                    row["expectedTest"] = qualified
                    # Exit 101 also denotes a compiler error. Require one named
                    # runtime failure and its intended assertion, not zero tests
                    # or an unrelated filesystem/network/toolchain failure.
                    lines = result.stdout.splitlines()
                    row["exactTestExecuted"] = ("running 1 test" in lines
                                                and f"test {qualified} ... FAILED" in lines)
                    row["runtimeAssertionFailed"] = "test result: FAILED. 0 passed; 1 failed; 0 ignored;" in result.stdout
                    row["detectedByExpectedCheck"] = assertion in result.stdout + result.stderr
                    row["passed"] = (row["passed"] and row["exactTestExecuted"]
                                     and row["runtimeAssertionFailed"] and row["detectedByExpectedCheck"])
                    assert row["passed"], f"{label}: intended runtime assertion did not fail: {result.stdout[-2000:]} {result.stderr[-2000:]}"
                finally:
                    distribution.write_text(original_distribution)
            assert distribution.read_text() == original_distribution
            run("restored-distribution-tests", [*cargo_command, "test", "--locked", "--offline"])

            package = tree / "package.py"
            original_package = package.read_text()
            mutations = [
                ("remove-wasm-release-identity-guard", 'if release != [manifest["id"], manifest["version"], native]:', "if False:", "stale-native-pin"),
                ("remove-original-dependency-notices", "    files.update(dependency_notices())", "    # Test mutation: omit original dependency notices.", "licenses/THIRD_PARTY.json"),
            ]
            for label, anchor, replacement, failure in mutations:
                assert original_package.count(anchor) == 1, f"{label}: expected one live mutation anchor"
                package.write_text(original_package.replace(anchor, replacement, 1))
                evidence = out / label
                result, row = run(label, [sys.executable, str(tree / "tests/package_e2e.py"), "--wasm", str(wasm), "--output", str(evidence)], 1)
                oracle = json.loads((evidence / "report.json").read_text())
                failed_names = [case["name"] for case in oracle["cases"] if not case["passed"]]
                row["detectedByExpectedCheck"] = (failed_names == [failure] if failure == "stale-native-pin"
                                                   else failure in oracle.get("error", "") and not failed_names)
                row["passed"] = row["passed"] and row["detectedByExpectedCheck"] and not oracle["passed"]
                assert row["passed"], f"{label}: unrelated oracle failure: {oracle.get('error')}"
                package.write_text(original_package)
            report["passed"] = all(row["passed"] for row in report["rows"])
    except Exception as error:
        report["error"] = repr(error)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "rows": report["rows"], "error": report.get("error")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
