# `OperationResult` — the structured operation result (stage API step 4A) — Design

> **Status:** designed, build not started. Scoped to **step 4A** of the production-readiness
> roadmap ([`docs/plans/next-steps-priority-2026-07-15.md:172`](../plans/next-steps-priority-2026-07-15.md),
> action 13 in [`docs/plans/production-readiness-actions-2026-07-15.md:218`](../plans/production-readiness-actions-2026-07-15.md)).
> The `inspect → plan → execute` **primitives** are step 4B, designed separately
> (`ncarnate-stage-api.md`, forthcoming); this doc defines only the *result object* those
> primitives return. **This is the artifact step 5 freezes into a JSON Schema** — get the shape
> right here.
> **Codebase facts** below were verified against `ncarnate` on branch `feat/stage-api-4a` off
> `main` `73fe5af` on 2026-07-20: `recompress` at [`core.py:45`](../../ncarnate/core.py#L45),
> `_write_verified` at [`core.py:205`](../../ncarnate/core.py#L205), `preflight_destinations` at
> [`convert/preflight.py:100`](../../ncarnate/convert/preflight.py#L100), `_audit_file` /
> `AuditResult` at [`audit/__init__.py:200`](../../ncarnate/audit/__init__.py#L200) /
> [`audit/models.py:121`](../../ncarnate/audit/models.py#L121), the code registry at
> [`audit/codes.py`](../../ncarnate/audit/codes.py) (`RULESET_VERSION = 4`), and
> `SCHEMA_VERSION = 1` at [`audit/models.py:32`](../../ncarnate/audit/models.py#L32).

## Context / problem

A downstream integration — the frozen-handoff schema (step 5) and the materialized-Zarr-v3 tail
(step 6) — must **perform and understand** an ncarnate conversion without invoking the CLI, parsing
logs, or importing private functions (gate G4). Today the outputs of a conversion are:

- `recompress(...) -> str` returns only the destination **path**. Everything else it knew — the
  detected format, the effective per-variable encoding, which coordinates it reconstructed vs
  skipped, the sanitized-name companions, the verification it ran — is discarded or only visible as
  log lines and file side effects.
- The manifest path aggregates `ConvertRecord(path, reason, code)`
  ([`convert/models.py:63`](../../ncarnate/convert/models.py#L63)) — enough to know *that* a file
  converted or *why* one failed, but nothing about *what* the conversion produced.

So automation that wants the source/output digests, the executed encoding, or the
conversion-verification status has to scrape terminal output or re-open the files itself and guess.
Action 13's done-when is exactly the inverse: **automation does not need to scrape logs to determine
what happened.**

## Goals & non-goals

**Goals**

1. A single versioned `OperationResult` value type describing one executed conversion, with **its own
   schema version constant** (following the `SCHEMA_VERSION` / `RULESET_VERSION` precedent) so step 5
   can freeze against it independently of the audit record schema.
2. The **same** structure exposed as a Python object **and** as JSON, via a `to_record()` returning a
   JSON-safe dict (the established audit-model convention) plus a `canonical_json()` with a
   deterministic byte serialization for golden-hash tests.
3. Carry the action-13 field list: source & destination identities + detected formats; source/output
   sizes + sha256; operation + encoding options; **effective** per-variable encoding
   (dtype/endian/zlib/shuffle/complevel/chunking/fill); group/name mappings (`hdf4_name` /
   `hdf4_eos_name` / `<attr>__hdf4_name` companions); coordinate actions **generated vs skipped**;
   warnings with stable registry codes; ncarnate version; elapsed time.
4. **Separate conversion-verification status from scientific-validation status** — structurally, from
   day one, so step 5 freezes the separation without a shape change. ncarnate only ever populates the
   conversion-verification side; the scientific-validation side is declared `not_performed` for a
   caller/pipeline to fill.
5. Populate it from the existing execute engine (`_write_verified` region of `recompress`) **without
   changing `recompress`'s released `-> str` signature**, and collect it through the manifest run and
   the summary renderer.
6. Verification-status wording that does **not** overclaim beyond `_verify_lossless` /
   `verify_conversion`'s `equal_nan`-gated, dtype-scoped guarantee (overclaim-guard lens against
   [`docs/fidelity-notes.md`](../fidelity-notes.md)).

**Non-goals (scope fence)**

- **No `inspect`/`plan`/`execute` primitives, no batch preflight, no lazy iteration** — that is step
  4B. This doc defines only the value the future `execute` returns and threads it through the current
  entry points internally.
- **No JSON Schema file, no retention field, no source/executed-plan/output-digest triad framing** —
  that is step 5, which *freezes* this object. This doc must be *sufficient* for step 5; it does not
  do step 5's work. (The `retention` field explicitly stays out — step 5 owns it, action 5 step 3.)
- **No Zarr, no bounded-memory streaming, no `--jobs`, no compression-policy vocabulary** (steps 6,
  10, 15, 16) — the result *reports* the effective encoding but introduces no policy surface.
- **No change to `recompress`'s return type**, no new status/issue codes, no re-opening the frozen
  audit record schema.
- **No scientific validation** performed by ncarnate — only the *field* that names its absence.

## Approach

### Where it comes from — the three seams, unchanged in role

The result is assembled from data three existing seams already compute; 4A only stops throwing it
away.

| Field group | Source seam (already computes it) |
|---|---|
| source identity, detected format, source sha256 | `preflight_destinations` (manifest) / `detect_format` + `sha256_of_file` (one-shot) |
| destination path, operation (recompress vs convert) | `recompress` destination resolution ([`core.py:102-165`](../../ncarnate/core.py#L102)) |
| per-variable effective encoding, name mappings, coordinate actions | the write step (`_copy_variables` / the HDF4 `write_netcdf` tree) |
| conversion-verification status | `_verify_lossless` / `hdf4.verify_conversion` ([`core.py:416`](../../ncarnate/core.py#L416)) |
| output size + sha256 | a single read-back of the committed output after the atomic replace |

The engine already re-opens both files to verify (`_verify_lossless`); 4A adds one read-back of the
**committed** output to record its *effective* encoding (what the netCDF library actually wrote —
which for a contiguous source differs from the requested chunking) and its digest. Ground truth, not
the requested options echoed back.

### Module & type layout

New top-level module `ncarnate/result.py` (top-level because `core`, `convert`, and any future stage
API all consume it — mirrors how `audit/codes.py` is the shared code namespace). All types are stdlib
dataclasses with a JSON-safe `to_record()`, exactly like `audit/models.py`.

```
OPERATION_RESULT_SCHEMA_VERSION = 1     # own constant; bumped only on a breaking result-shape change

OperationResult                          # one executed conversion
├─ source        : SourceIdentity        # path, detected_format, size_bytes, sha256
├─ destination   : OutputIdentity        # path, container_format ("NETCDF4"), size_bytes, sha256
├─ operation     : str                   # "recompress" | "convert"  (netCDF rewrite vs HDF4 conversion)
├─ options       : EncodingOptions       # zlib, shuffle, complevel, geolocation  (the requested run)
├─ verification  : VerificationStatus    # ── conversion-integrity, SEPARATE from science (see below)
├─ variables     : list[VariableEncoding]# per-variable EFFECTIVE encoding (read back from output)
├─ name_mappings : list[NameMapping]     # sanitized→original companions (HDF4 path only; [] otherwise)
├─ coordinates   : CoordinateActions     # generated: [str]; skipped: [SkippedCoordinate{name,reason,code}]
├─ warnings      : list[ResultWarning]   # non-fatal notes: {code, message, context} (registry codes)
└─ elapsed_seconds : float               # time.monotonic() around the execute engine
# ncarnate_version + schema_version are injected by to_record() from constants, never stored per-instance
```

`VariableEncoding` = `{path, dtype, endian, zlib, shuffle, complevel, chunksizes|None, fill_value|None}`.
`NameMapping` = `{netcdf_name, original_name, kind}` where kind ∈ {variable, dimension, attribute,
group, grid} — the companion-attribute families listed in
[`fidelity-notes.md`](../fidelity-notes.md).

### The verification / validation separation (load-bearing for step 5)

```
VerificationStatus
├─ conversion_verification : "verified"          # the ONLY success value; a failure RAISES (never a result)
├─ scientific_validation   : "not_performed"     # ncarnate never sets anything else — a pipeline fills this
├─ verifier                : str                 # e.g. "ncarnate._verify_lossless" / "ncarnate.hdf4.verify_conversion"
├─ verifier_version        : str                 # ncarnate __version__ (the code that ran the check)
└─ method                  : str                 # the guarantee, worded to fidelity-notes — NOT beyond it
```

`method` reads, verbatim-scoped to the contract: *"value-identical raw arrays
(`np.array_equal`, `equal_nan` for float; complex excluded), dtype/dimension/attribute/group-tree
equal; storage-only changes."* It claims re-read value identity, **not** scientific correctness — the
two are different questions and the schema keeps them different fields.

An `execute` that fails verification **raises** (`VerificationError`) and atomically leaves no output
(the existing `_write_verified` contract) — there is no `OperationResult` for a failed conversion, so
`conversion_verification` has no failure enum value. The manifest loop records a failed execute as a
`ConvertRecord(path, reason, code)` exactly as today; failures are already scriptable via `.code` (F2)
and never require log-scraping, satisfying action 13 for the failure path without a rich digest of an
output that does not exist or is unverified.

### JSON: `to_record()` and `canonical_json()`

- `to_record() -> dict` — JSON-safe, injecting `schema_version` and `ncarnate_version` from constants
  (audit-model convention). Numpy scalars (fill values, attribute values) coerced to Python scalars;
  **non-finite floats encoded as the strings `"NaN"` / `"Infinity"` / `"-Infinity"`** so the payload
  is strict-RFC-8259 JSON (a fill value of NaN is real and must round-trip through a schema validator).
- `canonical_json(result) -> str` — `json.dumps(to_record(), sort_keys=True,
  separators=(",", ":"), ensure_ascii=False, allow_nan=False)`. Deterministic bytes so the step-5
  golden **hash** test is stable across runs and platforms. This is the exact function step 5's
  "canonical serialization/hash golden tests" will pin.

### Relationship to `ConvertRecord` / `ConvertResult`

Decision (see Key decisions): **`OperationResult` does not replace `ConvertRecord`; it replaces the
success payload.** `ConvertResult.converted` becomes `list[OperationResult]`; `skipped` and `failed`
stay `list[ConvertRecord]` (nothing executed, so no digest exists). The summary renderer gains an
`OperationResult` overload (renders `.source.path`, size delta, and any warnings). A one-shot
`recompress` caller still gets a `str`; the rich result is reachable through the 4B `execute`
primitive (and, internally in 4A, the manifest journal).

### Manifest journal

4A adds an optional `--result-journal <path>` to `convert --manifest`: one
`OperationResult.to_record()` JSON object per line, for every converted file. This is action 13's
"manifest journal" surface and the machine-readable record a pipeline consumes. The human summary
(`render_summary`) is unchanged in spirit — tallies plus per-record reasons — and additionally prints
each conversion's size delta.

## Key decisions

- **KD1 — new `OperationResult`, `ConvertRecord` survives.** The handoff floated making `ConvertRecord`
  a thin *view* of `OperationResult`. Rejected: `ConvertRecord` also models **skips** (status not
  selected, blocker) and **pre-execution failures** (containment, sha256 mismatch, collision) where
  *nothing was executed* and a source/output digest is meaningless. Forcing those through a rich result
  full of `None`s is worse than an honest split: `converted: list[OperationResult]`,
  `skipped`/`failed: list[ConvertRecord]`.
- **KD2 — `recompress(...) -> str` is unchanged (released contract).** ncarnate is on PyPI/conda-forge
  at v2.2.1; changing a public return type is a breaking change. The execute engine gains an internal
  `_recompress_result(...) -> OperationResult`; `recompress` calls it and returns
  `result.destination.path`. 4B's public `execute(plan) -> OperationResult` wraps the same internal.
- **KD3 — own schema-version constant.** `OPERATION_RESULT_SCHEMA_VERSION` is independent of the audit
  `SCHEMA_VERSION` (they version different artifacts and will move at different times). Step 5 freezes
  *this* number.
- **KD4 — conversion-verification and scientific-validation are separate fields, always present.**
  Named structurally from v1 so step 5 freezes the separation, not a later migration. ncarnate sets
  `scientific_validation = "not_performed"` and never anything else.
- **KD5 — record EFFECTIVE encoding, read back from the committed output.** For a contiguous source
  the library picks chunking ncarnate did not request; echoing the *requested* options would misreport
  what a Zarr tail must reproduce. One post-replace read-back yields ground truth and the output digest
  in the same open.
- **KD6 — failure raises; no failure enum on `conversion_verification`.** Preserves the fail-loud,
  atomic "no bad output survives" contract; a failed conversion has no verified output to describe.
- **KD7 — warnings reuse the existing code registry.** A `ResultWarning.code` is drawn from
  `ncarnate/audit/codes.py` `ALL_CODES` (the single stable namespace), keeping every machine-readable
  signal ncarnate emits in one registry. A skipped coordinate cites the same code the audit would.
- **KD8 — non-finite floats serialize as strings; canonical JSON is sorted + tight + `allow_nan=False`.**
  Strict JSON portability for a schema-validated payload, and a stable golden hash for step 5.
- **KD9 — `record.path` / mapping names stay `!r`-escaped in any rendering.** Manifests are untrusted
  input (threat-model boundary); the summary renderer's existing `!r` discipline
  ([`convert/report.py:49`](../../ncarnate/convert/report.py#L49)) extends to every new rendered field.

## Alternatives considered

- **Extend `ConvertRecord` in place with all the fields.** Rejected (KD1): overloads one type across
  executed / skipped / failed, most fields `None` for two of the three; and `ConvertRecord` is
  manifest-only, whereas the result must also serve the one-shot and future stage-API paths.
- **Change `recompress` to return `OperationResult`.** Rejected (KD2): breaks a released public API for
  no gain a wrapper doesn't give.
- **A single `verification_status` string (e.g. `"verified"`) covering everything.** Rejected (KD4):
  collapses conversion-integrity and scientific-validation into one field, which is exactly the
  conflation step 5 forbids. The separation must be structural, not a naming convention.
- **Echo the requested encoding options instead of reading back effective encoding.** Rejected (KD5):
  misreports library-chosen chunking; a Zarr tail reproducing "the plan" would diverge from the file.
- **Put warnings in a fresh per-result code space.** Rejected (KD7): fragments the stable namespace
  archive managers already script against.
- **Emit Python `NaN` tokens (json default `allow_nan=True`).** Rejected (KD8): not valid JSON; a
  conforming step-5 schema validator would reject the very fill values ncarnate must round-trip.

## Risks

- **Output hashing cost on multi-GB granules.** `sha256_of_file` streams, but hashing every output
  doubles the read of a large file. v1: on by default (action 13 asks for output hashes), reusing the
  preflight-computed **source** digest rather than recomputing it. A future opt-out / bounded-memory
  hash is action-10 territory — noted, not built.
- **Wide-file result size.** A file with thousands of variables yields a large `variables` list. Kept
  (it is what makes the result sufficient for a Zarr tail, G5); the human summary renderer elides it,
  only the JSON journal carries it.
- **Over/under-claiming verification.** The `method` wording is the overclaim surface; it is pinned to
  `fidelity-notes.md` and must be re-checked against `_verify_lossless`/`verify_conversion` in review
  (overclaim-guard). It claims *re-read value identity within the dtype scope*, not correctness.
- **Untrusted names in the JSON journal.** Mapping originals and `record.path` come from untrusted
  inputs; they are data values in JSON (safe by construction) but any *textual* rendering keeps `!r`
  (KD9).
- **Schema drift vs step 5.** This object is frozen downstream; any field added after step 5 freezes
  is a `OPERATION_RESULT_SCHEMA_VERSION` bump. The golden canonical-hash test is the tripwire.

## Open questions

1. **`--result-journal` flag name & default.** Propose off-by-default, explicit path (symmetric with
   `audit --output`). Confirm the flag name against the audit family's conventions in 4B, where the CLI
   surface is otherwise touched.
2. **Does `operation` reuse `ConversionPlan.operation`'s vocabulary?** The audit
   `ConversionPlan.operation` ([`audit/models.py:109`](../../ncarnate/audit/models.py#L109)) already
   names the operation; recommend the result reuse the same strings so inspect≙audit and the executed
   result agree. Confirm the exact tokens when 4B wires `inspect`.
3. **Elapsed-time boundary.** Wrap just the execute engine (write + verify + read-back), or include
   plan resolution? Recommend engine-only for v1 (the plan is cheap and separately timable in 4B);
   revisit if a pipeline wants end-to-end timing.
4. **`fill_value` for user-defined / non-scalar fills.** v2 fidelity excludes compound/VLen types
   (they raise `UNSUPPORTED_TYPE` before output), so `fill_value` is always a JSON-safe scalar or
   `None` on the supported path — confirm no exotic scalar (e.g. a length-1 char array) slips through
   the coercion.

## Rollout / migration

1. Add `ncarnate/result.py` (types + `OPERATION_RESULT_SCHEMA_VERSION` + `to_record` +
   `canonical_json`), pure data, no I/O — unit-testable in isolation.
2. Thread it through the execute engine (internal `_recompress_result`), `recompress` returns
   `.destination.path` (no signature change).
3. `ConvertResult.converted -> list[OperationResult]`; summary renderer overload; `--result-journal`.
4. Golden canonical-serialization + hash tests; one real-fixture (AMSR-E grid) result fixture checked
   into `tests/`.
5. Gauntlet (`pytest -q`, `ruff check .`, `build` + `twine check`) → `/pre-merge-review` → **local**
   `--no-ff` merge. **No push, no release** — shipping step 4 is a separate owner-gated ask (CHANGELOG
   + version bump + conda sync happen then, not now).

No data migration: `OperationResult` is additive; the audit record schema and `recompress`'s public
signature are untouched. The primitives (4B) and the frozen JSON Schema (step 5) build **on** this
object, in their own branches.
