# `ncarnate convert --manifest` — Design (audit family, increment 2)

> **Status:** designed, **build in progress in this repo**. This is the build-authoritative
> copy (imported from the owner's internal planning records, master
> `an internal revision`, opus SIGN-OFF). The `IMPLEMENTATION_PLAN.md` at the repo root decomposes this
> doc; **re-read this doc every loop iteration** — the plan decomposes it, it does not
> redesign it.
> **Input:** [`ncarnate-audit.md`](ncarnate-audit.md) (the increment-1 design; it froze
> the record schema and sketched the consumption contract) and the current ncarnate code
> (`recompress`, the CLI pre-dispatch shim, the audit manifest writer). Codebase facts
> below were verified against ncarnate **`ad8deb2`** (audit increments 1–3 merged) on
> 2026-07-10, and **re-verified against current `main` `76734a4`** at build-setup time
> (only dev-artifact-strip commits landed since; `recompress`'s signature at
> [`core.py:45`](../../ncarnate/core.py) and `_guard_auto_destination` at
> [`core.py:175`](../../ncarnate/core.py) still match; `_sha256` at
> [`audit/__init__.py:184`](../../ncarnate/audit/__init__.py); the `convert` verb shim at
> [`cli.py` `main()`](../../ncarnate/cli.py); `SCHEMA_VERSION = 1`
> (`audit/models.py`), `RULESET_VERSION = 2` (`audit/codes.py`);
> fixtures `NETCDF_FIXTURES`/`HDFEOS2_FIXTURES`/`BLOCKER_FIXTURES`/`stage` in
> `tests/conftest.py`; the record schema in `tests/audit/record.schema.json`).
> **This is increment 2's own design pass** the increment-1 doc deferred: the flag
> surface and consumption mechanics for the converter reading the migration manifest. The
> record schema is **already frozen** and is *not* re-opened here.

## Context / problem

Increment 1 shipped `ncarnate audit`, which writes a versioned JSONL **migration
manifest** — one record per file with `root`+relative `path`, `sha256`, a predicted
`status`, named `issues`, and a `plan`. The manifest was explicitly designed to be
*executed*: the increment-1 doc states the file `ncarnate audit /archive --output
audit.jsonl` writes is "byte-for-byte what `ncarnate convert --manifest audit.jsonl
--status ready` consumes," and that consumption "re-verifies `sha256` before touching
data and refuses records where it is `null` unless explicitly overridden."

This increment builds that consumer: `ncarnate convert --manifest`. An operator audits a
terabyte archive once (cheap, read-only), reviews the readiness report, then runs the
converter against the *same manifest* to modernize exactly the files the audit blessed —
with a guarantee that what gets converted is bit-identical to what was audited. This is
the pivot from "what can ncarnate safely do?" (audit) to "do it" (convert), and it is the
intake format the later family members (`ncarnate-lake` orchestration, dashboards) plan
against.

## Goals & non-goals

**Goals**

1. `ncarnate convert --manifest <file.jsonl>`: read the manifest, and for each record
   whose status is selected, run the existing `recompress()` conversion against the
   audited file — **non-destructively by default**, into a mirrored output tree.
2. **Integrity by construction:** re-verify each file's `sha256` against its manifest
   record before touching it, so the converter acts on exactly the bytes the audit saw. A
   changed or unverifiable file is skipped, never silently converted.
3. **Agreement preserved for free:** the converter is the audit's own oracle — `recompress`
   runs the same fail-loud predicates the audit called, so a stale/wrong prediction raises
   and is reported, never produces a silent wrong conversion.
4. Compose cleanly with the existing encoding flags (`--complevel`, `--zlib`, `--shuffle`,
   `--geolocation`) and with the `convert` verb added in increment 1.

**Non-goals (scope fence)**

- No new record-schema fields, no new status/issue codes — the schema is frozen; this doc
  only *reads* it.
- No parallelism (`--jobs`) and no HTML/DuckDB reporting — deferred fast-follows, same as
  the audit's.
- No `--mode sample`/`preflight` re-audit inside convert; the manifest's predictions are
  taken as given (guarded by the sha256 gate + `recompress`'s own predicates).
- **No stdin (`--manifest -`)** — deferred (see KD9). Manifest input is a file path only in
  this increment.
- No network, ever. The only writes are the converted outputs (and, with the explicit
  `--in-place` opt-in, the sources) — never the manifest, never an audited source by
  default.

## Approach

### Invocation shape

```
ncarnate convert --manifest audit.jsonl --out-dir ./modern (--root DIR | --allow-manifest-root)
                 [--status ready] [--allow-unverified] [--in-place]
                 [--skip-existing] [--complevel 9] [--no-shuffle] [--no-geolocation]
```

> **Read base is operator-controlled (KD10, resolved post-audit 2026-07-11).** The
> containment base a source resolves under must **not** default to the manifest's own
> `record.root` (untrusted input — a crafted `root` redirects reads outside the archive,
> and the sha256 gate is no defense since the attacker also authors the recorded hash). So a
> manifest run requires **either `--root DIR`** (an operator-supplied base — e.g. the
> archive's current location if it moved since the audit; `record.root` is then ignored) **or
> `--allow-manifest-root`** (an explicit opt-in to trust the manifest's recorded `root`).
> With neither, the run is **refused** rather than silently trusting an attacker-controllable
> base — consistent with the rule never to auto-pick a security-critical default the operator
> didn't name.

`--manifest` is a **`convert`-only** flag. Increment 1 made `convert` an explicit alias
that strips the verb and falls through to the legacy flat parser; this increment gives
`convert` its **own argparse parser** (mirroring how `audit` got one), so `--manifest`,
`--status`, `--out-dir`, etc. live there and never pollute the bare `ncarnate <path>`
form. The convert parser still accepts the legacy positional `path...` form (so
`ncarnate convert granule.hdf --complevel 9` keeps working); `--manifest` and positional
paths are **mutually exclusive** (one or the other drives the run).

### The per-record loop

For each JSONL line (each a frozen v1 record):

1. **Compatibility check.** If `record.schema_version` ≠ the consumer's `SCHEMA_VERSION`
   → **hard refuse the whole run** (the record shape may differ). If `ruleset_version` ≠
   the consumer's `RULESET_VERSION` → **warn once** ("manifest produced under ruleset vN;
   classification semantics have changed — re-audit recommended") and proceed (safety
   still holds via steps 3–4).
2. **Status filter.** Skip the record unless its `status` ∈ `--status` (default: `ready`
   only). Blocker statuses (`unsupported`/`malformed`/`unsafe`/`unknown`) carry
   `plan: null` and are never actionable — skipped with a counted reason even if named.
3. **Integrity gate (the load-bearing safety property).** Resolve the source path as
   `base / record.path`, where `base` is **`--root` if given, else `record.root` only when
   `--allow-manifest-root` is set** — with neither, the run is refused up front (KD10: the
   base must be operator-controlled, never a default-trusted manifest field). Recompute its
   `sha256` (chunked) and require
   it equals `record.sha256`. A `null` recorded hash → **refuse** the record unless
   `--allow-unverified` (the audit was run without `--checksum sha256`). A **mismatch** →
   skip-with-error: the file changed since the audit, so its prediction is stale and must
   not be trusted.
4. **Convert.** Resolve the output path (below) and call the existing
   `recompress(src, dst=out_path, zlib=…, shuffle=…, complevel=…, geolocation=…)`.
   `recompress` self-dispatches by magic-byte format — HDF4/HDF-EOS2 → conversion,
   netCDF/HDF5 → recompression — and runs its own fail-loud predicates, so agreement with
   the audit is automatic: a record the audit called `ready` that `recompress` cannot
   actually convert **raises**, and is reported as a failure rather than mis-converted.

Per-record failures are **isolated** (one bad file does not abort the run — same
discipline as the audit scan); a summary of converted / skipped / failed with reasons is
printed at the end, and the exit code is non-zero iff any *selected* record failed.

### Output destination (non-destructive by default)

An archive-scale convert must **not** mutate the source archive by default — that is the
institutional deal-breaker the whole family is built around. So:

- **`--out-dir DIR` is required** in manifest mode. The output mirrors the audit's
  `root`-relative tree: output = `DIR / record.path`, with an HDF4/HDF-EOS2 source's
  extension swapped to `.nc` (a conversion) and a netCDF source's name kept (a
  recompressed copy). **`convert_manifest` computes this output path itself** — reading
  `record.format` for the HDF4→`.nc` swap (safe: the sha256 gate has already confirmed the
  bytes) — and passes it to `recompress` as an explicit `dst`, which `recompress` uses
  *verbatim* (it does not auto-swap extensions when `dst` is given). Intermediate
  directories are created as needed. Because `dst` is passed explicitly, `recompress`
  leaves every source untouched.

  > **This revises the increment-1 illustrative command.** The increment-1 doc's forward
  > reference (`ncarnate convert --manifest audit.jsonl --status ready`) was an
  > illustration of *manifest consumption*, not a frozen CLI contract — only the manifest
  > *content* is byte-for-byte frozen. Increment 2 finalizes the flag surface, and the
  > non-destructive-by-default thesis requires an explicit destination, so the real minimal
  > command is `ncarnate convert --manifest audit.jsonl --out-dir DIR` (`--status` defaults
  > to `ready`). A magic default output dir was rejected — an archive-scale tool should
  > never auto-create a destination the operator didn't name.
- **`--in-place`** is a separate, explicit opt-in that omits `dst` and lets `recompress`
  overwrite netCDF sources in place (after its verify-lossless step) and write HDF4
  conversions beside the source. Dangerous on an archive; documented as such; never the
  default. **Caveat:** for an HDF4 source in `--in-place` mode, `recompress` auto-derives
  `<stem>.nc` and *refuses (raises) if that output already exists* (`core._guard_auto_destination`),
  so a re-run over already-converted HDF4 files hard-fails per file — resumability is
  therefore an **`--out-dir`-mode-only** guarantee.
- **`--skip-existing`** (out-dir mode) skips any record whose computed output path already
  exists, making a terabyte-scale run resumable after an interruption without re-doing
  work. It relies on the predictable mirrored output path, so it is scoped to `--out-dir`
  mode (see the `--in-place` caveat above).

> **Flag coexistence with the legacy positional form.** The `convert` verb keeps accepting
> the legacy positional `path...` form, which retains its current behavior — including
> `--overwrite` defaulting **on** (in-place). So the same verb is in-place-by-default for
> positional paths but non-destructive-by-default for `--manifest`. This asymmetry is a
> deliberate backward-compatibility concession (the positional form's contract is
> unchanged from today), while the *new* manifest path adopts the family's safety-first
> default. `--overwrite`/`--no-overwrite` govern the positional form; `--in-place` is the
> manifest-mode analog — they are documented as the two families so an operator isn't
> surprised.

### Per-status conversion parameters

The encoding flags apply uniformly to every output, with one status-driven override:

| status | operation (via `recompress`) | geolocation |
|---|---|---|
| `ready` | convert / recompress (by format) | honors `--geolocation` (default on) |
| `ready_no_geolocation` | convert SDS-only | **forced off** (the audit predicted geolocation is unsupported) |
| `already_modern` | recompress | n/a |

`--status` defaults to `ready` only; an operator widens it explicitly (e.g.
`--status ready,ready_no_geolocation,already_modern`) once they've read the audit report
and accepted the SDS-only caveat for the no-geolocation set.

### Python API

```python
from ncarnate.convert import convert_manifest, ConvertOptions
result = convert_manifest("audit.jsonl",
                          ConvertOptions(out_dir="./modern", statuses={"ready"}))
result.converted; result.skipped; result.failed   # per-file records + reasons
```

`convert_manifest` is the one public entry; the CLI is a thin shell over it (same shape as
`audit_path`). Lives in a new `ncarnate/convert/` subpackage (KD7), importing `recompress`,
the manifest models, and the audit's `_sha256` helper (promoted to a shared util).

## Key decisions

1. **`convert` gets its own parser; `--manifest` is convert-only.** Keeps the bare
   `ncarnate <path>` legacy form pristine while giving the verb a real flag surface.
   Mirrors the `audit` sub-parser precedent. `--manifest` xor positional paths.
2. **The sha256 re-verify gate is mandatory by default.** Executing a manifest means
   acting on the exact bytes that were audited; a `null` hash is refused
   (`--allow-unverified` to override for trusted/immutable trees), a mismatch is
   skipped-with-error. This is the increment-1 promise made mechanical.
3. **Non-destructive by default: `--out-dir` required, `--in-place` opt-in.** The archive
   is never mutated unless the operator explicitly asks. `--out-dir` also gives a clean
   mirrored tree and makes `--skip-existing` resumability trivial.
4. **Drive from `status`, not from `plan.operation`; let `recompress` self-dispatch.**
   The converter re-runs its own predicates, so agreement is automatic and a stale
   manifest cannot cause a silent wrong conversion — it can only cause a *reported
   failure*. `plan` stays advisory (informational), not trusted control flow. This keeps
   exactly one source of truth for conversion rules (the converter), the same principle
   the audit's classification rests on.
5. **Schema mismatch refuses; ruleset mismatch warns.** A different record *shape* is
   unsafe to consume; a different *classification ruleset* only means the predictions may
   be stale, which steps 3–4 already defend against — so warn-and-proceed, don't refuse.
6. **Blocker records are never actionable.** They carry `plan: null`; naming them in
   `--status` skips them with a counted reason rather than attempting a doomed convert.
7. **Module home = `ncarnate/convert/` subpackage.** *(Resolved from Open questions at
   build start.)* Symmetric with `ncarnate/audit/` (`__init__.py` = public
   `convert_manifest` + CLI `main`; `models.py` = `ConvertOptions`/result dataclasses;
   a manifest reader module). The single-file `ncarnate/manifest.py` alternative was
   rejected: the surface (reader + integrity gate + path-containment + convert loop + CLI +
   models) is large enough that the subpackage's separation-of-concerns matches the audit
   precedent and keeps modules focused. The audit's `_sha256` is promoted to a shared util
   both subpackages import (do **not** duplicate it).
8. **`--status` default = `ready` only.** *(Resolved from Open questions — confirmed.)* The
   operator has *read the audit report* and opts into the caveated sets (`ready_no_geolocation`
   SDS-only, `already_modern`) deliberately, e.g. `--status ready,ready_no_geolocation`. The
   conservative default never converts a status the operator didn't name.
9. **Manifest input is a file path only; `--manifest -` (stdin) is deferred.** *(Resolved
   from Open questions — deferred.)* Cheap to add later for `audit … | convert` piping, but
   out of scope for this increment; keeping it a real path also keeps the path-containment
   and re-read semantics simple. Not a blocker for any downstream family member.
10. **The read containment base is operator-controlled, never a default-trusted manifest
    field.** *(Resolved post-audit 2026-07-11; revises the original §per-record-loop step 3,
    which defaulted the base to `record.root`.)* A manifest run requires `--root DIR` (the
    operator supplies the base; `record.root` is then ignored — the "archive moved" path) **or**
    `--allow-manifest-root` (explicit opt-in to trust the manifest's recorded `root`); with
    neither it is refused. Rationale: `record.root` is untrusted input, so using it as the
    containment base makes containment circular (a hostile `root` defines its own "contained"
    region and reads/copies arbitrary parseable files into `--out-dir`); the sha256 gate is
    no defense (the attacker authors the hash too). An audit found this gap in the original
    design; the opt-in preserves the self-describing-manifest convenience for operators who
    trust their manifest, while making the safe anchor (`--root`) the recommended path.

## Alternatives considered

- **Trust `plan.operation` as control flow** (dispatch convert/recompress/copy_payload
  from the manifest). Rejected: it duplicates the converter's format dispatch and lets a
  stale/hostile manifest steer the operation; re-detecting format inside `recompress`
  keeps one source of truth and makes agreement automatic.
- **In-place by default** (reuse the legacy `overwrite=True` recompress default).
  Rejected: an archive-scale tool that mutates the source archive on the default path is
  the exact thing institutions refuse; safety must be the default, danger the opt-in.
- **Re-audit each file inside convert** (ignore the manifest's predictions, re-inspect).
  Rejected: it throws away the manifest's whole value (the cheap read-only pass already
  happened) and doubles the I/O; the sha256 gate + `recompress`'s predicates already make
  trusting the manifest safe.
- **Overload the legacy flat parser with `--manifest`.** Rejected: it would make
  `ncarnate --manifest x.jsonl` (bare, no verb) a thing, blurring the clean bare form the
  increment-1 shim deliberately preserved.
- **A separate `ncarnate-migrate` tool.** Rejected for the same reason increment 1 stayed
  a subcommand: the converter *is* ncarnate; the seam to externalize is the manifest
  schema (already a published contract), not the code.
- **Single-file `ncarnate/manifest.py` module home.** Rejected in favor of the subpackage
  (KD7) — the surface is large enough that the audit-symmetric subpackage keeps modules
  focused.

## Risks

- **Executing a stale manifest.** A file edited between audit and convert → the sha256
  gate skips it with an error; it is never converted on a stale prediction. (A `null`-hash
  manifest run with `--allow-unverified` forfeits this — documented as the unsafe mode.)
- **TOCTOU between the sha256 gate and the conversion read** *(residual risk, hostile
  archive filesystem).* `convert_manifest` `realpath`-resolves and hashes the source, then
  `recompress` re-opens it **by path** — two non-atomic opens. An attacker who can race the
  source tree (swap the file, or flip a parent directory to a symlink) between the hash and
  the `recompress` read makes the converter act on bytes the sha256 gate never verified.
  This is inherent to check-then-use with a path. **Current posture:** accepted as a
  documented residual risk — it requires write access to the archive mid-run (a strong
  precondition), and `recompress` still verifies its output lossless against whatever it
  read. **Full mitigation (deferred):** hash and convert from a single opened fd
  (`open` once with `O_NOFOLLOW`, hash that fd, hand the same handle to the converter),
  which requires giving `core.recompress` an fd-accepting entry point — a change to the
  conversion engine's API beyond this increment's scope.
- **Path traversal from an untrusted manifest** *(security — the manifest is data that
  becomes a filesystem write path).* A crafted `record.path` such as `../../etc/passwd`
  (or an absolute path) could make an output land outside `--out-dir`, or a read target
  point anywhere. **Control:** confine every resolved output to under `--out-dir` and
  reject any record whose `path` is absolute or escapes the root after normalization
  (`os.path.realpath` containment check); apply the same containment to the resolved
  *source* path under the read base. **The read base itself must be operator-controlled**
  (`--root`, or `record.root` only under the explicit `--allow-manifest-root` opt-in; KD10):
  containment under an *attacker-supplied* base is circular — a hostile `record.root` would
  simply define its own "contained" region — so trusting `record.root` by default was the
  original design's gap, closed here. This is a **required** control, not optional, and it
  is the *sole* defense against read/write redirection — the sha256 gate does **not** help
  here: an attacker who authors the manifest also authors `record.sha256`, so they would
  simply record the hash of whatever they redirect to. The sha256 gate defends a *different*
  threat (a file changed between audit and convert), not a hostile manifest. Treat the
  manifest as untrusted input whose only safe interpretation is: relative paths, confined
  under `root` (read) and `--out-dir` (write), rejected on any absolute or `..`-escaping
  path after normalization.
- **Mutating the archive unexpectedly.** Mitigated by the non-destructive default; only
  `--in-place` writes sources, and `recompress` still verifies lossless before any
  in-place replace.
- **Manifest from a newer/older ncarnate.** Schema-version refuse / ruleset-version warn
  (KD5); the sha256 gate and the converter's own predicates hold regardless.
- **Partial failure on a long run.** Per-record isolation + end-of-run summary + non-zero
  exit on any selected failure; `--skip-existing` makes a re-run resume rather than repeat.

## Open questions

*(None — all three resolved into Key decisions 7–9 at build start: module home =
subpackage; `--status` default = `ready`; stdin deferred.)*

## Rollout / sequencing

One increment, its own branch in `~/projects/ncarnate`, ATDD like increment 1:

1. **Reader + integrity gate:** manifest JSONL reader, schema/ruleset compat checks, the
   sha256 re-verify gate, path-containment control. Tests: a tampered file is skipped; a
   `null`-hash record is refused without `--allow-unverified`; a traversal `path` is
   rejected.
2. **Convert loop + destinations:** `convert_manifest` driving `recompress` into the
   mirrored `--out-dir`; per-status geolocation override; `--in-place`, `--skip-existing`.
   Tests: over the audit fixtures, a `ready` manifest round-trips to lossless outputs; a
   `ready_no_geolocation` record converts SDS-only; a blocker record is skipped.
3. **CLI wiring + agreement:** the `convert` sub-parser, `--manifest` xor paths, the
   summary + exit codes; an **agreement test** that `audit --output m.jsonl` then
   `convert --manifest m.jsonl` converts exactly the `ready` set and its outputs verify
   lossless. Docs (`README` golden path "audit then convert", `api.rst`), CHANGELOG.

This doc is the build-authoritative copy (imported at build start, as increment 1 did). The
`IMPLEMENTATION_PLAN.md` carries explicit plan items for the **integration seam** (CLI →
`convert_manifest` end-to-end, not just unit tests) and the **negative directions**
(tamper/null-hash/traversal/blocker), each as its own committed test fixture. This is the
load-bearing lesson from the increment-1 build loop: a checklist-anchored loop ticked every
box while never *wiring* the CLI to the engine and never testing the blocker direction — so
the plan must carry integration and negative-fixture ratchet items explicitly, or the loop
builds disconnected, half-tested parts.
