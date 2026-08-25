# BM-08 finding: frozen failure identities were never checked for stability

**No provider call was made. Additional spend $0.00.** The paid run stopped in
preflight, before the first request.

## What happened

The BM-08-v3 corpus was frozen with 14 cases across 10 repositories, clearing
the predeclared minimum of 12/10. The paid run's mandatory all-case preflight
then refused to start: two cases could not reproduce their frozen failure
identity.

```
faker-5128ae64      tests/test_proxy.py::TestFakerProxyClass::test_seed_class_locales
frozen : assert 'Hansh Ravel' == 'Jacqueline Martineau'
run 0  : assert 'Hansh Ravel' == 'Jacqueline Martineau'
run 1  : assert 'Hansh Ravel' == 'Rosie Smith'
run 2  : assert 'Paulette-Chr...lle Couturier' == 'Rosie Smith'

pathspec-b70e3fb4   tests/test_02_gitignore_basic.py::GitIgnoreBasicPatternTest::test_16_repr_str
frozen : '<pathspec.patterns.gitignore.basic.GitIg[38 chars]d00>' != "GitIgnore...
run 0  : '<pathspec.patterns.gitignore.basic.GitIg[38 chars]380>' != "GitIgnore...
run 1  : '<pathspec.patterns.gitignore.basic.GitIg[38 chars]880>' != "GitIgnore...
run 2  : '<pathspec.patterns.gitignore.basic.GitIg[38 chars]380>' != "GitIgnore...
```

Two different mechanisms, which is what makes this general rather than a quirk.
`faker` embeds randomly generated names in the assertion; `pathspec` embeds a
**memory address**, different in every process.

## The actual defect is upstream

The model-free validation contract captured a failure identity and treated
capture as proof. **One observation cannot establish reproducibility.** Both
cases were admitted on evidence that could not support them, and every later
stage — curation, the repository cap, the threshold check, the corpus freeze,
the manifest build — inherited the assumption without re-testing it.

Only the paid run's preflight, which compares a *fresh* observation against the
frozen one, could expose it. Two of fourteen cases, 14%, were affected. Had
all-case preflight not been mandatory before spend, this would have surfaced
mid-run with money already committed.

## Consequence

```
14 cases / 10 repositories   frozen corpus
-2 cases / -2 repositories   faker and pathspec are each their repository's only case
12 cases /  8 repositories   executable remainder

frozen minimum: >=12 cases AND >=10 repositories   ->  CORPUS_SHORTFALL
```

Cases clear their bar; repositories miss by two. The conditions are conjunctive.

## What was NOT done

- neither case patched
- memory addresses and random values **not** normalised out of signatures
- no replacement cases substituted
- the 12/10 minimum **not** lowered
- signature comparison **not** weakened

Each would make the benchmark pass without making it true.

## The remedy — adopted as BM-08-v4

Amend the validation contract: observe each candidate's failure identity **three
times in separate fresh processes** and require exact governed identity equality,
rejecting unstable signatures as `unstable_failure_identity`. Requires
revalidating the full frozen post-dedupe queue; the two defective cases drop and
others may prove unstable too. See `AMENDMENT-V4.md`.

The process boundary is load-bearing: a memory address is stable *within* one
interpreter and varies *between* interpreters, so three in-process observations
would have agreed and certified both defective cases as reproducible.

## Also fixed this pass (infrastructure)

The first paid attempt failed preflight on 9 additional cases with `clone
failed`. `materialise_baseline` took a single repository root while the v3
population spans two, so every `/repos-v3` repository failed to materialise. The
driver now consults the extra root only when the primary lacks the repository,
so a single-root population behaves exactly as before.

```
driver_hash    208244693ec8cf3e11d8f9cb01ded3cce0200a59c1a53ecfd2c3b968c34575e1
manifest_hash  6a9d70ba148b40bec5991ea7c4a3ecd5b9c124f5ae79c606d6f896148869a088
```

After that fix, 12 of 14 cases preflight clean. The remaining two are the
corpus defect above, not infrastructure.

## Product note — recorded, not actioned

> Exact `verify --expect-signature` style enforcement can be brittle when test
> failure text contains process-volatile values such as object memory addresses
> or random generated values.

A future product consideration only. `src/riftagent` is unchanged: no change to
failure-signature representation, verify semantics or normalisation. RIFT remains
frozen for BM-08.
