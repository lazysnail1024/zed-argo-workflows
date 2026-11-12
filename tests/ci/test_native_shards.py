"""Synthetic receipt controls; these are not native execution or release proof."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("native_shards_under_test", HERE / "native_shards.py")
shards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shards)
NATIVE = shards.ci_module().NATIVE
SOURCE = "c" * 40
VERSION = "0.0.1-alpha.3"
FINGERPRINTS = {"runnerSha256": "1" * 64, "shardsSha256": "2" * 64,
                "transferSha256": "3" * 64, "catalogSha256": "4" * 64}


def write(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(shards.canonical(record))


class NativeShardGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="wf-native-lanes-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.inputs = self.root / "inputs"
        self.versions, self.pairs = shards.release_inventory()
        self.producers, self.lanes = {}, {}
        for target, replacement in (
                ("code_fingerprints", mock.Mock(return_value=FINGERPRINTS)),
                ("checkout_identity", mock.Mock(return_value=(SOURCE, VERSION))),
                ("ci_module", mock.Mock(return_value=SimpleNamespace(NATIVE=NATIVE, host_platform=lambda: "linux/amd64")))):
            patcher = mock.patch.object(shards, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        for platform in shards.PLATFORMS:
            producer = self.producer(platform)
            path = self.inputs / ("producer-" + platform.replace("/", "-")) / "producer.json"
            write(path, producer)
            self.producers[platform] = path
            for lane in range(shards.LANES):
                folder = self.inputs / ("lane-" + platform.replace("/", "-") + f"-{lane}")
                path = folder / "native-lane.json"
                write(path, self.report(producer, lane))
                self.lanes[platform, lane] = path

    def producer(self, platform):
        identity = {"product": "argo-workflows", "serverVersion": VERSION,
                    "sourceCommit": SOURCE, "buildMode": "release", "defaultTargetVersion": "auto",
                    "catalogHash": FINGERPRINTS["catalogSha256"], "analysisReady": True,
                    "dirty": False, "acceptedTargetVersions": self.versions}
        return {"schemaVersion": 1, "sourceCommit": SOURCE, "releaseVersion": VERSION,
                "platform": platform, "identity": identity,
                "binarySha256": shards.digest(platform.encode()), "buildManifestSha256": "5" * 64,
                "archiveSha256": "6" * 64, "receiptSha256": "7" * 64,
                "runnerSha256": FINGERPRINTS["runnerSha256"], "transferSha256": FINGERPRINTS["transferSha256"]}

    def command(self, name, binary):
        return {"name": name, "started": True, "timedOut": False, "exitCode": 0,
                "pass": True, "timeoutSeconds": 300, "seconds": 1.0,
                "command": ["python", str(shards.ROOT / "tests/e2e" / (name + ".py")),
                            binary, "/synthetic/evidence/" + name]}

    def report(self, producer, lane, binary=None):
        binary = binary or "/synthetic/" + producer["platform"] + "/server"
        names = shards.selected_tests(lane)
        record = {"schemaVersion": 1, "sourceCommit": SOURCE, "releaseVersion": VERSION,
                  "platform": producer["platform"], "lane": lane, "laneCount": shards.LANES,
                  "producerSha256": shards.digest(shards.canonical(producer)), "executedBinary": binary,
                  "completed": True, "pass": True, **FINGERPRINTS,
                  "consumerSha256": {name: shards.sha(shards.ROOT / "tests/e2e" / (name + ".py")) for name in names},
                  "tests": []}
        for key in ("binarySha256", "buildManifestSha256", "archiveSha256", "receiptSha256", "identity"):
            record[key] = copy.deepcopy(producer[key])
        for name in names:
            if name == "wf-multi-version":
                record["tests"].append(self.multi_version_row(binary))
                record["multiVersion"] = self.multi_version_proof(producer["binarySha256"])
                continue
            record["tests"].append(self.command(name, binary) | {"caseCount": 1, "resultsSha256": "a" * 64})
        return record

    def multi_version_row(self, binary):
        commands = []
        for index in range(shards.RELEASE_SHARDS):
            command = self.command("wf-multi-version", binary)
            command["command"] += ["--release-shard", f"{index}/{shards.RELEASE_SHARDS}"]
            commands.append(command)
        return {"name": "wf-multi-version", "pass": True, "caseCount": 86,
                "releaseCount": 146, "resourceVersionPairs": 1052, "seconds": 4.0, "shards": commands}

    def multi_version_proof(self, binary_sha):
        records = []
        for index in range(shards.RELEASE_SHARDS):
            selected = self.versions[index::shards.RELEASE_SHARDS]
            manifest = {"releaseShard": f"{index}/{shards.RELEASE_SHARDS}", "selectedVersions": selected,
                        "totalReleaseCount": 146, "releaseCount": len(selected),
                        "resourceVersionPairs": sum(self.pairs[version] for version in selected),
                        "binarySha256": binary_sha, "catalogSha256": FINGERPRINTS["catalogSha256"],
                        "testSha256": shards.sha(shards.ROOT / "tests/e2e/wf-multi-version.py"),
                        "celRulesExercised": 10 if index == shards.RELEASE_SHARDS - 1 else 0}
            raw = json.dumps(manifest, indent=2) + "\n"
            records.append({"index": index, "manifestSha256": shards.digest(raw.encode()), "manifestJson": raw})
        return {"versions": self.versions.copy(), "releaseCount": 146,
                "resourceVersionPairs": 1052, "shards": records}

    def mutate(self, path, update):
        record = json.loads(path.read_text())
        update(record)
        write(path, record)

    def reject(self, pattern):
        with self.assertRaisesRegex((ValueError, OSError), pattern):
            shards.verify_inputs(self.inputs, source_commit=SOURCE, version=VERSION)

    def lane(self, index=0):
        return self.lanes["linux/amd64", index]

    def test_fixed_partition_is_exact_disjoint_and_balanced_by_plan(self):
        zero, one = shards.selected_tests(0), shards.selected_tests(1)
        self.assertEqual((len(zero), len(one)), (17, 18))
        self.assertFalse(set(zero) & set(one))
        self.assertEqual(set(zero) | set(one), set(NATIVE))
        self.assertEqual(len(NATIVE), 35)
        self.assertIn("wf-multi-version", one)
        self.assertIn("wf-responsive-editing", zero)
        for invalid in (True, -1, 2, "0"):
            with self.subTest(lane=invalid), self.assertRaises(ValueError):
                shards.selected_tests(invalid)

    def test_all_five_platforms_two_lanes_and_exact_versions_pass(self):
        report = shards.verify_inputs(self.inputs, source_commit=SOURCE, version=VERSION)
        self.assertIs(report["pass"], True)
        self.assertEqual(report["laneCount"], 10)
        self.assertEqual(report["nativeSuitesPerPlatform"], 35)
        self.assertEqual(report["exactReleasesPerPlatform"], 146)
        self.assertEqual(report["resourceVersionPairsPerPlatform"], 1052)

    def test_missing_lane_is_rejected(self):
        self.lane().unlink()
        self.reject("missing native lane")

    def test_duplicate_lane_is_rejected(self):
        duplicate = self.inputs / "duplicate" / "native-lane.json"
        write(duplicate, json.loads(self.lane().read_text()))
        self.reject("duplicate")

    def test_missing_producer_is_rejected(self):
        self.producers["darwin/arm64"].unlink()
        self.reject("missing native producer")

    def test_duplicate_producer_is_rejected(self):
        write(self.inputs / "duplicate" / "producer.json", self.producer("linux/amd64"))
        self.reject("duplicate native producer")

    def test_unknown_platform_is_rejected(self):
        self.mutate(self.lane(), lambda record: record.update(platform="linux/riscv64"))
        self.reject("unknown native lane platform")

    def test_unknown_lane_is_rejected(self):
        self.mutate(self.lane(), lambda record: record.update(lane=2))
        self.reject("unknown native lane")

    def test_wrong_lane_source_version_and_fingerprints_are_rejected(self):
        path = self.lane()
        original = path.read_bytes()
        for key, value, pattern in (
                ("sourceCommit", "d" * 40, "source or version"),
                ("releaseVersion", "0.0.1-alpha.99", "source or version"),
                ("producerSha256", "b" * 64, "producer hash"),
                ("runnerSha256", "b" * 64, "code or catalog"),
                ("shardsSha256", "b" * 64, "code or catalog"),
                ("catalogSha256", "b" * 64, "code or catalog")):
            with self.subTest(field=key):
                path.write_bytes(original)
                self.mutate(path, lambda record: record.update({key: value}))
                self.reject(pattern)

    def test_producer_source_version_and_transfer_hash_are_checked(self):
        path = self.producers["linux/amd64"]
        original = path.read_bytes()
        for key, value, pattern in (
                ("sourceCommit", "d" * 40, "source or version"),
                ("releaseVersion", "0.0.1-alpha.99", "source or version"),
                ("transferSha256", "b" * 64, "transferSha256 mismatch")):
            with self.subTest(field=key):
                path.write_bytes(original)
                self.mutate(path, lambda record: record.update({key: value}))
                self.reject(pattern)

    def test_matching_wrong_binary_in_both_lanes_does_not_replace_producer(self):
        for lane in range(shards.LANES):
            self.mutate(self.lane(lane), lambda record: record.update(binarySha256="b" * 64))
        self.reject("producer binarySha256 mismatch")

    def test_deleted_suite_with_rewritten_claimed_expected_list_is_rejected(self):
        def omit(record):
            record["tests"].pop()
            record["expectedTests"] = [row["name"] for row in record["tests"]]
        self.mutate(self.lane(), omit)
        self.reject("missing, duplicated or unexpected native suite")

    def test_duplicate_suite_is_rejected(self):
        self.mutate(self.lane(), lambda record: record["tests"].__setitem__(1, copy.deepcopy(record["tests"][0])))
        self.reject("missing, duplicated or unexpected native suite")

    def test_top_level_pass_cannot_hide_command_failures_or_missing_results(self):
        path = self.lane()
        original = path.read_bytes()
        for field, value, pattern in (
                ("exitCode", 1, "failed or incomplete command"),
                ("started", False, "failed or incomplete command"),
                ("timedOut", True, "failed or incomplete command"),
                ("pass", "true", "failed native consumer"),
                ("caseCount", 0, "missing semantic results"),
                ("caseCount", True, "missing semantic results"),
                ("resultsSha256", "bad", "missing semantic results"),
                ("timeoutSeconds", 600, "consumer deadline differs")):
            with self.subTest(field=field):
                path.write_bytes(original)
                self.mutate(path, lambda record: record["tests"][0].update({field: value}))
                self.reject(pattern)

    def test_unfinished_lane_or_truthy_success_is_rejected(self):
        path = self.lane()
        original = path.read_bytes()
        for field, value in (("completed", False), ("completed", "true"), ("pass", "true")):
            with self.subTest(field=field, value=value):
                path.write_bytes(original)
                self.mutate(path, lambda record: record.update({field: value}))
                self.reject("failed or incomplete native lane")

    def test_wrong_consumer_fingerprint_and_executed_binary_are_rejected(self):
        path = self.lane()
        original = path.read_bytes()
        self.mutate(path, lambda record: record["consumerSha256"].update({record["tests"][0]["name"]: "b" * 64}))
        self.reject("native consumer hash mismatch")
        path.write_bytes(original)
        self.mutate(path, lambda record: record["tests"][0]["command"].__setitem__(2, "/wrong/binary"))
        self.reject("wrong executed consumer")

    def test_146_rows_with_duplicate_version_are_rejected(self):
        self.mutate(self.lane(1), lambda record: record["multiVersion"]["versions"].__setitem__(1, self.versions[0]))
        self.reject("duplicated or mismatched exact release")

    def test_multi_version_subshards_cannot_be_missing_or_duplicate(self):
        path = self.lane(1)
        original = path.read_bytes()
        self.mutate(path, lambda record: record["multiVersion"]["shards"].pop())
        self.reject("missing multi-version subshard")
        path.write_bytes(original)
        self.mutate(path, lambda record: record["multiVersion"]["shards"].__setitem__(1, copy.deepcopy(record["multiVersion"]["shards"][0])))
        self.reject("duplicated or reordered multi-version subshard")

    def test_multi_version_manifest_content_and_hash_are_both_checked(self):
        path = self.lane(1)
        original = path.read_bytes()
        self.mutate(path, lambda record: record["multiVersion"]["shards"][0].update(manifestSha256="b" * 64))
        self.reject("manifest hash mismatch")
        path.write_bytes(original)
        def replace_and_rehash(record):
            shard = record["multiVersion"]["shards"][0]
            manifest = json.loads(shard["manifestJson"])
            manifest["selectedVersions"][1] = manifest["selectedVersions"][0]
            shard["manifestJson"] = json.dumps(manifest)
            shard["manifestSha256"] = shards.digest(shard["manifestJson"].encode())
        self.mutate(path, replace_and_rehash)
        self.reject("wrong coverage or source")

    def test_failed_multi_version_child_cannot_be_hidden(self):
        def fail(record):
            row = next(row for row in record["tests"] if row["name"] == "wf-multi-version")
            row["shards"][0]["exitCode"] = 1
        self.mutate(self.lane(1), fail)
        self.reject("failed or incomplete command")

    def test_missing_non_matrix_semantic_case_count_is_rejected(self):
        def weaken(record):
            row = next(row for row in record["tests"] if row["name"] == "wf-multi-version")
            row["caseCount"] = 12
        self.mutate(self.lane(1), weaken)
        self.reject("semantic controls")

    def test_writer_binds_raw_producer_bytes_and_actual_binary_identity(self):
        binary = self.root / "server"
        binary.write_bytes(b"synthetic binary; never executed by this test")
        producer = self.producer("linux/amd64")
        producer["binarySha256"] = shards.sha(binary)
        producer_path = self.root / "writer-producer.json"
        producer_path.write_text(json.dumps(producer, separators=(",", ":")))
        rows = self.report(producer, 0, str(binary))["tests"]
        with mock.patch.object(shards, "binary_identity", return_value=producer["identity"]) as identity:
            result = shards.write_report(self.root / "writer", lane=0, producer=producer_path,
                                         rows=rows, binary=binary)
        identity.assert_called_once_with(binary)
        self.assertIs(result["pass"], True)
        self.assertEqual(result["producerSha256"], shards.sha(producer_path))
        self.assertTrue((self.root / "writer/native-lane.json").is_file())
        with mock.patch.object(shards, "binary_identity", return_value={"wrong": "identity"}), self.assertRaisesRegex(ValueError, "native identity"):
            shards.write_report(self.root / "wrong-writer", lane=0, producer=producer_path, rows=rows, binary=binary)

    def test_writer_retains_explicit_failure_instead_of_false_success(self):
        binary = self.root / "server"
        binary.write_bytes(b"synthetic binary")
        producer = self.producer("linux/amd64")
        producer["binarySha256"] = shards.sha(binary)
        rows = self.report(producer, 0, str(binary))["tests"]
        rows[0]["exitCode"] = 1
        with mock.patch.object(shards, "binary_identity", return_value=producer["identity"]):
            result = shards.write_report(self.root / "failed-writer", lane=0, producer=producer,
                                         rows=rows, binary=binary)
        self.assertIs(result["pass"], False)
        self.assertIn("failed or incomplete command", result["evidenceError"])

    def test_writer_keeps_raw_windows_manifest_hashes_and_full_version_coverage(self):
        binary = self.root / "server"
        binary.write_bytes(b"synthetic binary")
        producer = self.producer("linux/amd64")
        producer["binarySha256"] = shards.sha(binary)
        output = self.root / "version-writer"
        report = self.report(producer, 1, str(binary))
        for item in report["multiVersion"]["shards"]:
            folder = output / "wf-multi-version" / f"shard-{item['index']}"
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_bytes(item["manifestJson"].replace("\n", "\r\n").encode())
        with mock.patch.object(shards, "binary_identity", return_value=producer["identity"]):
            result = shards.write_report(output, lane=1, producer=producer, rows=report["tests"], binary=binary)
        self.assertIs(result["pass"], True)
        for item in result["multiVersion"]["shards"]:
            path = output / "wf-multi-version" / f"shard-{item['index']}" / "manifest.json"
            self.assertEqual(item["manifestSha256"], shards.sha(path))
            self.assertIn("\r\n", item["manifestJson"])

    def test_gate_rejects_different_checkout_even_with_consistent_receipts(self):
        with mock.patch.object(shards, "checkout_identity", return_value=("d" * 40, VERSION)):
            self.reject("checkout or version mismatch")


if __name__ == "__main__":
    unittest.main()
