"""False-pass resistance for the four-shard, 146-release native acceptance gate.

Only synthetic evidence is used here. Actual CLI/LSP execution remains mandatory
in run.py, which applies this same verifier to each native platform's artifacts.
"""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("native_ci", HERE / "run.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class MultiVersionGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wf-version-gate-")
        self.addCleanup(self.temporary.cleanup)
        # macOS maps /var to /private/var. Match the real runner's canonical
        # CLI argv and verifier paths instead of trusting the temporary alias.
        self.root = Path(self.temporary.name).resolve()
        self.binary = self.root / "argo-workflows-ls"
        self.binary.write_bytes(b"synthetic test only; not a real LSP")
        self.output = self.root / "wf-multi-version"
        lock = json.loads((ci.ROOT / "compat/catalog.lock.json").read_text())
        self.versions = [row["version"] for row in lock["versions"]]
        self.kinds = {row["version"]: [source["kind"] for source in row["sources"]]
                      for row in lock["versions"]}
        self.fingerprint = {"binarySha256": ci.sha(self.binary),
                            "testSha256": ci.sha(ci.E2E / "wf-multi-version.py"),
                            "catalogSha256": ci.sha(ci.ROOT / "compat/catalog.lock.json")}
        for index in range(ci.MULTI_VERSION_SHARDS):
            expected = self.versions[index::ci.MULTI_VERSION_SHARDS]
            destination = self.shard(index)
            results = [{"case": name, "pass": True, "assertions": {"valid": True}}
                       for name in ("release-provenance", "machine-identity", "every-release-consumer")]
            results[0]["assertions"] = {f"provenance-{i}": True for i in range(447)}
            results[1]["assertions"] = {f"identity-{i}": True for i in range(5)}
            results[2]["assertions"] = {"exactShardOrder": True, "everyReleaseAccepted": True}
            if index == ci.MULTI_VERSION_SHARDS - 1:
                results += [{"case": f"other-semantic-{i}", "pass": True,
                             "assertions": {"valid": True}} for i in range(72)]
                results += [{"case": "all-kinds-automatic-session", "pass": True,
                             "assertions": {"valid": True}},
                            {"case": "unavailable-WorkflowTaskSet-hover", "pass": True,
                             "assertions": {"valid": True}}]
            write(destination / "results.json", results)
            write(destination / "manifest.json", self.fingerprint | {
                "releaseShard": f"{index}/{ci.MULTI_VERSION_SHARDS}",
                "selectedVersions": expected, "totalReleaseCount": 146,
                "releaseCount": len(expected), "resourceVersionPairs": sum(len(self.kinds[v]) for v in expected),
                "celRulesExercised": 10 if index == ci.MULTI_VERSION_SHARDS - 1 else 0})
            releases = []
            for version in expected:
                evidence = destination / f"release-{version}"
                write(evidence / "check.command.json", {
                    "argv": [str(self.binary), "check", "--root", str((evidence / "input").resolve()),
                             "--format", "json"], "exitCode": 0, "timedOut": False})
                write(evidence / "check.stdout", {
                    "analysisComplete": True, "notEvaluated": [], "configurationErrors": [],
                    "diagnostics": [], "filesAnalyzed": 1, "resourcesAnalyzed": len(self.kinds[version]),
                    "targets": [{"version": version, "experimental": False}],
                    "elapsedMilliseconds": 1400})
                releases.append({"version": version, "kinds": self.kinds[version],
                                 "checks": {f"semantic-{n}": True for n in range(10)},
                                 "pass": True, "fixtureSeconds": 0.001,
                                 "cliWallSeconds": 1.6, "analysisMilliseconds": 1400,
                                 "processAndEvidenceOverheadSeconds": 0.2})
            write(destination / "releases.json", releases)

    def shard(self, index):
        return self.output / f"shard-{index}"

    def mutate(self, index, name, update):
        path = self.shard(index) / name
        data = json.loads(path.read_text())
        update(data)
        write(path, data)

    def reject(self, pattern):
        with self.assertRaisesRegex((ValueError, OSError), pattern):
            ci.verify_multi_version_shards(self.output, self.binary)

    def test_complete_matrix_and_actual_per_version_metrics(self):
        result = ci.verify_multi_version_shards(self.output, self.binary)
        self.assertEqual(result["releaseCount"], 146)
        self.assertEqual(result["resourceVersionPairs"], 1052)
        performance = json.loads((self.output / "performance.json").read_text())
        self.assertEqual(len(performance["perVersion"]), 146)
        self.assertEqual(performance["analysisMilliseconds"]["p95"], 1400)

    def test_missing_release_is_not_accepted(self):
        self.mutate(1, "releases.json", lambda rows: rows.pop())
        self.reject("missing, duplicated or reordered releases")

    def test_duplicate_release_is_not_accepted(self):
        self.mutate(1, "releases.json", lambda rows: rows.__setitem__(1, copy.deepcopy(rows[0])))
        self.reject("missing, duplicated or reordered releases")

    def test_semantic_failure_cannot_be_relabelled_as_success(self):
        self.mutate(2, "releases.json", lambda rows: rows[0]["checks"].update({"exactTarget": False}))
        self.reject("invalid semantic assertions")

    def test_truthy_string_is_not_a_pass(self):
        self.mutate(0, "releases.json", lambda rows: rows[0].update({"pass": "true"}))
        self.reject("failed semantic row")

    def test_missing_cli_evidence_is_not_a_pass(self):
        version = self.versions[0]
        (self.shard(0) / f"release-{version}" / "check.stdout").unlink()
        with self.assertRaises(FileNotFoundError):
            ci.verify_multi_version_shards(self.output, self.binary)

    def test_forged_cli_report_is_not_a_pass(self):
        version = self.versions[0]
        self.mutate(0, f"release-{version}/check.stdout",
                    lambda data: data["targets"][0].update({"version": "4.1.6"}))
        self.reject("CLI evidence contradicts")

    def test_missing_nonmatrix_assertions_is_not_a_pass(self):
        self.mutate(ci.MULTI_VERSION_SHARDS - 1, "results.json", lambda rows: rows.__delitem__(slice(3, None)))
        self.reject("omitted non-release CLI/LSP assertions")

    def test_missing_shard_is_not_a_pass(self):
        (self.shard(2) / "manifest.json").unlink()
        with self.assertRaises(FileNotFoundError):
            ci.verify_multi_version_shards(self.output, self.binary)

    def test_bad_timing_is_not_a_pass(self):
        self.mutate(2, "releases.json", lambda rows: rows[0].update({"analysisMilliseconds": 5001}))
        self.reject("analysis or process deadline regression")

    def test_weakened_provenance_is_not_a_pass(self):
        self.mutate(0, "results.json", lambda rows: rows[0].update({"assertions": {"fake": True}}))
        self.reject("assertions were weakened")

    def test_source_or_binary_mismatch_is_not_a_pass(self):
        self.mutate(2, "manifest.json", lambda record: record.update({"binarySha256": "0" * 64}))
        self.reject("mismatched binarySha256")


if __name__ == "__main__":
    unittest.main()
