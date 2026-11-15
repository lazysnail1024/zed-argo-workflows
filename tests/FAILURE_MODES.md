# Deterministic owner-boundary checks

## Analysis explanation proof lifetime

The executable `wf-analysis-status` consumer checks visible CLI/stdio results,
including read-only clients, incomplete references and stale source actions.
Two additional cases in `internal/lsp/request_consistency_test.go` freeze an
explanation immediately before transport publication, then accept a change to
another file in its selected scope. A no-cursor readiness result and an explanation
with a closed-file Rename refusal must both lose their old catalog proof, even
though the queried buffer itself has not changed.

A black-box client cannot force the worker/publication interleaving without a
product timing hook. Sleeping or making an input artificially large could pass
without hitting the race. These tests therefore use the real session event
acceptor and catalog proof validator with manually drained workers. They assert
delivery authority, not JSON wording, and supplement the executable consumer.

## Configuration changes after an edit worker finishes

The `wf-resource-rename` and `wf-reaudit-actions` executable consumers change
saved configuration without sending a watch/configuration event. They verify
that real versioned edits are refused, accepted overlays keep priority, and
restored configuration permits exact edits with correct applied bindings or
diagnostics. These cases exercise the original metadata-refresh gap.

`TestCompletedEditRechecksConfigurationAtDelivery` fixes a later interleaving:
the worker has already constructed its reply and recorded the configuration it
used, then the saved policy changes before owner validation. The old session
epoch and catalog proof remain valid because no event was accepted. The final
configuration check must independently refuse that result. The test also checks
overlay priority and refusal of an unavailable configuration buffer.

An external client cannot deterministically stop a completed worker before the
owner consumes its result without adding a production timing hook. The native
test isolates that proof handoff using an empty edit payload; the executable
consumers supply the actual Rename/Quick Fix application and byte oracles.
