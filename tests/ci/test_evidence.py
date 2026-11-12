"""Real archive round trips and refusal controls for complete CI evidence."""

import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("ci_evidence", Path(__file__).with_name("evidence.py"))
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wf-evidence-")
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.root = self.workspace / "artifacts/ci"
        self.root.mkdir(parents=True)
        (self.root / "fixture/.git").mkdir(parents=True)
        # Archive raw bytes unchanged; text-mode newline conversion differs on Windows.
        self.history = b"synthetic source history\r\nline without terminator"
        (self.root / "fixture/.git/config").write_bytes(self.history)
        (self.root / "failed.stdout").write_bytes(b"failed raw protocol\n\x00")
        self.executable = self.root / "server"
        self.executable.write_bytes(b"synthetic executable fixture, not a native LSP\n")
        self.executable.chmod(0o755)
        self.output = self.workspace / "artifacts/packed"

    def pack(self):
        result = evidence.pack([self.root], self.output, self.workspace)
        self.receipt = self.output / "evidence.json"
        return result

    def rewrite(self, transform):
        archive = self.output / "evidence.tar.gz"
        rows = []
        with tarfile.open(archive, "r:gz") as source:
            for info in source:
                data = source.extractfile(info).read() if info.isfile() else None
                rows.append((info, data))
        with tarfile.open(archive, "w:gz") as target:
            for info, data in transform(rows):
                target.addfile(info, io.BytesIO(data) if data is not None else None)
        receipt = json.loads(self.receipt.read_bytes())
        receipt["archiveSha256"] = evidence.digest(archive)
        receipt["archiveBytes"] = archive.stat().st_size
        self.receipt.write_bytes(evidence.encoded(receipt))

    def test_complete_roundtrip_preserves_hidden_files_modes_and_originals(self):
        before = self.executable.stat()
        receipt = self.pack()
        self.assertTrue(receipt["verification"]["verified"])
        self.assertEqual(evidence.verify(self.receipt)["entryCount"], 6)
        with tarfile.open(self.output / "evidence.tar.gz", "r:gz") as archive:
            self.assertEqual(archive.extractfile("artifacts/ci/fixture/.git/config").read(),
                             self.history)
            self.assertEqual(archive.getmember("artifacts/ci/server").mode, stat.S_IMODE(before.st_mode))
        self.assertEqual(self.executable.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertTrue((self.root / "failed.stdout").exists())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO fixtures require POSIX")
    def test_fifo_is_metadata_and_never_opened(self):
        fifo = self.root / "blocked.yaml"
        os.mkfifo(fifo)
        self.pack()
        with tarfile.open(self.output / "evidence.tar.gz", "r:gz") as archive:
            self.assertTrue(archive.getmember("artifacts/ci/blocked.yaml").isfifo())
        self.assertTrue(stat.S_ISFIFO(fifo.lstat().st_mode))

    def test_external_symlink_is_not_dereferenced(self):
        sentinel = self.workspace / "outside-secret"
        sentinel.write_bytes(b"external sentinel must never be opened")
        link = self.root / "external"
        try:
            link.symlink_to(sentinel)
        except OSError as error:
            self.skipTest("host cannot create symlink: " + str(error))
        # Windows may store a \\?\ extended path rather than the input spelling.
        stored_target = os.readlink(link)
        self.pack()
        with tarfile.open(self.output / "evidence.tar.gz", "r:gz") as archive:
            info = archive.getmember("artifacts/ci/external")
            self.assertTrue(info.issym())
            self.assertEqual(info.linkname, stored_target)
            for member in archive.getmembers():
                if member.isfile():
                    self.assertNotIn(sentinel.read_bytes(), archive.extractfile(member).read())

    def test_archive_byte_corruption_is_rejected(self):
        self.pack()
        with (self.output / "evidence.tar.gz").open("ab") as archive:
            archive.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "archive (checksum|size)"):
            evidence.verify(self.receipt)

    def test_changed_regular_content_is_rejected_even_with_new_archive_digest(self):
        self.pack()
        def change(rows):
            return [(info, b"X" * len(data) if info.name.endswith("failed.stdout") else data)
                    for info, data in rows]
        self.rewrite(change)
        with self.assertRaisesRegex(ValueError, "inventory checksum"):
            evidence.verify(self.receipt)

    def test_duplicate_member_is_rejected(self):
        self.pack()
        self.rewrite(lambda rows: [rows[0], *rows])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evidence.verify(self.receipt)

    def test_path_traversal_is_rejected(self):
        self.pack()
        def change(rows):
            rows[0][0].name = "../outside"
            return rows
        self.rewrite(change)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            evidence.verify(self.receipt)

    def test_missing_inventory_is_rejected(self):
        self.pack()
        self.rewrite(lambda rows: rows[:-1])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            evidence.verify(self.receipt)

    def test_output_inside_input_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "inside an input"):
            evidence.pack([self.root], self.root / "archive", self.workspace)

    def test_source_outside_artifacts_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "beneath artifacts"):
            evidence.pack([self.workspace], self.output, self.workspace)

    def test_missing_source_and_existing_output_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid evidence root"):
            evidence.pack([self.root / "absent"], self.output, self.workspace)
        self.pack()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.pack()

    def test_overlapping_roots_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlapping"):
            evidence.pack([self.root, self.root / "fixture"], self.output, self.workspace)


if __name__ == "__main__":
    unittest.main()
