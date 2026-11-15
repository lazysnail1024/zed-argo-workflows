#!/usr/bin/env python3
"""Validate reviewed semantic provenance without fetching or changing sources.

Default validation binds the reviewed release/commit inventory independently of
generated CRD and branch metadata. --source-dir verifies optional exact upstream
files named <sourceID>.go.txt, including AST-recorded function byte slices.
--baseline reports semantic/source provenance changes separately from CRDs.
Neither mode runs upstream controllers or upgrades boundary samples to an audit
of all catalog releases. All output is JSON; invalid evidence exits nonzero.
"""

import argparse
import ast
import hashlib
import json
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
HEX256 = re.compile(r"[0-9a-f]{64}\Z")
HEX160 = re.compile(r"[0-9a-f]{40}\Z")
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
ID = re.compile(r"[a-z0-9][a-z0-9.-]*\Z")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_json(path):
    return json.loads(path.read_bytes())


def version(text):
    if not isinstance(text, str) or not VERSION.fullmatch(text):
        raise ValueError(f"invalid exact release version: {text!r}")
    return tuple(map(int, text.split(".")))


def changed(old, new, key):
    before = {row[key]: row for row in old}
    after = {row[key]: row for row in new}
    return {"added": sorted(after.keys() - before.keys()),
            "removed": sorted(before.keys() - after.keys()),
            "changed": sorted(k for k in before.keys() & after.keys() if before[k] != after[k])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path, default=ROOT / "compat/modeled-rules.json")
    parser.add_argument("--catalog", type=pathlib.Path, default=ROOT / "compat/catalog.lock.json")
    parser.add_argument("--scenario-file", type=pathlib.Path, default=ROOT / "tests/e2e/wf-compatibility.py")
    parser.add_argument("--source-dir", type=pathlib.Path)
    parser.add_argument("--baseline", type=pathlib.Path)
    args = parser.parse_args()
    errors = []
    report = {"schemaVersion": 1, "kind": "modeled-rule-provenance-check", "networkUsed": False,
              "runtimeExecutionProved": False, "allCatalogRuntimeSourcesAudited": False}

    def check(condition, message):
        if not condition:
            errors.append(message)

    try:
        manifest = read_json(args.manifest)
        catalog = read_json(args.catalog)
        releases = catalog["versions"]
        pins = [[row["version"], row["upstreamCommit"]] for row in releases]
        pin_index = dict(pins)
        check(len(pin_index) == len(pins), "catalog contains duplicate release versions")
        for release, commit in pins:
            version(release)
            check(isinstance(commit, str) and HEX160.fullmatch(commit), f"invalid catalog commit for {release}")
        pin_hash = digest(pins)
        coverage = manifest["coverage"]
        check(manifest.get("schemaVersion") == 1 and manifest.get("product") == "argo-workflows", "unsupported provenance schema or product")
        check(coverage.get("kind") == "boundary-samples", "coverage must retain the boundary-samples limitation")
        check(coverage.get("allCatalogRuntimeSourcesAudited") is False, "boundary evidence cannot claim all catalog runtime sources audited")
        check(coverage.get("upstreamRuntimeExecutionProved") is False, "source evidence cannot claim upstream runtime execution")
        check(coverage.get("catalogVersionCount") == len(releases), "reviewed catalog release count changed")
        check(coverage.get("catalogPinSetSHA256") == pin_hash, "reviewed release/commit inventory differs: review source provenance before accepting new pins")

        sources = manifest["sources"]
        source_index = {row["id"]: row for row in sources}
        check(len(sources) == len(source_index), "duplicate source IDs")
        blobs = {}
        for row in sources:
            sid = row["id"]
            check(isinstance(sid, str) and ID.fullmatch(sid), f"unsafe source ID {sid!r}")
            check(row["repository"] == "argoproj/argo-workflows", f"unexpected source repository for {sid}")
            check(row["version"] in pin_index and pin_index.get(row["version"]) == row["commit"], f"source commit is not the catalog pin: {sid}")
            check(HEX256.fullmatch(row["sha256"]) and HEX160.fullmatch(row["gitBlobSHA1"]), f"invalid source digest: {sid}")
            check(type(row["bytes"]) is int and row["bytes"] > 0, f"invalid source size: {sid}")
            expected_url = "https://github.com/" + row["repository"] + "/blob/" + row["commit"] + "/" + row["upstreamPath"]
            check(row["url"] == expected_url, f"source URL is not pinned: {sid}")
            if args.source_dir is not None and ID.fullmatch(sid):
                blob = (args.source_dir / (sid + ".go.txt")).read_bytes()
                blobs[sid] = blob
                check(len(blob) == row["bytes"], f"source size mismatch: {sid}")
                check(hashlib.sha256(blob).hexdigest() == row["sha256"], f"source SHA256 mismatch: {sid}")
                check(hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest() == row["gitBlobSHA1"], f"Git blob mismatch: {sid}")

        scenario_rules = None
        for node in ast.parse(args.scenario_file.read_text()).body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "RULES" for target in node.targets):
                scenario_rules = ast.literal_eval(node.value)
        check(isinstance(scenario_rules, list) and all(isinstance(rule, str) for rule in scenario_rules), "behavior scenario RULES must be a literal string list")
        rules = manifest["rules"]
        rule_ids = [row["id"] for row in rules]
        check(len(rule_ids) == len(set(rule_ids)), "duplicate modeled rule IDs")
        check(isinstance(scenario_rules, list) and set(rule_ids) == set(scenario_rules), "modeled rule IDs do not match independently named boundary scenarios")
        used_sources = set()
        function_count = 0

        def function(source_id, item):
            nonlocal function_count
            function_count += 1
            used_sources.add(source_id)
            check(source_id in source_index, f"unknown function source: {source_id}")
            check(isinstance(item["function"], str) and bool(item["function"]), f"missing function name: {source_id}")
            check(bool(HEX256.fullmatch(item["sha256"])), f"invalid function hash: {source_id}/{item['function']}")
            start, end = item["startByte"], item["endByte"]
            first, last = item["startLine"], item["endLine"]
            check(type(start) is int and type(end) is int and 0 <= start < end <= source_index[source_id]["bytes"], f"invalid function byte range: {source_id}/{item['function']}")
            check(type(first) is int and type(last) is int and 0 < first <= last, f"invalid function line range: {source_id}/{item['function']}")
            if source_id in blobs:
                blob = blobs[source_id]
                declaration = blob[start:end]
                check(hashlib.sha256(declaration).hexdigest() == item["sha256"], f"function/caller SHA256 mismatch: {source_id}/{item['function']}")
                check(blob[:start].count(b"\n") + 1 == first and blob[:end - 1].count(b"\n") + 1 == last, f"function/caller line mismatch: {source_id}/{item['function']}")
                check(declaration.startswith(b"func ") and declaration.endswith(b"}") and re.search(rb"\b" + re.escape(item["function"].encode()) + rb"\s*\(", declaration.split(b"{", 1)[0]), f"function/caller declaration mismatch: {source_id}/{item['function']}")

        for rule in rules:
            rid = rule["id"]
            check(rule.get("behaviorScenarioIDs") == ["wf-compatibility/" + rid], f"missing independently named behavior scenario: {rid}")
            check(bool(rule.get("modelCondition")) and bool(rule.get("limitations")), f"missing condition/limitations: {rid}")
            ranges = [(version(row["minInclusive"]), version(row["maxExclusive"])) for row in rule["featureEnabledRanges"]]
            check(bool(ranges), f"missing feature ranges: {rid}")
            previous = None
            for lower, upper in ranges:
                check(lower < upper and (previous is None or previous <= lower), f"empty/overlapping/unsorted ranges: {rid}")
                check(any(lower <= version(release) < upper for release in pin_index), f"range contains no catalog release: {rid}")
                previous = upper
            for boundary in rule["boundaries"]:
                before, after = boundary["before"], boundary["after"]
                check(before in pin_index and after in pin_index, f"boundary release is outside catalog: {rid}")
                check(not any(a <= version(before) < z for a, z in ranges) and any(a <= version(after) < z for a, z in ranges), f"boundary does not cross modeled feature ranges: {rid}")
            for evidence in rule["evidence"]:
                for item in evidence["functions"]:
                    function(evidence["sourceID"], item)
                for item in evidence["callers"]:
                    function(item["sourceID"], item)
            if "constantEvidence" in rule:
                constant = rule["constantEvidence"]
                sid = constant["sourceID"]
                used_sources.add(sid)
                check(sid in source_index, f"unknown constant source: {rid}")
                if sid in blobs:
                    lines = blobs[sid].splitlines()
                    line = constant["line"]
                    check(type(line) is int and 0 < line <= len(lines), f"invalid constant source line: {rid}")
                    if type(line) is int and 0 < line <= len(lines):
                        declaration = lines[line - 1].decode()
                        check(constant["name"] in declaration and json.dumps(constant["value"]) in declaration, f"constant source declaration differs: {rid}")
        check(used_sources == set(source_index), "source inventory and referenced evidence differ")
        report.update({"catalogVersionCount": len(releases), "catalogPinSetSHA256": pin_hash,
                       "catalogDeclaredSchemaIdentitySHA256": digest([[row["version"], row["schemaSha256"]] for row in releases]),
                       "modeledRulesSHA256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                       "semanticSourceSetSHA256": digest(sources), "ruleCount": len(rules),
                       "sourceFileCount": len(sources), "sampledReleaseCount": len({row["version"] for row in sources}),
                       "functionAndCallerCount": function_count, "exactLocalSourcesVerified": args.source_dir is not None})
        if args.baseline is not None:
            baseline = read_json(args.baseline)
            report["provenanceDiff"] = {"sources": changed(baseline["sources"], sources, "id"),
                                        "rules": changed(baseline["rules"], rules, "id"),
                                        "reviewedPinsChanged": baseline["coverage"]["catalogPinSetSHA256"] != pin_hash,
                                        "independentOfGeneratedCRDMetadata": True}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError) as error:
        errors.append(f"invalid or unavailable provenance input: {error}")
    report["valid"] = not errors
    report["errors"] = errors
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
