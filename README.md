# riftagent Claude Code handoff v1.2.3 — revision 3

This archive is a self-contained implementation handoff, not a completed
riftagent binary.

## How to use it

1. Extract the archive into a new working directory.
2. Open that directory in Claude Code.
3. Send Claude Code this single instruction:

   > Read `CLAUDE.md` and every authoritative file it names completely. Start
   > with M0 only. Implement it, run its exit gate, write
   > `IMPLEMENTATION_STATUS.md`, and stop for my review. Do not begin M1 until
   > I explicitly approve continuation. Repeat the same review stop after M1a
   > (`rift verify`), M1, M1.5, and M2. Do not redesign the system, skip an exit
   > gate, or claim an unexecuted check.

Claude Code should automatically discover `CLAUDE.md`, but the explicit prompt
prevents it from treating the design as background reading.

## Contents

- `CLAUDE.md` — authoritative implementation instructions and guardrails.
- `riftagent_design_v1.2.3.md` — frozen product design with yield-aware
  benchmarks, observational/ungatable diagnosis scope, standalone verify-first,
  separated sandbox authority, and the v1.2.2 structural guarantees.
- `IMPLEMENTATION_PLAN.md` — concrete milestone scope and deliverables.
- `ACCEPTANCE_MATRIX.md` — minimum evidence required before each milestone may
  be called complete.
- `reference/rift_v2/` — unchanged RIFT v2/RIFT-Code prototype snapshot supplied
  as mechanism and evidence reference. M0 intentionally begins by correcting
  its known honesty/documentation debt.

## Intended stopping point

The implementation target is M2: `verify`, `fix`, `why`, `edit`, `build`, and
`resume` for Python/pytest repositories. M1a `verify` is the first shippable
slice and must pass its acceptance-authority benchmark before LLM-backed M1
work begins. M2.5 doc-scale building and all v2 candidates remain gated by
benchmark evidence.

Implementation is intentionally delivered one milestone at a time. The human
checkpoint applies the same cheap-review-before-expensive-work principle that
riftagent applies to feature specifications.
