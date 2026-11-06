#!/usr/bin/env python3
"""Run the production installer in Windows/Linux WASI with real native probes.

Only Zed's download/chmod/process boundaries are bridged; filesystem operations
stay in the wasm32-wasip2 guest. This is not a Zed GUI acceptance test.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform as host_platform
import queue
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_VERSION = "48.0.7"  # Same release used by Zed's current WASI host.
RUNTIMES = {
    "linux": ("tar.xz", "2192e2b5b911dc42321b9eac2b7f1f0b0d03356058c1e2b2c2b892625bdd0f45"),
    "windows": ("zip", "23b43be8720d8fed20caee24d78104c9093717b90f2b4b0bdd171b6dd7f224af"),
}
CASE = "distribution::tests::runtime::wasi_cache_runtime"
PREFIX = "ARGO_CACHE_HOST:"


def download(url, path, limit):
    request = urllib.request.Request(url, headers={"User-Agent": "zed-workflows-cache-acceptance"})
    with urllib.request.urlopen(request, timeout=60) as response, path.open("wb") as out:
        total = 0
        while block := response.read(1024 * 1024):
            total += len(block)
            if total > limit:
                raise ValueError(f"Oversized download: {url}")
            out.write(block)


def runtime(folder, system):
    extension, expected = RUNTIMES[system]
    stem = f"wasmtime-v{RUNTIME_VERSION}-x86_64-{system}"
    archive = folder / f"{stem}.{extension}"
    download(f"https://github.com/bytecodealliance/wasmtime/releases/download/v{RUNTIME_VERSION}/{archive.name}",
             archive, 64 * 1024 * 1024)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected, "Wasmtime checksum mismatch"
    name = "wasmtime.exe" if system == "windows" else "wasmtime"
    target = folder / name
    # Extract only the exact executable, never arbitrary archive paths.
    if extension == "zip":
        with zipfile.ZipFile(archive) as package:
            target.write_bytes(package.read(f"{stem}/{name}"))
    else:
        with tarfile.open(archive) as package:
            member = package.getmember(f"{stem}/{name}")
            assert member.isfile()
            with package.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    target.chmod(0o755)
    return target


class DeleteLocks:
    """A real Windows sharing mode: reads/writes allowed, delete/rename denied."""
    def __init__(self):
        self.handles = {}
        if os.name == "nt":
            from ctypes import wintypes
            self.api = ctypes.WinDLL("kernel32", use_last_error=True)
            self.api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            self.api.CreateFileW.restype = wintypes.HANDLE
            self.api.CloseHandle.argtypes = [wintypes.HANDLE]
            self.api.CloseHandle.restype = wintypes.BOOL

    def hold(self, path):
        if os.name != "nt":
            return
        for item in (path.parent, path):
            if item in self.handles:
                continue
            flags = 0x02000000 if item.is_dir() else 0x80
            handle = self.api.CreateFileW(str(item), 0x80000000, 3, None, 3, flags, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            self.handles[item] = handle

    def close(self):
        failures = []
        for handle in self.handles.values():
            if not self.api.CloseHandle(handle):
                failures.append(ctypes.get_last_error())
        self.handles.clear()
        assert not failures, f"CloseHandle failed: {failures}"


def guest(wasmtime, wasm, work, assets, base, platform, scenario, client, evidence, barrier=None):
    work.mkdir(parents=True, exist_ok=True)
    command = [str(wasmtime), "run", "--dir", ".", "--env", f"ARGO_CACHE_PLATFORM={platform}",
               "--env", f"ARGO_CACHE_SCENARIO={scenario}", "--env", f"ARGO_CACHE_CLIENT={client}",
               str(wasm), "--exact", CASE, "--nocapture", "--test-threads=1"]
    locks, requests = DeleteLocks(), []
    interrupted = False
    probes = 0
    evidence.mkdir(parents=True, exist_ok=False)
    with (evidence / "stderr.log").open("w", encoding="utf-8") as stderr, \
            (evidence / "stdout.log").open("w", encoding="utf-8") as stdout:
        process = subprocess.Popen(command, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=stderr, text=True, encoding="utf-8")
        lines = queue.Queue()

        def pump():
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        deadline = time.monotonic() + 120
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("WASI cache test exceeded 120 seconds")
                line = lines.get(timeout=remaining)
                if line is None:
                    break
                stdout.write(line)
                stdout.flush()
                if PREFIX not in line:
                    continue
                request = json.loads(line.split(PREFIX, 1)[1])
                requests.append(request)
                response = {"result": None}
                try:
                    op = request["op"]
                    relative = request.get("path", "lock-control/server.exe")
                    path = (work / relative).resolve()
                    assert path.is_relative_to(work.resolve()), "Guest path escaped work directory"
                    if op == "fetch":
                        name = request["url"].removeprefix(base + "/")
                        assert request["url"] == base + "/" + name and name in assets
                        data = assets[name].read_bytes()
                        assert len(data) <= request["limit"]
                        if scenario == "interrupted" and name != "SHA256SUMS" and not interrupted:
                            interrupted = True
                            path.write_bytes(data[:4096])
                            locks.hold(path)
                            raise RuntimeError("runtime fixture interrupted transfer")
                        path.write_bytes(data)
                    elif op == "chmod":
                        if os.name != "nt":
                            path.chmod(0o755)
                    elif op == "corrupt":
                        assert path.name == "SHA256SUMS"
                        path.write_bytes(b"corrupted in-use cached manifest")
                    elif op == "lock-control":
                        path.parent.mkdir()
                        shutil.copyfile(next(value for name, value in assets.items() if name != "SHA256SUMS"), path)
                        locks.hold(path)
                    elif op == "identity":
                        result = subprocess.run([str(path), "version", "--json"], capture_output=True,
                                                text=True, encoding="utf-8", timeout=30)
                        assert result.returncode == 0, result.stderr
                        locks.hold(path)
                        probes += 1
                        if barrier is not None and probes == 1:
                            barrier.wait(timeout=30)
                        response["result"] = result.stdout
                    else:
                        raise ValueError(f"Unexpected host operation: {op}")
                except Exception as error:
                    response = {"error": str(error)}
                request["reply"] = response
                process.stdin.write(json.dumps(response) + "\n")
                process.stdin.flush()
            status = process.wait(timeout=10)
            assert status == 0, f"Guest failed ({status}); see {evidence}"
            output = (evidence / "stdout.log").read_text(encoding="utf-8")
            assert "test result: ok. 1 passed; 0 failed;" in output, "Expected test did not run"
            report_path = work / f"guest-report-{client}.json"
            assert report_path.is_file(), "Missing guest report"
            report = json.loads(report_path.read_text())
            assert report["passed"] and report["target"] == "wasm32"
            report.update({"hostProbes": probes, "windowsDeleteSharingDenied": os.name == "nt",
                           "retainedNativeHandles": len(locks.handles), "command": command, "requests": requests})
            (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            return report
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            reader.join(timeout=5)
            locks.close()
            (evidence / "requests.json").write_text(json.dumps(requests, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    report = {"passed": False, "cases": [], "scope": "WASI installer with native host adapters, not Zed GUI"}
    try:
        assert host_platform.system() in ("Windows", "Linux"), "Only Windows/Linux runtime hosts are supported"
        assert host_platform.machine().lower() in ("amd64", "x86_64"), "This runtime test requires x86_64"
        system = "windows" if os.name == "nt" else "linux"
        platform = f"{system}-amd64"
        version = (ROOT / "native-server-version").read_text().strip()
        asset = f"argo-workflows-ls-{version}-{platform}" + (".exe" if os.name == "nt" else "")
        base = f"https://github.com/lazysnail1024/zed-argo-workflows/releases/download/v{version}"
        report.update({"platform": platform, "nativeVersion": version, "wasmtimeVersion": RUNTIME_VERSION})
        report["sourceCommit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        report["sourceStatus"] = subprocess.check_output(["git", "status", "--porcelain", "--", "."], cwd=ROOT, text=True)
        report["sourceSHA256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                   ("src/distribution.rs", "src/distribution/tests.rs", "src/distribution/tests/runtime.rs",
                                    "tests/cache_runtime_e2e.py", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "native-server-version")}
        built = subprocess.run(["cargo", "test", "--locked", "--target", "wasm32-wasip2", "--no-run",
                                "--message-format=json"], cwd=ROOT, capture_output=True, text=True, timeout=300)
        (output / "build.stdout").write_text(built.stdout)
        (output / "build.stderr").write_text(built.stderr)
        assert built.returncode == 0, "WASI test compilation failed"
        artifacts = [json.loads(line) for line in built.stdout.splitlines() if line.startswith("{")]
        binaries = [Path(item["executable"]) for item in artifacts
                    if item.get("reason") == "compiler-artifact" and item.get("profile", {}).get("test")
                    and item.get("executable")]
        assert len(binaries) == 1 and binaries[0].is_file(), "Missing/ambiguous WASI test binary"
        report["wasmSHA256"] = hashlib.sha256(binaries[0].read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="argo-wasi-cache-") as temporary:
            scratch = Path(temporary)
            wasmtime = runtime(scratch, system)
            report["wasmtimeArchiveSHA256"] = RUNTIMES[system][1]
            report["wasmtimeIdentity"] = subprocess.check_output([str(wasmtime), "--version"], text=True, timeout=10).strip()
            assert f"wasmtime {RUNTIME_VERSION}" in report["wasmtimeIdentity"]
            assets = {name: scratch / name for name in ("SHA256SUMS", asset)}
            for name, path in assets.items():
                download(base + "/" + name, path, 128 * 1024 * 1024 if name == asset else 64 * 1024)
            expected = [line.split("  ", 1)[0] for line in assets["SHA256SUMS"].read_text().splitlines()
                        if line.endswith("  " + asset)]
            actual = hashlib.sha256(assets[asset].read_bytes()).hexdigest()
            assert expected == [actual], "Native release checksum missing/duplicated/mismatched"
            report["nativeAssetSHA256"] = actual
            for scenario in ("fresh", "interrupted", "corrupt"):
                row = guest(wasmtime, binaries[0], scratch / scenario, assets, base, platform,
                            scenario, "a", output / scenario)
                assert row["sha256"] == actual
                report["cases"].append(row)
            barrier = threading.Barrier(2)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(guest, wasmtime, binaries[0], scratch / "concurrent", assets,
                                       base, platform, "concurrent", client, output / f"concurrent-{client}", barrier)
                           for client in ("a", "b")]
                for future in futures:
                    row = future.result(timeout=150)
                    assert row["sha256"] == actual
                    report["cases"].append(row)
            report["passed"] = len(report["cases"]) == 5
    except Exception as error:
        report["error"] = repr(error)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "cases": len(report["cases"]), "error": report.get("error")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
