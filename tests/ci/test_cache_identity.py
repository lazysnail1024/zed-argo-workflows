"""The actual compiler/image fingerprint CLI remains deterministic and fail closed."""

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


class CacheIdentityTests(unittest.TestCase):
    def invoke(self, root, name, overrides):
        destination = root / name
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("run.py")),
                                 "cache-identity", "--output", str(destination)],
                                env=os.environ | overrides, capture_output=True, timeout=40)
        return result, destination

    def test_same_inputs_repeat_and_image_change_invalidates(self):
        with tempfile.TemporaryDirectory(prefix="wf-cache-key-") as temporary:
            root = Path(temporary)
            first, first_path = self.invoke(root, "first", {"ImageVersion": "test-image-1"})
            repeated, repeated_path = self.invoke(root, "repeated", {"ImageVersion": "test-image-1"})
            changed, changed_path = self.invoke(root, "changed", {"ImageVersion": "test-image-2"})
            self.assertEqual((first.returncode, repeated.returncode, changed.returncode), (0, 0, 0), first.stderr)
            self.assertEqual((first_path / "identity.json").read_bytes(),
                             (repeated_path / "identity.json").read_bytes())
            self.assertNotEqual((first_path / "identity.json").read_bytes(),
                                (changed_path / "identity.json").read_bytes())
            identity = json.loads((first_path / "identity.json").read_bytes())
            self.assertTrue(identity["compilerVersion"])
            self.assertIn(identity["platform"], {"linux/amd64", "linux/arm64", "darwin/amd64",
                                                "darwin/arm64", "windows/amd64"})

    def test_unavailable_compiler_does_not_produce_a_cache_key(self):
        with tempfile.TemporaryDirectory(prefix="wf-cache-key-") as temporary:
            result, destination = self.invoke(Path(temporary), "missing", {"CC": "nonexistent-ci-compiler"})
            self.assertEqual(result.returncode, 1)
            self.assertFalse((destination / "identity.json").exists())

    def test_nested_native_include_change_invalidates_without_a_lock_change(self):
        spec = importlib.util.spec_from_file_location("cache_native_inputs", Path(__file__).with_name("run.py"))
        ci = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ci)
        with tempfile.TemporaryDirectory(prefix="wf-cache-native-") as temporary:
            root = Path(temporary)
            include = root / "vendor/example/grammar/tree_sitter/parser.c.inc"
            include.parent.mkdir(parents=True)
            include.write_bytes(b"first C include bytes")
            output = root / "output"
            compiler = SimpleNamespace(stdout="fixed compiler\n", stderr="")
            with patch.object(ci, "ROOT", root), patch.object(ci.subprocess, "run", return_value=compiler):
                ci.cache_identity(SimpleNamespace(output=output))
                before = json.loads((output / "identity.json").read_bytes())
                include.write_bytes(b"changed C include bytes")
                ci.cache_identity(SimpleNamespace(output=output))
                after = json.loads((output / "identity.json").read_bytes())
            self.assertEqual(before["nativeSourceCount"], 1)
            self.assertNotEqual(before["nativeSourcesSha256"], after["nativeSourcesSha256"])
            self.assertEqual(before["compilerVersion"], after["compilerVersion"])


if __name__ == "__main__":
    unittest.main()
