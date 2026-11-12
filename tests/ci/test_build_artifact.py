"""Actual transfer archives with synthetic identities; native execution remains CI-mandatory."""

import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("native_transfer", HERE / "build_artifact.py")
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


class BuildArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wf-transfer-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.sender, self.receiver = self.root / "producer", self.root / "consumer"
        for root in (self.sender, self.receiver):
            (root / "tests/ci").mkdir(parents=True)
            shutil.copyfile(HERE / "run.py", root / "tests/ci/run.py")
        self.commit, self.version = "a" * 40, "0.0.1-alpha.1"
        self.platform = transfer.ci.host_platform()
        self.identity = {"product": "argo-workflows", "analysisReady": True,
                         "sourceCommit": self.commit, "serverVersion": self.version,
                         "dirty": False, "buildMode": "release", "defaultTargetVersion": "auto"}
        self.build = self.sender / transfer.BUILD_ROOT
        self.build.mkdir(parents=True)
        name = "argo-workflows-ls.exe" if self.platform.startswith("windows/") else "argo-workflows-ls"
        self.binary = self.build / name
        self.binary.write_bytes(b"synthetic transfer fixture, not an executable LSP")
        self.binary.chmod(0o755)
        self.metadata = {"binary": str(self.binary), "binarySha256": transfer.ci.sha(self.binary),
                         "sourceCommit": self.commit, "sourceStatus": "", "platform": self.platform,
                         "identity": self.identity}
        transfer.ci.save(self.build / "build.json", self.metadata)
        self.bundle = self.sender / "artifacts/native-build"
        self.output = self.receiver / "artifacts/ci/relocated"
        self.source_patch = patch.object(transfer, "source_identity")
        self.runtime_patch = patch.object(transfer, "binary_identity", return_value=self.identity)
        self.source_patch.start()
        self.runtime_patch.start()
        self.addCleanup(self.source_patch.stop)
        self.addCleanup(self.runtime_patch.stop)

    def pack(self):
        with patch.object(transfer, "ROOT", self.sender):
            return transfer.pack(self.build / "build.json", self.bundle, self.commit, self.version, self.platform)

    def restore(self, **overrides):
        with patch.object(transfer, "ROOT", self.receiver):
            return transfer.restore(self.bundle, self.output, overrides.get("commit", self.commit),
                                    overrides.get("version", self.version), overrides.get("platform", self.platform))

    def test_real_archive_roundtrip_relocates_only_the_derived_manifest(self):
        producer = self.pack()
        original = (self.build / "build.json").read_bytes()
        result = self.restore()
        self.assertTrue(result["verified"])
        self.assertEqual((self.receiver / transfer.BUILD_ROOT / "build.json").read_bytes(), original)
        relocated = json.loads((self.output / "build.json").read_bytes())
        self.assertEqual(relocated["originalBinaryPath"], str(self.binary))
        self.assertEqual(relocated["originalBuildManifestSha256"], producer["buildManifestSha256"])
        self.assertEqual(relocated["producerRecordSha256"], transfer.ci.sha(self.bundle / "producer.json"))
        self.assertEqual(Path(relocated["binary"]).read_bytes(), self.binary.read_bytes())
        self.assertEqual(Path(relocated["binary"]).stat().st_mode & 0o777, self.binary.stat().st_mode & 0o777)

    def test_bad_source_version_or_platform_is_rejected_before_extraction(self):
        self.pack()
        for overrides in ({"commit": "b" * 40}, {"version": "9.0.0"}, {"platform": "invalid/platform"}):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, "mismatch"):
                self.restore(**overrides)
            self.assertFalse((self.receiver / transfer.BUILD_ROOT).exists())

    def test_corrupted_transfer_receipt_is_a_hard_failure(self):
        self.pack()
        with (self.bundle / "evidence.json").open("ab") as target:
            target.write(b" ")
        with self.assertRaisesRegex(ValueError, "receipt checksum"):
            self.restore()

    def test_corrupted_archive_is_a_hard_failure(self):
        self.pack()
        with (self.bundle / "evidence.tar.gz").open("ab") as target:
            target.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "archive (size|checksum)"):
            self.restore()

    def test_independent_producer_binary_hash_is_required(self):
        producer = self.pack()
        producer["binarySha256"] = "0" * 64
        transfer.ci.save(self.bundle / "producer.json", producer)
        with self.assertRaisesRegex(ValueError, "imported binary checksum"):
            self.restore()

    def test_independent_build_manifest_hash_is_required(self):
        producer = self.pack()
        producer["buildManifestSha256"] = "0" * 64
        transfer.ci.save(self.bundle / "producer.json", producer)
        with self.assertRaisesRegex(ValueError, "build manifest checksum"):
            self.restore()

    def test_runtime_identity_is_reexecuted_and_compared(self):
        self.pack()
        bad = copy.deepcopy(self.identity)
        bad["sourceCommit"] = "b" * 40
        with patch.object(transfer, "binary_identity", return_value=bad), self.assertRaisesRegex(ValueError, "runtime identity"):
            self.restore()

    def test_wrong_transfer_implementation_is_rejected(self):
        producer = self.pack()
        producer["transferSha256"] = "0" * 64
        transfer.ci.save(self.bundle / "producer.json", producer)
        with self.assertRaisesRegex(ValueError, "runner source"):
            self.restore()

    def test_extractor_rejects_link_and_path_traversal_without_following(self):
        for name, node_type in (("artifacts/ci/build/escape", tarfile.SYMTYPE), ("../escape", tarfile.REGTYPE),
                                ("artifacts/ci/build/C:\\outside", tarfile.REGTYPE),
                                ("artifacts/ci/build/C:/outside", tarfile.REGTYPE),
                                ("artifacts/ci/build/x\\..\\..\\escape", tarfile.REGTYPE)):
            with self.subTest(name=name):
                archive = self.root / "unsafe.tar.gz"
                info = tarfile.TarInfo(name)
                info.type, info.linkname = node_type, "/outside"
                with tarfile.open(archive, "w:gz") as output:
                    output.addfile(info, io.BytesIO(b"") if info.isfile() else None)
                with patch.object(transfer, "ROOT", self.receiver), self.assertRaises(ValueError):
                    transfer.extract_build(archive)
                self.assertFalse((self.receiver / transfer.BUILD_ROOT).exists())

    def test_existing_consumer_build_is_not_overwritten(self):
        self.pack()
        self.restore()
        with self.assertRaisesRegex(ValueError, "destination already exists"):
            self.restore()


if __name__ == "__main__":
    unittest.main()
