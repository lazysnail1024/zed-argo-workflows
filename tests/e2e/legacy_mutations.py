#!/usr/bin/env python3
"""Strict, orthogonal replacements for historical ablate-* entry points.

Ordinary and synthetic-schema binaries have separate passing baselines. The
shared mutation gate owns compilation, fresh receipts and exact semantic kills.
Aliases retain historical commands without compiling the same guard twice in CI.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import tempfile

import mutation_support as support
from mutation_support import Mutation

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ASSET = "internal/assets/schemas/96631f9b6d683112ccd0dd0dd7c28b3a59310400e3b2550e8cadb0a370410ac7.json"
TOOLKIT_QUERY = "vendor/github.com/lazysnail1024/k8s-crd-toolkit/schemaquery/query.go"


def rename(name, file, changes, cases, assertion="exact_bytes"):
    return Mutation(name, "legacy", "ablate-local-rename.py", file, changes,
                    "wf-local-rename.py", tuple((case, assertion) for case in cases),
                    env={"WF_RENAME_CASES": ",".join(cases)})


LEGACY_MUTATIONS = (
    Mutation("audit-onexit", "legacy", "ablate-audit-gaps.py", "internal/analysis/localrename.go",
             (('b.callValue(task, "onExit", workflowValue{}, templates)', '// onExit binding omitted'),),
             "wf-audit-gaps.py", (("rename-dag-onexit", "all_static_references_updated"),)),
    Mutation("audit-field-scope-simulation", "legacy", "ablate-audit-gaps.py", "internal/lsp/edits.go",
             (('if fix.Code == "FIX_FIELD_NAME" && analysis.SafeScopePlan(ctx, w, d, fix.Plan) != nil {',
               'if false && fix.Code == "FIX_FIELD_NAME" && analysis.SafeScopePlan(ctx, w, d, fix.Plan) != nil {'),),
             "wf-audit-gaps.py", (("fieldfix-external", "unsafe_fix_withheld"),)),
    Mutation("audit-strict-index", "legacy", "ablate-audit-gaps.py", "internal/analysis/external.go",
             (('projected, err := project(ctx, d.Snapshot, doc, d.Target.Version)\n\t\t\tif err != nil {\n\t\t\t\tcomplete[d.Target.ID] = false',
               'projected, err := project(ctx, d.Snapshot, doc, d.Target.Version)\n\t\t\tif err != nil {\n\t\t\t\t// failed projection incorrectly leaves the scope complete'),),
             "wf-audit-gaps.py", (("scope-same-duplicate", "missing_reference_severity"),)),
    Mutation("audit-target-gate", "legacy", "ablate-audit-gaps.py", "internal/analysis/projection.go",
             (('if _, err := compat.SchemaFile(version, api, kind); err != nil {',
               'if _, err := compat.SchemaFile(version, api, kind); false && err != nil {'),),
             "wf-audit-gaps.py", (("unsupported-gvk", "caller_not_falsely_resolved"),)),
    Mutation("audit-alias-inputs", "legacy", "ablate-audit-gaps.py", "internal/analysis/references.go",
             (('func namedInputs(n workflowValue, kind string) map[string]bool {\n\tresult := map[string]bool{}',
               'func namedInputs(n workflowValue, kind string) map[string]bool {\n\tresult := map[string]bool{}\n\tif n.node.Kind() == yamlview.Alias { return result }'),),
             "wf-audit-gaps.py", (("alias-input", "no_false_input_scope_error"),)),
    Mutation("domain-visibility", "legacy", "ablate-domain-queries.py", "internal/analysis/domain_queries.go",
             (("if visible, constrained := b.visibility[span]; constrained && !visible[key] {\n\t\t\t\t\tcontinue",
               "if visible, constrained := b.visibility[span]; false && constrained && !visible[key] {\n\t\t\t\t\tcontinue"),),
             "wf-domain-boundaries.py", (("parallel-peer", "visibility"),)),
    Mutation("domain-recovery", "legacy", "ablate-domain-queries.py", "internal/analysis/localrename.go",
             (("if b.readOnly && b.ctx.Err() == nil {", "if false && b.readOnly && b.ctx.Err() == nil {"),
              ("if readOnly {\n\t\treturn b, ctx.Err()", "if readOnly {\n\t\treturn b, b.err")),
             "wf-domain-boundaries.py", (("unrelated-malformed-expression", "exactBinding"),)),
    Mutation("domain-overlap-identity", "legacy", "ablate-domain-queries.py", "internal/analysis/localrename.go",
             (("b.queryPositions[span][key] = true", "b.queryPositions[span] = map[renameKey]bool{key: true}"),),
             "wf-domain-overlap.py", (("overlap", "exactDefinitionCandidates"), ("workflow", "exactReferenceSetTrue"))),
    Mutation("domain-origin-cardinality", "legacy", "ablate-domain-queries.py", "internal/analysis/domain_queries.go",
             ((" || b.declarationCount[key] != len(b.declarations[key])", ""),
              (" || b.declarationCount[candidate] != 1", "")),
             "wf-domain-inexact-cardinality.py", (("local-completion", "uniqueBeforeOriginFilter"),
                                                  ("local-alias-completion", "uniqueBeforeOriginFilter"))),
    Mutation("external-definition-role", "legacy", "ablate-domain-repair.py", "internal/lsp/stdio.go",
             (('result = append(result, analysis.ExternalDefinitions(ctx, scopeWorkspace(w, d.Target.ID), string(d.Snapshot.URI()), pos)...)',
               'if len(result) == 0 { result = append(result, analysis.ExternalDefinitions(ctx, scopeWorkspace(w, d.Target.ID), string(d.Snapshot.URI()), pos)...) }'),),
             "wf-domain-repair.py", (("external-overlap", "0-textDocument/definition-completeResult"),),
             ("--cases=external-overlap",)),
    Mutation("scope-reference-uniqueness", "legacy", "ablate-domain-queries.py / ablate-domain-repair.py", "internal/analysis/external.go",
             (("if len(targets) != 1 {", "if false && len(targets) != 1 {"),),
             "wf-domain-ambiguous-references.py", (("useTrue", "noResolvedReferences"), ("declarationTrue", "noResolvedReferences"))),
    Mutation("external-inexact-role", "legacy", "ablate-domain-repair.py", "internal/analysis/external.go",
             (("if containsPosition(selection, uri, pos) {", "if false && containsPosition(selection, uri, pos) {"),),
             "wf-domain-repair.py", (("external-overlap-inexact-input", "1-textDocument/references-completeResult"),
                                     ("external-overlap-duplicate-input", "1-textDocument/references-completeResult")),
             ("--cases=external-overlap-inexact-input,external-overlap-duplicate-input",)),
    Mutation("external-template-role-cardinality", "legacy", "ablate-domain-role-cardinality.py", "internal/analysis/external.go",
             (('if query != nil && !query.definitions && explicitTemplate != "" && !strings.Contains(explicitTemplate, "{{") {',
               'if false && query != nil && !query.definitions && explicitTemplate != "" && !strings.Contains(explicitTemplate, "{{") {'),),
             "wf-domain-role-cardinality.py", (("duplicate-external-template", "overlapReferencesTrue"),
                                               ("encoded-duplicate-external-template", "overlapReferencesTrue"))),
    Mutation("external-resource-role-cardinality", "legacy", "ablate-domain-role-cardinality.py", "internal/analysis/external.go",
             (('if query != nil && !query.definitions && explicitTemplate != "" && !strings.Contains(explicitTemplate, "{{") {',
               'if false && query != nil && !query.definitions && explicitTemplate != "" && !strings.Contains(explicitTemplate, "{{") {'),),
             "wf-domain-role-resources.py", (("duplicate-external-resource", "overlapReferencesTrue"),)),
    Mutation("external-input-name-match", "legacy", "ablate-domain-role-cardinality.py", "internal/analysis/external.go",
             (('if queryMember(input, "name") == name {', 'if queryMember(input, "name") != nil {'),),
             "wf-domain-role-resources.py", (("duplicate-resource-missing-input", "overlapReferencesTrue"),)),
    Mutation("external-candidate-navigation", "legacy", "ablate-external-candidates.py", "internal/analysis/external.go",
             (('if query != nil && query.definitions && containsPosition(nodeLocation(caller.document.Snapshot, call), query.uri, query.position) {',
               'if false && query != nil && query.definitions && containsPosition(nodeLocation(caller.document.Snapshot, call), query.uri, query.position) {'),),
             "wf-external-candidates.py", (("resource-duplicates", "resourcedefinition"),
                                           ("template-duplicates", "entrydefinition"),
                                           ("encoded-resource-duplicate", "resourcedefinition"))),
    Mutation("external-candidate-reference-contamination", "legacy", "ablate-external-candidates.py", "internal/analysis/external.go",
             (('query := &externalQuery{offset: -1, references: true, uri: uri, position: pos}\n\t_, bindings, _, _ := external(ctx, w, query)',
               'query := &externalQuery{offset: -1, definitions: true, references: true, uri: uri, position: pos}\n\t_, bindings, _, _ := external(ctx, w, query)\n\tbindings = append(bindings, query.candidates...)'),),
             "wf-external-candidates.py", (("encoded-resource-duplicate", "resourcereferencesTrue"),
                                           ("encoded-template-duplicate", "entryreferencesTrue"))),
    rename("rename-environment-closure", "internal/analysis/localrename.go",
           (('if p[0] == "$env" {', 'if false && p[0] == "$env" {'),),
           ("env-static-refusal", "env-dynamic-refusal", "env-get-refusal", "env-shadow-refusal", "env-alias-refusal"), "rename_refused"),
    rename("rename-expression-scope", "internal/analysis/expressionreferences.go",
           (("if shadowed[node] {", "if false && shadowed[node] {"),),
           ("expression-shadowed-input", "expression-shadow-initializer")),
    rename("rename-step-self-hook", "internal/analysis/localrename.go",
           (('hookVisible[renameKey{i, "step", workflowString(workflowMember(step, "name"))}] = true', '// self hook visibility omitted'),),
           ("step-self-hook-declaration", "step-self-hook-reference")),
    rename("rename-expression-syntax", "internal/analysis/expressionreferences.go",
           (("tokens, err := expressionTokens(ctx, text)\n\tif err != nil {\n\t\treturn nil, err", "tokens, err := expressionTokens(ctx, text)\n\tif err != nil {\n\t\treturn memberReferences(text), nil"),
            ("tree, err := parser.Parse(text)\n\tif err != nil {\n\t\treturn nil, err", "tree, err := parser.Parse(text)\n\tif err != nil {\n\t\treturn memberReferences(text), nil")),
           ("malformed-expression-tail", "malformed-expression-parenthesis", "malformed-raw-expression"), "rename_refused"),
    rename("rename-expression-lexical-identity", "internal/analysis/expressionreferences.go",
           (("return refs, nil", "return memberReferences(text), nil"),),
           ("expression-escaped-literal", "expression-raw-literal", "expression-comment-preserved")),
    rename("rename-expression-rune-to-byte", "internal/analysis/expressionreferences.go",
           (("Start: offsets[loc.From], End: offsets[loc.To]", "Start: loc.From, End: loc.To"),),
           ("expression-unicode-before-member",)),
    rename("rename-dynamic-closure", "internal/analysis/localrename.go",
           (("if b.blocked[key] {", "if false && b.blocked[key] {"),),
           ("computed-index-target-refused", "dynamic-index"), "rename_refused"),
    rename("rename-call-input-binding", "internal/analysis/localrename.go",
           (('b.reference(callee, "inputs."+kind, member(arg, "name"), true)', '_ = arg'),),
           ("input-parameter-call-argument",)),
    rename("rename-depends-binding", "internal/analysis/localrename.go",
           (("b.taskNames(i, queryNode(depends), true)", "_ = depends"),), ("task-declaration",)),
    rename("rename-scope-simulation", "internal/lsp/edits.go",
           (("if err := analysis.SafeScopePlan(ctx, w, d, plan); err != nil {", "if err := analysis.SafeScopePlan(ctx, w, d, plan); false && err != nil {"),),
           ("incomplete-same-scope",), "rename_refused"),
    rename("rename-reference-set", "internal/analysis/localrename.go",
           (("if len(after.symbols[nextKey]) != len(b.symbols[key]) {", "if false && len(after.symbols[nextKey]) != len(b.symbols[key]) {"),),
           ("workflow-reserved-json",), "rename_refused"),
    Mutation("query-snapshot-bytes", "legacy", "ablate-query-facts.py", "internal/analysis/invocation.go",
             (('return snapshotIdentity{s.URI(), s.Revision(), s.Text()}', 'return snapshotIdentity{s.URI(), s.Revision(), ""}'),),
             "wf-query-fact-handoff.py", (("warm", "definitions"), ("repeat-fresh-context", "definitions"))),
    Mutation("quickfix-pin-document-boundary", "legacy", "ablate-quickfixes.py", "internal/analysis/pin.go",
             (("at = start", "at = len(text)"),), "wf-quickfixes.py",
             (("pin-root-document-end", "exact_pin_bytes"), ("pin-root-document-end-crlf", "exact_pin_bytes")),
             env={"WF_QUICKFIX_CASES": "pin-root-document-end,pin-root-document-end-crlf"}),
    Mutation("quickfix-new-scope-error", "legacy", "ablate-quickfixes.py", "internal/analysis/edits.go",
             (('if counts[key] < 0 {\n\t\t\t\treturn fmt.Errorf("edit introduces an error")',
               'if false && counts[key] < 0 {\n\t\t\t\treturn fmt.Errorf("edit introduces an error")'),
              ('if counts[key] < 0 {\n\t\t\t\treturn fmt.Errorf("edit introduces a scope error")',
               'if false && counts[key] < 0 {\n\t\t\t\treturn fmt.Errorf("edit introduces a scope error")'),),
             "wf-quickfixes.py", (("reference-local-candidates", "exact_candidate_bytes"), ("reference-quoted-crlf", "exact_candidate_bytes")),
             env={"WF_QUICKFIX_CASES": "reference-local-candidates,reference-quoted-crlf"}),
    Mutation("quickfix-single-literal", "legacy", "ablate-quickfixes.py", "internal/analysis/quickfixes.go",
             (('if n.Kind() != yamlview.Scalar || raw == "" || strings.ContainsAny(raw, "\\r\\n") || strings.ContainsAny(raw[:1], "|>&*!") {',
               'if n.Kind() != yamlview.Scalar || raw == "" {'),),
             "wf-quickfixes.py", (("reference-block-scalar", "exact_candidate_bytes"),),
             env={"WF_QUICKFIX_CASES": "enum-block-scalar,reference-block-scalar"}),
    Mutation("schema-exclusive-cardinality", "legacy", "ablate-schema-queries.py", TOOLKIT_QUERY,
             (("for i, branches := range [][]*jsonschema.Schema{s.AnyOf, s.OneOf}", "for _, branches := range [][]*jsonschema.Schema{s.AnyOf, s.OneOf}"),
              ("matches == 0 || i == 1 && matches != 1", "matches == 0")),
             "wf-schema-query-contract.py", tuple((case, "exactValues") for case in (
                 "object-exclusive", "object-duplicate-exclusive"))),
    Mutation("schema-marker-identity", "legacy", "ablate-schema-queries.py", "internal/analysis/query_context.go",
             ((" && m.Key.Span().Start == markerStart", ""), ("m.Value.Span().Start == markerStart && ", ""), ("n.Span().Start == markerStart && ", "")),
             "wf-schema-queries.py", (("existing-annotation-value-plain-client", "exactEnum"),)),
    Mutation("schema-flow-context", "legacy", "ablate-schema-query-boundaries.py", "internal/analysis/query_context.go",
             (("offset, flowDepth)", "offset, 0)"),), "wf-schema-query-boundaries.py",
             tuple((case, "exactBytes-0") for case in ("multiline-flow-sibling", "multiline-flow-sibling-crlf-comment", "multiline-flow-array-object"))
             + (("multiline-flow-closing-delimiters", "exactLabels"),), ("actual",)),
    Mutation("schema-key-closure", "legacy", "ablate-schema-query-boundaries.py", "internal/analysis/queries.go",
             (("if !allowed {", "if false && !allowed {"),), "wf-schema-query-boundaries.py",
             (("closedConjunction-keys", "exactLabels"), ("closedRefConjunction-keys", "exactLabels")), ("synthetic",)),
    Mutation("schema-independent-sibling", "legacy", "ablate-schema-query-sibling.py", TOOLKIT_QUERY,
             (("!w.inspect(s, depth) || s.Bool != nil && !*s.Bool", "!w.inspect(s, depth) || !w.compatible(s, path, value, depth)"),
              ("w.compatible(child, path, value, depth+1) && w.allowsKey", "w.allowsKey")),
             "wf-schema-query-sibling.py", (("unrelated-invalid-enum-key", "exactLabels"),)),
    Mutation("scope-prefix-budget", "legacy", "ablate-domain-scope.py", "internal/workspace/config.go",
             (("if !strings.HasPrefix(name, literal) {", "if false && !strings.HasPrefix(name, literal) {"),),
             "wf-domain-scope-budget.py", (("literal-prefix-work", "slow_path_budget"),)),
)

SYNTHETIC = {"schema-exclusive-cardinality", "schema-key-closure"}
ALIASES = {
    "ablate-audit-gaps.py": {"scope-completeness": ("rename-scope-simulation",), "onexit": ("audit-onexit",),
        "scope-simulation": ("audit-field-scope-simulation",), "strict-index": ("audit-strict-index",),
        "target-gate": ("audit-target-gate",), "alias-inputs": ("audit-alias-inputs",)},
    "ablate-domain-queries.py": {"visibility": ("domain-visibility",), "recovery": ("domain-recovery",),
        "lexical-shadowing": ("rename-expression-scope",), "ambiguous-union": ("scope-reference-uniqueness",),
        "overlap-identity": ("domain-overlap-identity",), "origin-cardinality": ("domain-origin-cardinality",)},
    "ablate-domain-repair.py": {"scalar-encoding": ("yaml-string-edit",), "external-definition-role": ("external-definition-role",),
        "cross-role-uniqueness": ("scope-reference-uniqueness",), "inexact-role": ("external-inexact-role",)},
    "ablate-domain-role-cardinality.py": {"early-role-accounting": ("external-template-role-cardinality", "external-resource-role-cardinality"),
        "input-name-match": ("external-input-name-match",)},
    "ablate-domain-scope.py": {"scope": ("external-completion-scope-reads",), "prefix": ("scope-prefix-budget",)},
    "ablate-external-candidates.py": {"candidate-navigation": ("external-candidate-navigation",),
        "candidate-reference-contamination": ("external-candidate-reference-contamination",)},
    "ablate-local-rename.py": {m.name.removeprefix("rename-"): (m.name,) for m in LEGACY_MUTATIONS if m.name.startswith("rename-")},
    "ablate-query-facts.py": {"snapshot-bytes": ("query-snapshot-bytes",)},
    "ablate-quickfixes.py": {"pin-document-boundary": ("quickfix-pin-document-boundary",), "new-error": ("quickfix-new-scope-error",),
        "stale-scope": ("stale-action",), "single-literal": ("quickfix-single-literal",)},
    "ablate-schema-queries.py": {"exclusive-cardinality": ("schema-exclusive-cardinality",), "marker-identity": ("schema-marker-identity",)},
    "ablate-schema-query-boundaries.py": {"flow-context": ("schema-flow-context",), "key-closure": ("schema-key-closure",)},
    "ablate-schema-query-sibling.py": {"sibling": ("schema-independent-sibling",)},
}
ALIASES["ablate-local-rename.py"]["expression-let-scope"] = ALIASES["ablate-local-rename.py"].pop("expression-scope")


def main(argv=None, entry=None):
    parser = argparse.ArgumentParser(description=__doc__)
    if entry is None:
        parser.add_argument("group", choices=("all", "legacy"))
    parser.add_argument("output", type=Path, help="fresh output directory")
    parser.add_argument("historical_variants", nargs="*", help="historical selectors (wrapper entry points only)")
    parser.add_argument("--variants", nargs="+")
    parser.add_argument("--tools", type=Path)
    parser.add_argument("--test-prefix-json", default="[]")
    parser.add_argument("--build-timeout", type=int, default=600)
    args = parser.parse_args(argv)
    if args.historical_variants and (not entry or args.variants):
        parser.error("positional variants require a historical wrapper and cannot accompany --variants")
    known = ALIASES[entry] if entry else {m.name: (m.name,) for m in LEGACY_MUTATIONS}
    selected = args.variants or args.historical_variants or list(known)
    if len(selected) != len(set(selected)):
        parser.error("duplicate variant selectors")
    unknown = set(selected) - set(known)
    if unknown:
        parser.error("unknown mutation(s): " + ", ".join(sorted(unknown)))
    names = list(dict.fromkeys(name for selector in selected for name in known[selector]))
    registry = support.MUTATIONS + LEGACY_MUTATIONS
    if any(m.name in names and m.suite == "wf-audit-gaps.py" for m in registry):
        if args.tools is None or not (args.tools / "argo").is_file():
            parser.error("--tools must contain the pinned argo binary for official lint oracles")
        registry = tuple(replace(m, env=m.env | {"WF_TEST_ARGO_BINARY": str((args.tools / "argo").resolve())})
                         if m.suite == "wf-audit-gaps.py" else m for m in registry)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"pass": False, "selected": names, "variants": [], "groups": {},
              "entry": entry, "driverSha256": support.digest(Path(__file__))}
    support.save(out / "results.json", report)
    original_root, original_registry = support.ROOT, support.MUTATIONS
    try:
        support.MUTATIONS = registry
        for fixture, group_names in (("ordinary", [n for n in names if n not in SYNTHETIC and n != "scope-prefix-budget"]),
                                     ("synthetic-schema", [n for n in names if n in SYNTHETIC]),
                                     ("glob-work-budget", [n for n in names if n == "scope-prefix-budget"])):
            if not group_names:
                continue
            with tempfile.TemporaryDirectory(prefix="wf-legacy-fixture-") as directory:
                if fixture != "ordinary":
                    source = Path(directory)
                    for folder in ("cmd", "internal", "compat", "schemas", "vendor"):
                        shutil.copytree(ROOT / folder, source / folder)
                    for filename in ("go.mod", "go.sum"):
                        shutil.copy2(ROOT / filename, source / filename)
                    if fixture == "synthetic-schema":
                        shutil.copy2(ROOT / "tests/fixtures/schema-query/contract.json", source / SCHEMA_ASSET)
                    else:
                        path = source / "internal/workspace/config.go"
                        original = path.read_text()
                        needle = "ok, _ := glob(p, name)"
                        if original.count(needle) != 1:
                            raise support.GateError("glob instrumentation must match once")
                        path.write_text(original.replace(needle, 'fmt.Fprintln(os.Stderr, "WF_TEST_GLOB_PARSE")\n\t\t' + needle, 1))
                    support.ROOT = source
                else:
                    support.ROOT = ROOT
                command = ["all", str(out / fixture), "--variants", *group_names,
                           "--test-prefix-json", args.test_prefix_json, "--build-timeout", str(args.build_timeout)]
                if args.tools:
                    command += ["--tools", str(args.tools.resolve())]
                code = support.main(command)
                evidence = json.loads((out / fixture / "results.json").read_text())
                report["groups"][fixture] = {"exitCode": code, "pass": evidence["pass"],
                    "results": str(out / fixture / "results.json"), "selected": group_names}
                if fixture == "synthetic-schema":
                    report["groups"][fixture]["fixtureSha256"] = support.digest(ROOT / "tests/fixtures/schema-query/contract.json")
                report["variants"].extend(evidence["variants"])
                support.save(out / "results.json", report)
        report["pass"] = ({row["variant"] for row in report["variants"]} == set(names)
                          and len(report["variants"]) == len(names)
                          and all(row["detected"] for row in report["variants"])
                          and all(group["pass"] for group in report["groups"].values()))
    except Exception as error:
        report["error"] = str(error)
    finally:
        support.ROOT, support.MUTATIONS = original_root, original_registry
        support.save(out / "results.json", report)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
