# Argo Workflows language server — 0.0.1-alpha.4

This alpha adds configuration-free chart discovery and explicit Helm rendering while retaining automatic compatibility across Argo Workflows 3.0.0–4.1.6.

- Ordinary YAML in chart templates keeps normal diagnostics and editor queries. Helm source supports conservative definitions for local templates, literal templateRef targets and escaped Argo parameter tags, with original source coordinates.
- Dynamic keys, unknown target identities, namespaces and structural Helm actions do not fabricate source bindings. Template-name hover works with the schema's template-choice constraints.
- The **Argo Workflows: render and check saved chart** code action needs no project task file. It discovers the selected chart and scope, finds Helm on PATH, uses chart defaults and opens generated YAML. Collection and resolution never run tools; stale or unsaved input is refused.
- CLI `render-check --file path/to/template.yaml` uses the same bounded implementation. Helm 3 and 4 are checked explicitly; cancellation terminates Helm descendants. Rendering never runs on save.
- This release also includes the upstream bounded query, schema deadline, closed-file authority and CI evidence validation fixes merged since alpha.3.

Native executables and archives cover Linux amd64/arm64, macOS amd64/arm64 and Windows amd64, with license/source provenance and SHA256SUMS. The forthcoming independent Zed adapter `zed-v0.0.1-alpha.5` will pin native `v0.0.1-alpha.4`.

Use the Helm extension for Helm syntax; see the README for one-time language associations and known source-navigation limits. Separate visible roots remain separate indexes in Zed 1.28.1; open a common parent for cross-repository navigation. This remains an alpha, not a Zed extension-store publication.
