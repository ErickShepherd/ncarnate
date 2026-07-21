# Freeze the verified-netCDF4 handoff schema (step 5) — Design

> **Status:** **REVIEWED** (step 5 on `feat/handoff-schema-5`; rev 2 folds an independent adversarial
> review — MUST-FIX `adapter_versions` schematization, plan_hash null-digest caveat, strengthened G5
> gate, `definitions` over `$defs`, deferral-cost note). Freezes the `OperationResult` shipped in
> step 4A ([`docs/design/ncarnate-operation-result.md`](ncarnate-operation-result.md)) into a versioned
> JSON Schema + a caller-owned `retention` slot + an explicit `plan_hash`, with a contract test and a
> real-fixture golden. Scoped to the frozen verified-netCDF4 handoff contract.
> **This doc must be reviewed before the schema is written** (the 4A adversarial-review pattern).
> **Codebase facts** verified against `ncarnate` on `feat/handoff-schema-5` off `main` `8465ad4`,
> 2026-07-20: `OPERATION_RESULT_SCHEMA_VERSION = 1` at
> [`result.py:48`](../../ncarnate/result.py#L48); `OperationResult` at
> [`result.py:487`](../../ncarnate/result.py#L487) (`to_record` :511, `canonical_form` :529,
> `canonical_json` :584); `ValidationRecord` at [`result.py:417`](../../ncarnate/result.py#L417);
> the two construction sites `_build_operation_result` ([`core.py:891`](../../ncarnate/core.py#L891))
> and `_minimal_result` ([`core.py:429`](../../ncarnate/core.py#L429)); the frozen-schema precedent at
> [`tests/audit/record.schema.json`](../../tests/audit/record.schema.json) +
> [`tests/audit/test_contract.py`](../../tests/audit/test_contract.py); the existing canonical golden
> [`tests/fixtures/operation_result/packed_fill.canonical.json`](../../tests/fixtures/operation_result/packed_fill.canonical.json).

## Context / problem

Step 4A made `OperationResult` **sufficient** for a downstream Zarr tail: after an independent
adversarial review it carries the full group / dimension / attribute layer, so a consumer can build
array shape, `dimension_names`, dtype, fill, codecs, and coordinate *identities/paths* *without
re-opening the source granule* (the array *values*, including reconstructed coordinates, are read from
the verified `destination` netCDF4 — see the post-freeze addendum on consumer obligations).
Step 4A deliberately stopped short of the freeze: it left out the caller-owned `retention` field, the
explicit executed-plan hash, and any JSON Schema, naming each as **step 5's** to own (4A Non-goals).

Step 5 is that freeze. It turns the shape into a **contract** a downstream repo (the step-6 Zarr tail,
and any pipeline consuming the manifest journal) validates *received handoffs* against — so the schema
is a validation surface **for the consumer**, not just documentation of our output. **Gate G5:** the
schema is sufficient for a Zarr tail without reading terminal output or ncarnate internals — proven by
a test that acts as the step-6 consumer and derives a Zarr-array spec from a schema-valid result alone.

This is a **freeze, not a redesign.** The `OperationResult` shape is not reopened; the only genuinely
new fields are `retention` (caller-owned) and `plan_hash` (computed). Both are additive and both bump
`OPERATION_RESULT_SCHEMA_VERSION` 1→2 with a golden regeneration — the expected, documented cost of a
shape change (4A KD3: the canonical-hash golden is the drift tripwire).

## Goals & non-goals

**Goals**

1. A checked-in **JSON Schema** (draft-07) for one `OperationResult.to_record()` — every field the
   step-5 spec names (source digest, executed-plan hash, versions, output digest, group tree,
   name mappings, dtypes, packing/fill, chunks/compression, coordinates, warnings, verification, and a
   validation record), frozen against schema version 2.
2. A **contract test** mirroring `tests/audit/test_contract.py`: every emitted `to_record()` (over the
   in-repo netCDF and HDF-EOS2 fixtures) validates against the schema; a malformed record is rejected
   (proving the schema is tight, not vacuous); the schema-version const is pinned.
3. The **`retention`** slot — a caller/pipeline-owned field ncarnate **never** sets (always emits
   `null`), declared in the schema so a pipeline can attach retention metadata without a shape change.
4. An explicit **`plan_hash`** — a stable, ncarnate-version-independent identity of the *executed plan*
   (operation + source identity + requested options), so a Zarr tail can key idempotent/retry-safe
   materialization on it.
5. Codify the **conversion-integrity vs scientific-validation separation** (already structural in 4A:
   `VerificationRecord` vs `ValidationRecord`) in the schema as two distinct required records.
6. Canonical serialization / hash golden (the explicitly-chunked `packed_fill`, regenerated for v2) as
   the drift tripwire, **plus** one **real** ncarnate result fixture (AMSR-E HDF-EOS2 — the richest:
   StructMetadata, name mappings, reconstructed coordinates) that validates against the schema and
   drives the G5 gate.
7. **Overclaim-guarded** schema `title`/`description` prose — the verification wording must not claim
   beyond `_verify_lossless` / `hdf4.verify_conversion`'s `equal_nan`-gated, storage-only guarantee
   ([`docs/fidelity-notes.md`](../fidelity-notes.md)).

**Non-goals (scope fence)**

- **No reshaping of the 4A object** beyond adding `retention` + `plan_hash`. Any other field is out of
  scope; the shape was frozen-sufficient in 4A on purpose.
- **ncarnate never populates `retention` or `validation.validator/method/record`** — it only reserves
  the slots. A pipeline fills them.
- **No Zarr tail** (step 6), no bounded-memory / `--jobs` (steps 10+). The schema is *sufficient for*
  a Zarr tail; it builds none.
- **No new runtime or test dependency** for validation (the audit-contract spec constraint) — the
  contract test uses a tiny stdlib schema-subset validator, extended only to resolve local `$ref`
  (needed for the recursive group tree).
- **No push, no release.** Shipping step 4+5 (CHANGELOG + version bump + conda sync) is a separate
  owner-gated ask. This branch merges **locally** on sign-off.

## Approach

### The schema file — location & shape

Mirror the audit precedent: a test-scoped, checked-in schema next to the fixtures it validates —
`tests/fixtures/operation_result/handoff.schema.json` — with the contract test at
`tests/test_handoff_schema.py`. (The audit schema lives in the test tree, not `package_data`; the
downstream step-6 consumer is a *separate* artifact that vendors or references the frozen file. Promoting
the schema to shipped `package_data` later is additive and non-breaking — deferred, not precluded; see
Open questions.)

Draft-07, `type: object`, `additionalProperties: false` on **every structural object**, with `required`
listing every ncarnate-emitted key. The recursive group tree is expressed with `definitions` + `$ref`
(`groupNode` references itself through `groups`; draft-07's idiomatic keyword is `definitions`, and the
hand-rolled validator resolves by JSON-pointer regardless). Two classes of node are **not**
strictly-closed:

*Intentionally-open bags* — declared `object` / `["object","null"]` with **no**
`additionalProperties: false`, because their interior is caller- or data-owned and not ours to constrain:

- `retention` (`["object","null"]`) — caller-owned;
- `validation.record` (`["object","null"]`) — the pipeline's validation evidence;
- `warnings[].context` (`object`) — per-warning context (matches audit `issues[].context`);
- `attributes[].value` — an arbitrary JSON-safe attribute value (scalar, string, array, or the
  non-finite-float string tokens `"NaN"`/`"Infinity"`/`"-Infinity"`), typed as the draft-07 union of
  permitted JSON types rather than pinned.

*The adapter-version map* — `environment.adapter_versions` is a dict keyed by adapter name (`numpy`,
`netCDF4`, `netcdf_c`, `libhdf5`, `pyhdf`, `libhdf4`; [`core.py:926`](../../ncarnate/core.py#L926)) with
`string | null` values (a runtime absent from the install → `null`). The **key set is a probe list, not
a frozen contract** (a future adapter is additive), so it is schematized as an *open-valued* object —
`{"type": "object", "additionalProperties": {"type": ["string", "null"]}}` — rather than a closed
property list. This is the review's headline catch: the freeze validates the **full** `to_record()`
(which carries `environment`), so an un-schematized `adapter_versions` would red the contract test on
authoring. It is dropped from `canonical_form` (environment is nondeterministic), so only the
full-record contract test exercises it.

### The two new fields

**`retention`** becomes an optional field on `OperationResult` (`retention: dict | None = None`), so
neither `core.py` construction site changes — the default is `None`, and ncarnate has no code path that
sets it. `to_record()` emits `"retention": null`. It is declared in the schema as `["object","null"]`,
open interior. It **is** kept in `canonical_form()` (always `null` from ncarnate, therefore
deterministic): the golden then pins *"ncarnate emits retention = null"*, turning the drift tripwire
into an enforcement that ncarnate never starts filling the caller's slot.

**`plan_hash`** is a **computed** value, not stored state — a method on `OperationResult` that
`to_record()`/`canonical_form()` call — so, again, neither construction site changes. It is the
`sha256` hex digest of the canonical JSON of the *executed-plan projection*:

```
plan_hash = sha256(json.dumps(
    {"operation": operation,
     "options"  : options.to_record(),
     "source"   : {"detected_format": …, "size_bytes": …, "sha256": …}},
    sort_keys=True, separators=(",",":"), ensure_ascii=False, allow_nan=False
)).hexdigest()
```

Rationale: a Zarr tail wants a stable *executed-plan identity* — "same source bytes + same request ⇒
same `plan_hash`" — to make materialization idempotent and retries safe (step-6 actions 4/7). The
projection is exactly the **inputs** that determine the conversion (operation + requested options +
source identity, digest included so two different sources don't collide), **not** the output structure
(which is the *result* of executing the plan, not the plan). It excludes the absolute source *path*
(machine-specific) and every ncarnate/library version (so the identity is reproducible across releases).
`plan_hash` is derived from fields already in `canonical_form`, so it is deterministic there too; it is
kept in both serializations as a convenience index.

**Collision caveat (review SHOULD-FIX):** `plan_hash` is a collision-resistant key **only when
`source.sha256` is non-null.** Under `--allow-unverified` the digest is `None`
([`result.py:143`](../../ncarnate/result.py#L143)), and the projection degrades to
`{operation, options, format, size}` — two *different* unverified granules of equal
size+format+options then share a `plan_hash`. A step-6 consumer must therefore **refuse to key
idempotency/retry on a null-digest plan** (`source.sha256 is None`); this is documented on the field and
in the schema `description`, not silently assumed away.

### `to_record()` v2 shape (delta from v1)

```
{ schema_version: 2,          # ← bumped
  ncarnate_version, operation,
  plan_hash: "<sha256 hex>",  # ← new, computed
  source, destination, options, structure, name_mappings, coordinates,
  verification, validation,
  retention: null,            # ← new, caller-owned, ncarnate always null
  environment, warnings, elapsed_seconds }
```

`canonical_form()` (v2) keeps `plan_hash` and `retention` and continues to drop the nondeterministic
fields (`ncarnate_version`, `elapsed_seconds`, `environment`, absolute paths, output digest/size,
`verifier_version`) exactly as in 4A.

### The G5 gate — a test that *is* the step-6 consumer

`tests/test_g5_handoff_sufficiency.py` produces the AMSR-E result from the **committed** trim fixture
(`amsre_seaice12km_trim.hdf`, in-repo — no raw granule), validates its `to_record()` against the frozen
schema, then derives a Zarr-v3 array spec per variable. To make "record alone" **mechanical, not
disciplinary** (review SHOULD-FIX), the derivation is a module-level helper `zarr_array_spec(record:
dict) -> dict` that takes **only the JSON dict** — no `OperationResult`, no `ncarnate.core`, no netCDF
handle in scope — so record-insufficiency is a `KeyError`, not a silent re-open. Per variable it derives:

- `shape` — each `Variable.dimensions` name resolved against the group's `dimensions` sizes;
- `dimension_names` — the names verbatim (Zarr-v3 native);
- `dtype` — from `Variable.dtype` (`var.dtype.str`, e.g. `<i2`/`|i1`), which **already carries
  byteorder**; the derivation must read byteorder from `dtype` and **never** from `endian` (which is
  `"native"` for native vars, [`core.py:980`](../../ncarnate/core.py#L980) — not a concrete order);
- `fill_value` — the `_FillValue` attribute value if present, else `None` (the source may not declare
  one, [`core.py:654`](../../ncarnate/core.py#L654));
- `chunks` — `encoding.chunksizes` if present, else the full `shape` (contiguous → `chunksizes` is
  `None`, [`result.py:273`](../../ncarnate/result.py#L273));
- `codecs` — from `encoding.zlib`/`shuffle`/`complevel`;
- the coordinate variables from `coordinates.generated`.

The gate asserts **concrete expected values** for named AMSR-E variables (exact `shape`, `dtype`,
`fill_value`, `chunks`, `dimension_names`) — not merely "derivable", which a partial-spec return could
pass vacuously — **and** asserts reconstructed `lat`/`lon`/`polar_stereographic` appear in
`coordinates.generated`. It **also** asserts both None-fallbacks explicitly (a synthetic /
minimal-record case with `chunksizes: null` → `chunks == shape`, and no `_FillValue` → `fill_value is
None`), since the AMSR-E grid alone (explicit chunks + real fill) exercises neither. **That derivation,
against pinned values and both fallbacks, is the gate.**

## Key decisions

- **KD-S1 — `retention` is a field on the object, ncarnate always `null`; bump to schema v2.** An
  optional `retention: dict | None = None` reserves the caller's slot with zero change to either
  construction site, and the schema declares it. Rejected leaving it schema-only (a slot the object
  can't carry) — a pipeline needs to attach it to the *same record* it received.
- **KD-S2 — `retention` is kept in `canonical_form`.** It is deterministic (always `null` from
  ncarnate), so the golden pins that ncarnate never fills it — a stronger guarantee than excluding it.
- **KD-S3 — `plan_hash` explicit and computed, over the executed-plan *inputs*.** A Zarr tail wants a
  stable plan identity for idempotency; deriving it ad-hoc in every consumer invites drift. Computed
  (not stored) so construction sites are untouched. Projection = operation + options + source identity
  (digest in, path/versions out) — the plan, not the result structure. Collision-resistant only with a
  non-null `source.sha256`; the null-digest (`--allow-unverified`) degradation is documented on the
  field, not assumed away.
- **KD-S4 — `additionalProperties: false` on all structural objects; open bags typed but not closed.**
  Strict-closed catches ncarnate drift loudly (the whole point of a freeze); the caller-owned /
  data-owned bags (`retention`, `validation.record`, `warnings[].context`, `attributes[].value`) stay
  open (matches audit `issues[].context`), and `environment.adapter_versions` is an **open-valued**
  object (probe-list keys, `string|null` values) — not one of the four data bags but likewise not a
  frozen key set. Missing this last one would red the contract test, since the freeze validates the
  full `to_record()`.
- **KD-S5 — schema lives in the test tree, `definitions` + `$ref` for recursion, stdlib validator
  extended.** Mirrors `tests/audit/record.schema.json`; the recursive `groupNode` needs `$ref`, so the
  audit test's tiny validator is extended to resolve `#/definitions/*` JSON-pointers (recursion
  terminates on the finite instance tree — `groups: []` — so no loop) — still no third-party dependency.
- **KD-S6 — two goldens with two jobs.** The **hash** golden is the explicitly-chunked `packed_fill`
  canonical JSON (deterministic across HDF5 versions), regenerated for v2 — the drift tripwire. The
  **real** fixture is AMSR-E, checked in as a schema-validation + G5 reference and validated
  structurally, but **not** hash-pinned (its convert-path chunking may vary by HDF5 version — see
  Risks). This honors "add canonical/hash goldens *and* one real result fixture" without silently
  pinning a brittle cross-environment hash.
- **KD-S7 — verification prose is copied verbatim from the 4A per-verifier `method` strings**, which are
  already overclaim-guarded against `fidelity-notes.md`. The schema adds no new verification claim; its
  `description` fields point to the fidelity contract rather than restating a guarantee.

## Alternatives considered

- **`plan_hash` over `canonical_form(options + structure)`** (the 4A note's phrasing). Rejected: the
  output structure is the *result* of executing the plan, not the plan; folding it in makes `plan_hash`
  a result-identity (which the canonical/golden hash already provides) and breaks the useful
  "same request ⇒ same plan_hash" semantic a Zarr tail keys on.
- **`retention` as a schema-only optional a pipeline attaches post-emission.** Rejected (KD-S1): the
  object then can't carry what the record must — the pipeline receives an `OperationResult`, not a raw
  dict, and needs a place to put retention on it.
- **Exclude `retention` from `canonical_form`** (treat it like `environment`). Rejected (KD-S2): it is
  deterministic from ncarnate; keeping it turns the golden into an enforcement that ncarnate never sets
  it, which is exactly the invariant we want to freeze.
- **`additionalProperties: true`** (permit attachments freely). Rejected (KD-S4): weakens the freeze's
  tripwire — a drifted extra field from ncarnate would pass silently. The declared-open bags give
  callers their room without opening the structural objects.
- **Ship the schema as `package_data` now.** Deferred, not rejected (Open questions): the audit
  precedent is test-scoped, promotion is additive and non-breaking, and the sole consumer today (step 6)
  is a separate artifact that can vendor the frozen file. Revisit when step 6 lands.
- **Adopt `jsonschema` for validation.** Rejected: the audit-contract spec constraint forbids a new
  dependency; extending the stdlib validator with `$ref` is a few lines.
- **Hash-pin the AMSR-E real fixture.** Rejected (KD-S6): convert-path library-default chunking can
  vary by HDF5 version; pinning it would be a cross-environment flake. `packed_fill` (explicit chunks)
  is the deterministic hash tripwire.

## Risks

- **Golden brittleness across environments.** The `packed_fill` canonical hash is stable only on the
  little-endian, fixed-HDF5, explicitly-chunked dev/CI target (documented in 4A §Risks and in
  `canonical_form`'s docstring). Inherited unchanged. The AMSR-E fixture is validated structurally, not
  hash-pinned, precisely to avoid a chunking-driven cross-version flake. Flagged, not silently pinned.
- **Schema-drift vs the freeze.** Any field added after v2 is another `OPERATION_RESULT_SCHEMA_VERSION`
  bump + golden regen; the contract test's `const` schema-version and the canonical-hash golden are the
  two tripwires. This is a feature (loud on drift), the intended cost.
- **Trust boundary (threat-model).** The schema validates ncarnate's **own** output — but a downstream
  validates *received* handoffs against it, so it is a security surface for the consumer. Mapping
  originals, attribute values, and `record.path` originate in untrusted inputs; they are **data values**
  in JSON (safe by construction), and any textual rendering keeps the 4A `!r` escaping (4A KD9). The
  schema does not widen this surface: closed structural objects reject an injected unexpected key, and
  the open bags carry data, never executable structure. No new assets, no new attack surface beyond the
  4A object; the control (strict `additionalProperties` + typed leaves) is proportional.
- **Overclaim in schema prose.** The `description`/`title` verification wording is the overclaim
  surface; pinned to the 4A per-verifier strings (already guarded against `fidelity-notes.md`) and
  re-checked with `overclaim-guard` before merge.
- **`$ref` validator correctness.** The extended stdlib validator must resolve `#/definitions/*` and recurse
  without infinite loops on the self-referential `groupNode`. Covered by the malformed-record negative
  and by validating the deeply-nested AMSR-E group tree (a real recursion depth > 1).

## Open questions

1. **Ship the schema as `package_data`?** Deferred to when step 6 lands and its consumption pattern is
   known (vendor vs import), mirroring the test-scoped audit precedent per the owner convention. Additive
   and non-breaking; does not block the freeze. **Honest cost of the deferral (review SHOULD-FIX):** until
   promotion, the step-6 consumer can only *vendor a copy* of the test-tree file — no installed
   `ncarnate` artifact carries the frozen contract — so a promotion to `package_data` is the natural
   first move when step 6 starts, and is called out here rather than left implicit.
2. **Schema file name.** `handoff.schema.json` (this doc) vs `record.schema.json` (exact audit mirror).
   Leaning `handoff.schema.json` — it is *the* handoff schema and the name says so; confirm in review.

## Rollout / migration

1. `result.py`: add `retention: dict | None = None`; add a computed `plan_hash()`; emit both in
   `to_record()` and `canonical_form()`; bump `OPERATION_RESULT_SCHEMA_VERSION` to 2. No `core.py`
   change (both new fields default/compute). Export unchanged (`OperationResult` / `canonical_json` /
   `OPERATION_RESULT_SCHEMA_VERSION` already public).
2. Write `tests/fixtures/operation_result/handoff.schema.json`.
3. Regenerate `packed_fill.canonical.json` (now v2, +`plan_hash`, +`retention:null`) and re-pin its
   hash in `tests/test_operation_result.py`.
4. Add `tests/test_handoff_schema.py` (contract: validate fixtures' `to_record()`; malformed negative;
   schema-version const) and `tests/test_g5_handoff_sufficiency.py` (the G5 consumer gate). Check in the
   AMSR-E real result fixture.
5. Gauntlet (`pytest -q`, `ruff check .`, `build` + `twine check`) → `/overclaim-guard` on the schema
   prose → `/pre-merge-review` → **local** `--no-ff` merge. **No push, no release.**

No data migration: v2 is additive over v1 (two new fields, one caller-null); `recompress`'s public
signature and the audit record schema are untouched.

## Post-freeze addendum — handoff-contract hardening (before step 6)

A multi-model panel (Haiku/Sonnet/Opus/Fable) reviewed this freeze as the step-6 foundation and found a
cluster of cheap-now/expensive-later gaps at the seam a consumer compiles against — all docs + small
tests + one packaging change, **none reopening the v2 record shape**. Landed on
`fix/handoff-contract-hardening`:

- **Schema promoted to `package_data` (resolves Open Q1).** The frozen schema now ships in the wheel at
  `ncarnate/schemas/handoff.schema.json`, loaded via `ncarnate.handoff.load_handoff_schema()`; the
  test-tree copy is gone (single source of truth — no vendored-copy drift). The stdlib `$ref` validator
  moved into `ncarnate.handoff` (`validate_handoff` / `schema_errors`), so a consumer imports the same
  validator the contract test uses. (Supersedes the "test tree, not `package_data`" note under
  *The schema file — location & shape* and Rollout step 2.)
- **The handoff is a two-artifact pair — record + digest-verified destination.** The record is
  **metadata only** (no array bytes, not even reconstructed coordinate *values*); every byte lives in the
  `destination` netCDF4. A consumer reads data from `destination`, never re-opens `source.path`, and MUST
  verify `destination.sha256` before reading (`canonical_form` drops path/size/digest, so the digest is
  the only durable byte identity). `destination.path` is advisory. This obligation is now in the schema
  top-level `description`; it was previously implicit.
- **The silent-empty-store trap.** The degraded `_minimal_result` (readback failed post-commit) is
  schema-valid, `verified`, and empty — a naive "validate → derive → materialize" consumer would build an
  empty store. New `ncarnate.handoff.check_materializable` refuses it (empty structure over a non-empty
  destination, a lingering `RESULT_READBACK_INCOMPLETE`, or an unknown `schema_version`); a consumer gates
  through it, not schema validation alone. Codes `HANDOFF_SCHEMA_INVALID` / `HANDOFF_NOT_MATERIALIZABLE`
  (registry v6).
- **`plan_hash` dedupes requests, not artifacts (sharpens KD-S3).** It excludes the output, so the same
  request under a different HDF5/ncarnate version can yield a different store; artifact identity is the
  pair `(plan_hash, destination.sha256)`, and the step-6 commit manifest must link the digest.
- **Null-digest caveat corrected.** `execute()` *always* hashes the source, so the `--allow-unverified`
  null-digest degradation the KD-S3 caveat described is **not reachable** through the stage API today
  (that flag lives only in the separate `convert` manifest pipeline). The nullable branch is retained for
  a future digest relaxation; the caveat prose (schema + `plan_hash` docstring) now says so rather than
  implying a live path.
- **Codec realizability is a step-6 profile decision, not proven by G5.** The G5 `_codecs` sketch emits
  `{"name":"shuffle"}`, which is not a Zarr v3 *core* codec — G5 proved metadata *reachability*, not spec
  *realizability*. The shuffle→(blosc/numcodecs) mapping is an explicit step-6 choice.
