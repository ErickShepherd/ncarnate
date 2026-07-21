# `OperationResult` — the structured operation result (stage API step 4A) — Design

> **Status:** **BUILT** (step 4A implemented on `feat/stage-api-4a`; 337 tests green, ruff clean,
> build + twine pass). **Rev 3** (2026-07-20) — one implementation-driven refinement to KD1: a
> converted `ConvertRecord` *carries* an optional `OperationResult` rather than `ConvertResult.converted`
> changing element type, because the manifest-relative `path` is load-bearing for the summary and the
> per-record scripting handle (and is distinct from `OperationResult.source.path`, the absolute-realpath
> identity). **Rev 2** (2026-07-20) — reshaped after an independent adversarial review found the rev-1
> draft modeled only the variable-value/storage layer and dropped the dimension + attribute layer (the
> layer that makes the result sufficient for a Zarr tail without re-opening the file). Review findings
> and their resolutions are recorded inline (marked *R2:*).
> Scoped to the structured-result increment used by the public stage API and
> frozen handoff schema.
> The `inspect → plan → execute` **primitives** are step 4B, designed separately
> (`ncarnate-stage-api.md`, forthcoming); this doc defines only the *result object* those primitives
> return. **This is the artifact step 5 freezes into a JSON Schema** — get the shape right here.
> **Codebase facts** below were verified against `ncarnate` on branch `feat/stage-api-4a` off
> `main` `73fe5af` on 2026-07-20: `recompress` at [`core.py:45`](../../ncarnate/core.py#L45)
> (`-> str`), `_write_verified` at [`core.py:205`](../../ncarnate/core.py#L205),
> `_copy_variables` / `_copy_dimensions` at [`core.py:328`](../../ncarnate/core.py#L328) /
> [`core.py:276`](../../ncarnate/core.py#L276), `_verify_group` at
> [`core.py:434`](../../ncarnate/core.py#L434), `preflight_destinations` at
> [`convert/preflight.py:100`](../../ncarnate/convert/preflight.py#L100) (returns a 4-tuple, **no
> digest**), `verify_sha256` at [`convert/integrity.py:63`](../../ncarnate/convert/integrity.py#L63)
> (computes the digest but returns `None`), `AuditResult` / `ConversionPlan` at
> [`audit/models.py:121`](../../ncarnate/audit/models.py#L121) /
> [`audit/models.py:99`](../../ncarnate/audit/models.py#L99), the code registry at
> [`audit/codes.py`](../../ncarnate/audit/codes.py) (`RULESET_VERSION = 4`), and
> `SCHEMA_VERSION = 1` at [`audit/models.py:32`](../../ncarnate/audit/models.py#L32).

## Context / problem

A downstream integration — the frozen-handoff schema (step 5) and the materialized-Zarr-v3 tail
(step 6) — must **perform and understand** an ncarnate conversion without invoking the CLI, parsing
logs, or importing private functions (gate G4). Today the outputs of a conversion are:

- `recompress(...) -> str` returns only the destination **path** ([`core.py:181`](../../ncarnate/core.py#L181)).
  Everything else it knew — the detected format, the group/dimension tree, the per-variable dtype and
  effective encoding, the preserved packing declarations, which coordinates it reconstructed vs
  skipped, the sanitized-name companions, the verification it ran — is discarded or visible only as
  log lines and file side effects.
- The manifest path aggregates `ConvertRecord(path, reason, code)`
  ([`convert/models.py:63`](../../ncarnate/convert/models.py#L63)) — enough to know *that* a file
  converted or *why* one failed, but nothing about *what* the conversion produced.

So automation that wants the digests, the executed structure/encoding, or the conversion-verification
status has to scrape terminal output or re-open the files and guess. Action 13's done-when is the
inverse: **automation does not need to scrape logs to determine what happened.** And a Zarr tail
(step 6) must build ordinary chunks + declared codecs/fill/dimension metadata *from the handoff
record*, not by re-opening the netCDF — which means the record must carry the file's **structure**,
not just a flat list of variable names.

## Goals & non-goals

**Goals**

1. A single versioned `OperationResult` value type describing one executed conversion, with **its own
   schema version constant** (following the `SCHEMA_VERSION` / `RULESET_VERSION` precedent) so step 5
   freezes against it independently of the audit record schema.
2. The **same** structure exposed as a Python object **and** as JSON, via a `to_record()` returning a
   JSON-safe dict (the established audit-model convention) plus a `canonical_form()` /
   `canonical_json()` with a deterministic, nondeterminism-excluded byte serialization for
   golden-hash tests.
3. Carry the action-13 / step-5 field list in full, including the **dimension + attribute layer**:
   source & destination identities + detected formats; source/output sizes + sha256; operation +
   requested encoding options; the output's **group tree** (per-group dimensions with
   name/size/unlimited, group/global attributes, and per-variable dtype, endianness, dimension
   *names*, **effective** encoding, and the full attribute set including the `scale_factor` /
   `add_offset` / `_FillValue` packing declarations the fidelity contract preserves); group/name
   mappings (`hdf4_name` / `hdf4_eos_name` / `<attr>__hdf4_name` companions, each with its owner
   path); coordinate actions **generated vs skipped**; warnings with stable registry codes; ncarnate
   **and adapter** versions; elapsed time.
4. **Separate conversion-verification status from scientific-validation status** — structurally, both
   as records (not bare strings), present from v1, so step 5 freezes the separation and can attach a
   validation record without a shape change. ncarnate only ever populates the conversion-verification
   side.
5. Populate it from the existing execute engine (`_write_verified` region of `recompress`) **without
   changing `recompress`'s released `-> str` signature**, and collect it through the manifest run and
   the summary renderer.
6. Verification-status wording, **per verifier**, that does not overclaim beyond `_verify_lossless`
   (netCDF path) or `hdf4.verify_conversion` (HDF4 path, which is *additive* and transforms NUL
   padding) — overclaim-guard lens against [`docs/fidelity-notes.md`](../fidelity-notes.md).

**Non-goals (scope fence)**

- **No `inspect`/`plan`/`execute` primitives, no batch preflight, no lazy iteration** — step 4B. This
  doc defines only the value the future `execute` returns and threads it through the current entry
  points internally.
- **No JSON Schema file, no `retention` field, no formal freeze** — step 5, which *freezes* this
  object. This doc must be *sufficient* for step 5; it does not do step 5's work. The caller-owned
  `retention` field explicitly stays out (step 5 action 5 step 3 owns it).
- **No Zarr, no bounded-memory streaming, no `--jobs`, no compression-policy vocabulary** (steps 6,
  10, 15, 16) — the result *reports* effective encoding but introduces no policy surface.
- **No change to `recompress`'s return type**, no new status/issue codes, no re-opening the frozen
  audit record schema.
- **No scientific validation** performed by ncarnate — only the *record* that names its absence.
- **No "executed plan hash" field** — derivable from `canonical_form(options + structure)`; a step-5
  concern (R2 finding 11, accepted as a step-5 non-goal).

## Approach

### Where it comes from — the three seams, unchanged in role

The result is assembled from data three existing seams already compute (or trivially can); 4A only
stops throwing it away.

| Field group | Source seam |
|---|---|
| source identity, detected format | `detect_format` + `os.path.getsize` ([`core.py:87-93`](../../ncarnate/core.py#L87)) |
| source sha256 | **execute streams it itself** (`sha256_of_file`); *R2 finding 4* — preflight computes then discards it ([`integrity.py:97`](../../ncarnate/convert/integrity.py#L97)) and `--allow-unverified` computes none, so it is not reusable-for-free |
| destination path, operation (recompress vs convert) | `recompress` destination resolution ([`core.py:102-165`](../../ncarnate/core.py#L102)) |
| group tree: dims, group/var attributes, per-var dtype/dim-names/effective encoding | a single read-back of the **committed** output (the same shape `_verify_group` walks, [`core.py:434`](../../ncarnate/core.py#L434)) |
| name mappings, coordinate actions | the HDF4 write step's companion attributes / the geolocation reconstruction |
| conversion-verification status | `_verify_lossless` / `hdf4.verify_conversion` ([`core.py:416`](../../ncarnate/core.py#L416)) |
| output size + sha256 | the same post-replace read-back |
| adapter versions | `netCDF4` lib/HDF5 version constants, `pyhdf`/HDF4 version, `numpy` — runtime-queryable |

The engine already re-opens both files to verify; 4A adds one read-back of the **committed** output
to record its *effective* structure and encoding (what the netCDF library actually wrote — including
library-chosen chunking a contiguous source did not request) and its digest. Ground truth, not the
requested options echoed back (*R2 finding 1 note:* `_copy_variables` applies `zlib`/`shuffle`/
`complevel` uniformly from the run options — only the read-back makes them per-variable truth).

### Module & type layout

New top-level module `ncarnate/result.py` (top-level because `core`, `convert`, and the future stage
API all consume it — mirrors how `audit/codes.py` is the shared code namespace). All types are stdlib
dataclasses with a JSON-safe `to_record()`, exactly like `audit/models.py`.

```
OPERATION_RESULT_SCHEMA_VERSION = 1     # own constant; bumped only on a breaking result-shape change

OperationResult
├─ source        : SourceIdentity     # path, detected_format, size_bytes, sha256
├─ destination   : OutputIdentity     # path, container_format ("NETCDF4"), size_bytes, sha256
├─ operation     : str                # "recompress" | "convert"  (reuse ConversionPlan.operation tokens)
├─ options       : EncodingOptions    # zlib, shuffle, complevel, geolocation  (the REQUESTED run)
├─ structure     : GroupNode          # ── the output's group tree, read back from the committed file
├─ name_mappings : list[NameMapping]  # sanitized→original companions (HDF4 path; [] otherwise)
├─ coordinates   : CoordinateActions  # generated:[str]; skipped:[SkippedCoordinate{name,reason,code}]
├─ verification  : VerificationRecord # ── conversion-integrity (per-verifier), SEPARATE from validation
├─ validation    : ValidationRecord   # ── scientific-validation, an object; status="not_performed" (ncarnate never varies it)
├─ environment   : Environment        # ncarnate_version + adapter_versions{netcdf_c, libhdf5, pyhdf, libhdf4, numpy}
├─ warnings      : list[ResultWarning]# non-fatal notes: {code, message, context} (registry codes)
└─ elapsed_seconds : float            # time.monotonic() around the execute engine
# schema_version is injected by to_record() from the constant, never stored per-instance
```

The **dimension + attribute layer** (R2 MUST-FIX 1–3) lives in a recursive `GroupNode`, mirroring the
tree `_verify_group` already walks:

```
GroupNode                    # one group; root path "/"
├─ path        : str
├─ dimensions  : list[Dimension]   # {name, size, unlimited}        ← gives array shape + unlimited-append
├─ attributes  : list[Attribute]   # group/global attrs, incl. verbatim StructMetadata.0 on HDFEOS_INFORMATION
├─ variables   : list[Variable]
└─ groups      : list[GroupNode]   # recursive; empty/metadata-only groups included (HDFEOS_INFORMATION)

Variable
├─ name        : str
├─ dtype       : str               # numpy dtype str
├─ endian      : str               # "native" | "big" | "little"
├─ dimensions  : list[str]         # dimension NAMES → shape resolves against the group's Dimension list
├─ encoding    : {zlib, shuffle, complevel, chunksizes|None}   # EFFECTIVE, read back from output
└─ attributes  : list[Attribute]   # full set: scale_factor, add_offset, _FillValue, units, coordinates, grid_mapping, …

Attribute
├─ name         : str
├─ storage_type : str              # "NC_STRING" | "NC_CHAR" | numpy dtype str  ← preserves the KD-L6 distinction
└─ value        : <JSON-safe>      # numpy scalar/array coerced; non-finite floats → "NaN"/"Infinity"/"-Infinity"
```

`_FillValue`, `scale_factor`, and `add_offset` are ordinary `Attribute`s on the variable — no special
fields. This is faithful: `_copy_variables` declares `_FillValue` at `createVariable` time and
re-declares scale/offset as attributes ([`core.py:367-399`](../../ncarnate/core.py#L367)), so on
read-back all three appear in `ncattrs()` exactly as `_verify_attributes` compares them
([`core.py:503`](../../ncarnate/core.py#L503)). Carrying the full attribute set also captures the
embedded-NUL `<name>__hdf4_encoding` uint8 companion and the `<attr>__hdf4_name` companions
([`fidelity-notes.md:42-54`](../fidelity-notes.md)) for free.

`NameMapping` = `{netcdf_name, original_name, kind, parent_path}` where kind ∈ {variable, dimension,
attribute, group, grid}. **`parent_path` disambiguates** an attribute rename that can recur on many
owners (two variables can each carry a `Scan Offset`; R2 MUST-FIX 6). Name mappings are also derivable
from the `hdf4_name`/`<attr>__hdf4_name` companion attributes now carried in `structure`, but the flat
`name_mappings` list is the ergonomic index a consumer scripts against.

### The verification / validation separation (load-bearing for step 5)

Two records, both present in every result, both objects (R2 SHOULD-FIX 8 — a bare enum string can't
hold step 5's "validation record"):

```
VerificationRecord                                  # conversion-integrity — ncarnate ALWAYS fills this
├─ status          : "verified"                     # the only success value; a failure RAISES (no result)
├─ verifier        : str                            # "ncarnate._verify_lossless" | "ncarnate.hdf4.verify_conversion"
├─ verifier_version: str                            # ncarnate __version__ (the code that ran the check)
└─ method          : str                            # PER-VERIFIER wording, scoped to fidelity-notes (below)

ValidationRecord                                    # scientific-validation — ncarnate NEVER varies this
├─ status          : "not_performed"                # ncarnate sets only this; a pipeline sets its own
├─ validator       : str | None = None              # the pipeline's validator identity
├─ method          : str | None = None
└─ record          : dict | None = None             # the pipeline's validation evidence (step 5's "validation record")
```

`method`, **per verifier** (R2 SHOULD-FIX 7), scoped exactly to the contract:

- `_verify_lossless` (netCDF→netCDF): *"re-read value-identical raw arrays (`np.array_equal`,
  `equal_nan` for float; complex excluded), dtype / dimension (incl. unlimited) / attribute (incl.
  NC_STRING vs NC_CHAR storage type) / group-tree equal; storage-only changes."*
- `hdf4.verify_conversion` (HDF4→netCDF): *"SDS values re-read value-identical (bit-for-bit integer /
  char8, `equal_nan` float); reconstructed geolocation is **additive** (the output tree is a superset
  of the SDS payload); character-attribute NUL padding stripped and embedded-NUL attributes recoded to
  uint8 per the fidelity contract."*

Both claim re-read value identity within the dtype scope, **not** scientific correctness — the two are
different questions and the schema keeps them different records.

An `execute` that fails verification **raises** (`VerificationError`) and atomically leaves no output
(the existing `_write_verified` contract, [`core.py:241-256`](../../ncarnate/core.py#L241)); there is
no `OperationResult` for a failed conversion, so `status` has no failure enum value (KD6). The manifest
loop records a failed execute as `ConvertRecord(path, reason, code)` exactly as today — already
scriptable via `.code` (F2), no log-scraping — satisfying action 13's failure path without a digest of
an output that does not exist.

### JSON: `to_record()`, `canonical_form()`, `canonical_json()`

- `to_record() -> dict` — the **full** JSON-safe payload for the journal: everything, including
  `elapsed_seconds`, absolute paths, output digest, and `environment`. Injects `schema_version` and
  `ncarnate_version`. Numpy scalars/arrays coerced to Python scalars/lists; **non-finite floats
  encoded as the strings `"NaN"` / `"Infinity"` / `"-Infinity"`** so the payload is strict RFC-8259
  JSON (a NaN fill value is real and must round-trip through a schema validator).
- `canonical_form() -> dict` — the **deterministic subset** for golden hashing (R2 MUST-FIX 5).
  **Excludes** the per-run / per-machine / per-library-version fields that make a full-payload hash
  flaky: `elapsed_seconds`, the absolute `source.path` / `destination.path`, `destination.sha256`
  (HDF5-library-version-dependent bytes), and `environment.adapter_versions`. **Keeps** the structural
  content that is deterministic for a fixture + ncarnate version: `source.sha256` (deterministic),
  `operation`, `options`, `structure`, `name_mappings`, `coordinates`, `verification.method/status`,
  `warnings`. Residual risk: library-*default* chunking on a contiguous source could still vary by
  HDF5 version — so the step-5 golden fixture uses a source with **explicit** chunking (documented in
  the fixture), keeping effective encoding deterministic.
- `canonical_json(result) -> str` — `json.dumps(canonical_form(), sort_keys=True,
  separators=(",", ":"), ensure_ascii=False, allow_nan=False)`. The exact function step 5's golden
  **hash** test pins.

### Relationship to `ConvertRecord` / `ConvertResult`

**`OperationResult` does not replace `ConvertRecord`; the converted record carries it** (KD1, rev 3).
`ConvertRecord` gains `result : OperationResult | None` — populated for a converted record, `None` for a
skip or a failure (nothing executed → no digest). `ConvertResult.converted` stays `list[ConvertRecord]`,
so the manifest-relative `path` (the summary/scripting handle, distinct from the absolute-realpath
`OperationResult.source.path`) and the existing `.code` are unchanged, and every current caller/test
keeps working. The summary renderer prints each converted record's size delta from `record.result`
(`!r`-escaped, KD9). A one-shot `recompress` caller still gets a `str`; the rich result is reachable
through the manifest run today (`record.result`) and through the 4B `execute` primitive for a one-shot
call.

**G4 in 4A** is demonstrated through the public manifest path: `convert_manifest` (public) returns a
`ConvertResult` whose `converted[i].result` is a fully-understandable `OperationResult` — a downstream
consumer performs and understands a conversion with no CLI, no log parsing, and no private imports
(`tests/test_stage_api_g4.py`). The 4B primitives formalize the same reachability for a single call.

### Manifest journal

4A adds an optional `--result-journal <path>` to `convert --manifest`: one `OperationResult.to_record()`
JSON object per line, per converted file (action 13's "manifest journal"; the machine record a pipeline
consumes). `render_summary` is unchanged in spirit — tallies plus per-record reasons — and additionally
prints each conversion's size delta. The heavy `structure` payload lives only in the JSON journal,
never the human summary.

## Key decisions

- **KD1 — new `OperationResult`, `ConvertRecord` survives and carries it (rev 3).** `ConvertRecord` also
  models **skips** and **pre-execution failures** where nothing executed and a digest is meaningless;
  forcing those through a rich all-`None` result is worse than the honest split. `ConvertRecord` gains an
  optional `result` field (populated only where a conversion executed) rather than `converted` changing
  element type — preserving the load-bearing manifest-relative `path`.
- **KD2 — `recompress(...) -> str` unchanged (released contract).** An internal
  `_recompress_result(...) -> OperationResult`; `recompress` returns `result.destination.path`. 4B's
  public `execute(plan) -> OperationResult` wraps the same internal.
- **KD3 — own schema-version constant.** `OPERATION_RESULT_SCHEMA_VERSION`, independent of audit
  `SCHEMA_VERSION`. Step 5 freezes *this* number; the canonical-hash golden test is its drift tripwire.
- **KD4 — verification and validation are separate **records**, always present.** Named structurally
  from v1; ncarnate sets `validation.status = "not_performed"` and never varies it, but the record's
  `validator`/`method`/`record` slots let a pipeline attach step 5's validation record without a shape
  bump.
- **KD5 — record the full **effective** structure, read back from the committed output.** Dimensions,
  attributes, per-variable dtype/dim-names/encoding — ground truth, so a Zarr tail builds shape +
  `dimension_names` + codecs + fill without re-opening the file (G5/G6).
- **KD6 — failure raises; no failure enum on `verification.status`.** Preserves the fail-loud, atomic
  "no bad output survives" contract; a failed conversion has no verified output to describe.
- **KD7 — warnings and skipped-coordinate codes reuse the existing registry.** `ResultWarning.code` and
  `SkippedCoordinate.code` are drawn from `audit/codes.py` `ALL_CODES` — one stable namespace.
- **KD8 — two serializations: full `to_record()` for the journal, deterministic `canonical_form()` for
  hashing.** Non-finite floats → strings; canonical form excludes elapsed/paths/output-digest/adapter
  versions and `allow_nan=False`. Resolves the rev-1 determinism contradiction (R2 MUST-FIX 5).
- **KD9 — `record.path` / mapping names stay `!r`-escaped in any textual rendering** (untrusted-input
  boundary; [`convert/report.py:49`](../../ncarnate/convert/report.py#L49)). In JSON they are data
  values, safe by construction.
- **KD10 — execute computes the source digest itself** (R2 MUST-FIX 4); it does not assume a
  preflight-supplied one (preflight discards it, and `--allow-unverified` computes none). `source.sha256`
  is the digest over the bytes execute actually read. 4B *may* thread a verified digest through the plan
  to avoid a second hash on the manifest path — a 4B optimization, not a 4A dependency.

## Alternatives considered

- **Flat `variables` list with only value/storage fields (the rev-1 shape).** Rejected by the R2
  review: no dimensions → no shape / `dimension_names`; no attributes → no packing declarations, no
  `HDFEOS_INFORMATION`/`StructMetadata.0`, no CF `coordinates`/`grid_mapping`; a Zarr tail would have to
  re-open the file, violating G5/G6. The recursive `GroupNode` is the fix.
- **Extend `ConvertRecord` in place.** Rejected (KD1): overloads one type across executed / skipped /
  failed, most fields `None` for two of three; and it is manifest-only.
- **Change `recompress` to return `OperationResult`.** Rejected (KD2): breaks a released public API for
  no gain a wrapper doesn't give.
- **A single `verification_status` string.** Rejected (KD4): collapses the two concerns step 5 forbids
  conflating, and has nowhere to hold step 5's validation record.
- **A bare `scientific_validation: "not_performed"` string** (rev-1). Rejected (R2 SHOULD-FIX 8): step 5's
  validation record wouldn't fit → post-freeze shape bump; a `ValidationRecord` object avoids it.
- **One shared `method` string.** Rejected (R2 SHOULD-FIX 7): overclaims on the additive / NUL-transforming
  HDF4 path; `method` is per-verifier.
- **Hash the full `to_record()` for the golden test** (rev-1). Rejected (R2 MUST-FIX 5): elapsed time,
  absolute paths, and HDF5-version-dependent output digest make it flaky; hash `canonical_form()`.
- **Echo requested encoding instead of effective.** Rejected (KD5): misreports library-chosen chunking.
- **Per-result code space for warnings.** Rejected (KD7): fragments the stable namespace.
- **Emit Python `NaN` tokens** (`allow_nan=True`). Rejected (KD8): invalid JSON; a conforming step-5
  validator would reject the very fill values ncarnate must round-trip.

## Risks

- **Result / journal size.** The full `structure` for a file with thousands of variables, plus verbatim
  `StructMetadata.0` (kilobytes), is large. Kept — it is what makes the result sufficient for a Zarr
  tail (G5); the human summary elides it, only the JSON journal carries it.
- **Output hashing cost on multi-GB granules.** `sha256_of_file` streams; hashing every output doubles
  the read of a large file. v1: on by default (action 13 wants output hashes). A future opt-out /
  bounded hash is action-10 territory — noted, not built. `destination.sha256` is excluded from the
  canonical hash anyway (HDF5-version-dependent).
- **Golden-hash determinism residual.** Library-default chunking could vary by HDF5 version even inside
  `canonical_form()`; mitigated by pinning the step-5 golden fixture to an **explicitly-chunked**
  source. Flagged for step 5.
- **Over/under-claiming verification.** The per-verifier `method` strings are the overclaim surface;
  pinned to `fidelity-notes.md` and re-checked in review (overclaim-guard). They claim re-read value
  identity within the dtype scope, and — on HDF4 — additive geolocation + NUL transforms, not
  correctness.
- **Untrusted names in the journal.** Mapping originals, attribute values, and `record.path` come from
  untrusted inputs; they are data values in JSON (safe) but any *textual* rendering keeps `!r` (KD9).
- **Schema drift vs step 5.** Any field added after step 5 freezes is a
  `OPERATION_RESULT_SCHEMA_VERSION` bump; the canonical-hash golden test is the tripwire.

## Open questions

1. **`--result-journal` flag name & default.** Propose off-by-default, explicit path (symmetric with
   `audit --output`). Confirm against the audit family's conventions in 4B, where the CLI is otherwise
   touched.
2. **`operation` token reuse.** Reuse `ConversionPlan.operation`'s exact strings
   ([`audit/models.py:109`](../../ncarnate/audit/models.py#L109)) so inspect≙audit and the executed
   result agree — confirm the tokens when 4B wires `inspect`. (R2 NIT 12.)
3. **Elapsed-time boundary.** Wrap just the execute engine (write + verify + read-back), or include
   plan resolution? Recommend engine-only for v1 (excluded from the canonical hash regardless).
4. **Adapter-version probing surface.** Exact APIs for `libhdf5`/`netcdf_c` (`netCDF4.__hdf5libversion__`
   / `netCDF4.getlibversion()`) and `libhdf4`/`pyhdf` — confirm at implement time; degrade to `None`
   where a runtime is absent (Windows-pip has no pyhdf, KD-L3) rather than raising.

## Rollout / migration

1. Add `ncarnate/result.py` (the types + `OPERATION_RESULT_SCHEMA_VERSION` + `to_record` +
   `canonical_form` + `canonical_json`), pure data + coercion, no I/O — unit-testable in isolation.
2. A read-back builder that turns a committed netCDF file into a `GroupNode` tree (reuses the traversal
   shape of `_verify_group`).
3. Thread it through the execute engine (internal `_recompress_result`); `recompress` returns
   `.destination.path` (no signature change).
4. `ConvertRecord.result : OperationResult | None`; the manifest loop attaches it; summary size-delta
   section; `render_result_journal` + the `--result-journal` CLI flag; export `OperationResult` /
   `canonical_json` / `OPERATION_RESULT_SCHEMA_VERSION` on `ncarnate.__all__`.
5. Golden `canonical_json` serialization + hash tests over an **explicitly-chunked** fixture; one
   real-fixture (AMSR-E grid) result fixture checked into `tests/`; unit tests for non-finite-float and
   NC_STRING/NC_CHAR attribute coercion.
6. Gauntlet (`pytest -q`, `ruff check .`, `build` + `twine check`) → `/pre-merge-review` → **local**
   `--no-ff` merge. **No push, no release** — shipping step 4 is a separate owner-gated ask (CHANGELOG +
   version bump + conda sync happen then, not now).

No data migration: `OperationResult` is additive; the audit record schema and `recompress`'s public
signature are untouched. The primitives (4B) and the frozen JSON Schema + `retention` field (step 5)
build **on** this object, in their own branches.
