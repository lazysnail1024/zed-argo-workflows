#!/usr/bin/env python3
"""Install checksum-pinned Linux tools used only by explicit offline E2E cases."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import platform
import tarfile
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("These oracle tools are pinned for the Linux amd64 integration job")
    args.output.mkdir(parents=True, exist_ok=True)
    # Published checksums, not values calculated from the downloaded payload:
    # https://github.com/argoproj/argo-workflows/releases/tag/v3.6.0
    # https://github.com/helm/helm/releases/tag/v3.19.0
    tools = [
        ("argo", "3.6.0", "https://github.com/argoproj/argo-workflows/releases/download/v3.6.0/argo-linux-amd64.gz",
         "fa527acfeebc3a8c74a015883ff0ad97ac1114796389289eea631f5073a93343", None),
        ("helm", "3.19.0", "https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz",
         "a7f81ce08007091b86d8bd696eb4d86b8d0f2e1b9f6c714be62f82f96a594496", "linux-amd64/helm"),
    ]
    manifest = []
    for name, version, url, expected, member in tools:
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError(f"{name} download does not match the pinned SHA-256")
        if member:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                executable = archive.extractfile(member).read()
        else:
            executable = gzip.decompress(payload)
        target = args.output / name
        target.write_bytes(executable)
        target.chmod(0o755)
        manifest.append({"tool": name, "version": version, "url": url, "archiveSha256": expected,
                         "binarySha256": hashlib.sha256(executable).hexdigest()})
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
