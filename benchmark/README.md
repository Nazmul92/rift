# M1a verify benchmark — directory layout

Every run this milestone produced is kept, including the two that were
discarded. A benchmark whose failed attempts disappear cannot be audited, and
the ability to re-roll a sample until it reads well is exactly the weakness the
frozen-manifest rule exists to remove.

```
verify_bench.py            the harness: build / run / report
frozen/                    the run the milestone reports
  manifest.json            cases + ground truth + manifest_hash, written BEFORE any arm runs
  patches/                 the exact diffs handed to both arms
  results.json             raw per-case records, stamped with the manifest hash
  report.txt               metrics, recomputed from results.json
build.log                  full stdout of the reported run
abandoned-freeze-1/        first freeze — abandoned, see below
run-1-invalidated/         second freeze — completed then invalidated, see below
```

`report` refuses to print if `results.json` was produced against a different
manifest hash, so a report can never describe a case set it did not run.

## abandoned-freeze-1

Covered four repositories where the acceptance row requires five, because one
project's suite needs plugins this environment lacks and yielded no cases. It
was abandoned after 4 of 22 cases had produced arm results. **Those results were
not inspected**; the log is preserved so that claim is checkable. The repository
list was then widened and the manifest re-frozen.

## run-1-invalidated

A complete run (24 cases, 6 repositories, 0 errored) invalidated by a defect in
the harness, not by its outcome: `derive_judge_weakening` built its diff with
`git diff`, which compares the working tree to the **index**. The index still
held the parent commit, so the derived diff silently carried the commit's test
patch too and could not apply. All four judge-weakening cases were therefore
malformed, and that entire known-bad class measured nothing.

Its numbers were seen before the re-run was decided. That is disclosed rather
than hidden: a reviewer weighing the reported run should know a previous one was
observed first, and both are here to compare.

## Known limitations of the harness

- **Case selection is not reproducible across runs.** Candidate commits are
  enumerated with `git log` from the repository's *current* checkout, which a
  previous run leaves detached at an arbitrary parent commit. Two runs therefore
  sample different cases. The fix is to check out the default branch before
  scanning; it was deliberately **not** applied after the reported run, because
  changing the instrument after measuring and not re-measuring is worse than the
  defect. Each individual run is still internally frozen and hashed.
- **No order-masked class from a real repository.** The class that actually
  discriminates between the two arms — a bad patch the suite accepts because of
  test-order dependence — did not occur in the sampled commits. It is covered
  instead by the controlled click-shaped fixture in
  `tests/test_gate_end_to_end.py::test_v07_semantically_inert_order_masked_patch_is_rejected`.
- **No regression class.** Arm C runs with no `--preserve` nodes declared, so a
  patch that fixes its target and breaks a neighbour would be accepted by both
  arms. This is a real gap in what the benchmark can show about preservation.
- **Arm S is protocol A** (apply, run the suite, judge by the target's own
  report line). The target's-own-file protocol is not run separately.
