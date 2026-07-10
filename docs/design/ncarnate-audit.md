# `ncarnate audit` + the migration-manifest contract — Design

> **Status:** ratified design, build in progress in **this** repo. This copy is the
> **authoritative** build spec (per Key decision 9); the originating design lives in
> the owner's internal planning records and no longer governs.
> **Provenance:** synthesised from two planning conversations (internal planning
> `digest/synthesis.md` §5 spec, §8 open decisions, §9 framing) and their source
> transcripts. Codebase facts below were first verified against ncarnate **2.0.2** and
> **re-verified against 2.0.3** on 2026-07-10 (`ncarnate/constants.py`); the two known
> drift corrections from the 2.0.2 grounding are folded in (packed-geolocation raise
> site; fixture layout confirmed under `tests/fixtures/data/`).
> **This doc is the design pass** the synthesis asked for: it resolves synthesis §8
> and designs the flagship increment of the scientific-data-modernization tool family.

## Context / problem

Two independent planning conversations converged on the same thesis: ncarnate is the
flagship, and the highest-leverage move is a family of tools that turn legacy
HDF4/HDF-EOS2/ASCII Earth-science archives into modern, validated data without silently
changing scientific values. The first increment both sources point at (one explicitly,
one via the "validation gatekeeper" role) is a **read-only archive assessor**: *"what is
in this archive, what can ncarnate safely do with it, what will block conversion?"*

Institutions won't hand a converter a terabyte archive on faith. They *will* run a
read-only audit — and the audit's output, if it is a stable machine-readable contract
rather than a disposable report, becomes the planning substrate every later family member
(orchestration, dashboards, catalogs) builds on. Separately, ncarnate's JOSS/pyOpenSci
case is currently blocked on bursty-history + no-external-use evidence (synthesis §6);
building the audit publicly and incrementally is the same path that unblocks publication.

## Goals & non-goals

**Goals**

1. A read-only `ncarnate audit <path>` subcommand: recursive discovery, format detection,
   metadata-only inspection, per-file classification into a stable status taxonomy, named
   blockers/warnings via stable issue codes, terminal summary, JSONL + CSV output.
2. The **migration-manifest contract**: a versioned JSONL record schema that freezes
   path, checksum, status, issues, and conversion plan — designed now, so
   `ncarnate convert --manifest` and every later family member bolt onto it unchanged.
3. **Audit/convert agreement by construction and by test**: the audit predicts exactly
   what the converter does, because it calls the converter's own predicates and because
   CI asserts prediction == outcome on every fixture.
4. Accumulate the public, iterative development history the JOSS/pyOpenSci case needs —
   as a side effect of real work, never as theater.

**Non-goals (scope fence for this increment)**

- No `--mode sample` or `--mode preflight` (flags reserved; design below says where they
  slot in). No HTML dashboard. No Parquet/DuckDB/STAC output. No duplicate detection.
- No `ncarnate-lake` orchestration, no kerchunk/Zarr, nothing from the Gemini pipeline —
  the audit only has to not block it (see "The seam").
- No text-track (ASCII) auditing. The contract is designed format-agnostic; the text
  track is a different product (decision 4).
- No network access, ever, and no write access to audited files, ever.

## Approach

### Where it lives (decision 2, resolved: subcommand, not a package)

`audit` ships **inside ncarnate** as a subpackage + CLI subcommand. Everything an auditor
needs already lives there and is battle-tested: magic-byte format detection
(`ncarnate/formats.py:detect_format`), the ODL/PVL `StructMetadata` parser
(`ncarnate/eos/structmeta.py:parse` → `EosGrid`/`EosSwath` dataclasses), GCTP projection
supportability (`ncarnate/eos/gctp.py:projection_info`), allocation guards
(`ncarnate/limits.py:check_array_size`), and the typed exception taxonomy
(`ncarnate/errors.py`). A separate `ncarnate-audit` package would either duplicate that
or force an extension API into existence prematurely. The seam to a separate package is
the *contract* (taxonomy, codes, manifest schema), not the code — if a second producer
ever needs it, the schema gets published as a spec; the Python stays put.

### New code layout (in the ncarnate repo)

```
ncarnate/audit/
    __init__.py    # public API: audit_path(), AuditOptions
    models.py      # AuditOptions, AuditReport, AuditResult, AuditIssue,
                   # StructureAudit, ConversionPlan — stdlib dataclasses,
                   # each with to_record() -> dict (JSON-safe)
    inspect.py     # metadata-only inspection: one file -> raw facts
    classify.py    # facts -> status + issues (the exception->code mapping)
    codes.py       # the issue-code registry + RULESET_VERSION
    report.py      # terminal summary, JSONL writer, CSV writer
```

Dependencies point one way: `audit` imports `formats`, `eos.*`, `limits`, `errors`;
nothing outside `cli.py` imports `audit`. No new third-party dependencies.

### The metadata-only inspection path

ncarnate today has no inspect-only entry point — `hdf4.read_hdf4()` reads science arrays
(`dataset.get()` at `hdf4.py:434`). The audit **must not** do that in its default mode
(institutions scan terabytes). `inspect.py` composes an array-free pass from parts that
already exist:

1. `formats.detect_format(path)` — magic bytes only → `NETCDF3 | HDF5 | HDF4 | UNKNOWN`.
2. **HDF4/HDF-EOS2:** `pyhdf` `SD(path, SDC.READ)` using only `.info()`/attribute reads
   (names, ranks, shapes, dtypes — never `.get()`); `StructMetadata.0` text →
   `structmeta.parse()`; the field→structure/dimension index (today's private
   `hdf4._field_index`, to be exposed or mirrored). **Reuse the existing single-parse
   flow:** `hdf4._read_payload` already parses the (potentially many-part) ODL text
   exactly once and returns the parsed `EosStructMetadata` (see the comment at
   `hdf4.py:357`, from the 2.0.3 remediation `e95c420`) — factor the array-free metadata
   walk out of `_read_payload`/`_read_attributes` rather than re-parsing.
3. **netCDF3/HDF5:** `netCDF4.Dataset` structure walk (dims/vars/types/attrs, no values)
   → `already_modern`, flagging what the recompression path would reject (user-defined
   types, `core.py:_copy_variables` — raises `UnsupportedTypeError` at `core.py:320`).
4. **UNKNOWN / non-science files:** recorded, counted, never opened further.

### Classification: collected predicates, not forked logic

The converter is fail-loud: unsupported constructs *raise* typed exceptions at known
sites (`gctp.projection_info` → `UnsupportedProjectionError`; `hdf4._decorate_swaths` →
`UnsupportedGeolocationError` for index maps / merged fields / missing Lat-Lon;
`hdf4._normalize_coordinate` → `UnsupportedGeolocationError` for packed (scaled/offset)
Lat-Lon; `structmeta.parse` → `EosParseError`; `limits.check_array_size`; name-collision
raises in `hdf4._reserve_names`). The audit's core move is to **call those same
predicates and catch, rather than re-implement** — one source of truth, so the audit
cannot disagree with the converter about *rules*, only about *depth* (see honesty note
below).

One honest complication: the exception taxonomy is **coarser** than the code registry.
`UnsupportedGeolocationError` alone is raised at several distinct sites
(`_decorate_swaths`, `_reserve_names`-adjacent collisions, `_normalize_coordinate`,
`_attach_swath_coordinates`) that must map to *different* issue codes, so catching by
type alone can't disambiguate, and parsing exception *messages* would be exactly the
brittle coupling this design exists to avoid. The fix is part of this increment: raise
sites gain a structured identity — `NcarnateError.__init__` grows an optional
`code: str | None` keyword, and each mapped raise site passes its registry code (a
mechanical, behavior-preserving sweep; messages unchanged). `classify.py` then reads
`exc.code`, falling back to a type-level default code for any site not yet annotated.
Disambiguation is carried as data on the exception, never scraped from prose.

Where a converter check is currently entangled with array reads (parts of
`_decorate_swaths` / `_attach_swath_coordinates` inspect actual Lat/Lon values), the
audit runs the **metadata-visible subset** in this increment and the rest waits for
`--mode sample`/`preflight`. That is a depth limitation, stated, not a rule fork.

**Status taxonomy** (adopted verbatim from synthesis §5 — it survived review):
`ready`, `ready_no_geolocation`, `already_modern`, `unsupported`, `malformed`, `unsafe`,
`unknown`. Severity folding: any `blocker` issue ⇒ non-ready status; `warning`s ride
along on any status.

**Initial issue-code registry** — deliberately small, derived from the existing exception
taxonomy so every code is already exercised by the converter's tests:

| Code | Converter site it mirrors |
|---|---|
| `EOS_UNSUPPORTED_PROJECTION` | raise in `gctp.projection_info` (and its `gctp` helpers) |
| `EOS_STRUCTMETADATA_MALFORMED` | raises throughout `structmeta.parse` (`EosParseError`) |
| `SWATH_DIMMAP_UNRESOLVED` | `_axis_specification`'s unresolvable-axis *sentinel* (it returns, not raises) and the downstream `UnsupportedGeolocationError` in swath decoration |
| `SWATH_GEOLOCATION_UNSUPPORTED` | raises in `hdf4._decorate_swaths` / `_attach_swath_coordinates` (index maps, merged fields, missing Lat-Lon) and `hdf4._normalize_coordinate` (packed/scaled Lat-Lon, `hdf4.py:758`) |
| `NETCDF_NAME_COLLISION` | raises in `hdf4._reserve_names` / SDS sanitize collision (`TreeGroup.add_dimension`) |
| `UNSUPPORTED_TYPE` | raises in `hdf4._attribute_value`, `hdf4._read_dataset`, `core._copy_variables` |
| `DECLARED_ALLOCATION_TOO_LARGE` | raise in `limits.check_array_size` (bare `NcarnateError`) |
| `FORMAT_UNRECOGNIZED` | `formats.detect_format` returning `UNKNOWN` (no raise) |

Note two of these are *return-sentinel* predicates, not raise sites — the registry maps
converter *decisions* to codes; the `exc.code` mechanism above covers the raising
subset, and sentinel-returning predicates are mapped explicitly in `classify.py`.

Codes are **append-only**; a code is never renamed or repurposed. `codes.py` carries
`RULESET_VERSION: int`, bumped whenever classification *semantics* change (new code, a
predicate tightened/loosened). Archive managers script against these strings — that
stability is the product.

**Honesty rule:** a status is a *prediction at a depth*. Every record carries the `mode`
it was audited at, and `ready` documents as "no known blocker at this audit depth", not a
warranty. `--mode preflight` (later) is what upgrades prediction toward proof.

### One record schema = the migration manifest (no second format)

The per-file JSONL output **is** the manifest — there is no separate "report JSON" vs
"manifest" format to drift apart. Record schema v1:

```jsonc
{
  "schema_version": 1,
  "ncarnate_version": "2.1.0",
  "ruleset_version": 1,
  "mode": "metadata",
  "audited_at": "2026-07-10T18:30:00Z",
  "root": "/archive",                // audit root as invoked, absolute
  "path": "granules/AMSR_E_L3_SeaIce12km_B02_20020619.hdf",  // relative to root
  "size_bytes": 63124218,
  "sha256": null,                    // present iff --checksum
  "format": "HDF4",
  "status": "ready",
  "structures": [ { "type": "GRID", "name": "...", "projection": {...},
                    "geolocation_plan": {...} } ],
  "issues": [ { "code": "...", "severity": "info|warning|blocker",
                "message": "...", "context": {...} } ],
  "plan": { "operation": "convert|recompress|copy_payload",
            "geolocation_method": "...", "output_format": "NETCDF4" }
}
```

The file `ncarnate audit /archive --output audit.jsonl` writes is byte-for-byte what
`ncarnate convert --manifest audit.jsonl --status ready` (increment 2) consumes.
Consumption re-verifies `sha256` before touching data and refuses records where it is
`null` unless explicitly overridden — so `--checksum sha256` is opt-in for cheap scans
(hashing terabytes is not free) but required for a manifest you intend to *execute*.
CSV is a flat projection of the same records (one row per file, top blocker inline) for
spreadsheet triage; JSONL is the contract, CSV is a view.

Every JSONL line is a file record of this one shape — **no header line, no trailer**
(uniform lines keep per-line schema validation and `CSV rows == JSONL lines` trivially
true). The audit root is carried redundantly on each record (`root` + relative `path`);
JSONL is verbose by nature and compresses well, and uniformity beats the few bytes.

### CLI integration (the one pre-existing-surface risk)

`ncarnate`'s CLI is a flat argparse command with a positional `path` and a
`ncarnate = "ncarnate.cli:main"` console script — no subparsers. Adding subcommands
naively breaks every existing invocation. Design: dispatch in `main()` **before**
argparse — if `argv[1] == "audit"`, hand `argv[2:]` to the audit parser; otherwise fall
through to today's parser untouched. `audit` becomes a reserved word (colliding filenames
need `./audit`), which is acceptable and documented. `ncarnate convert` is *also* added
as an explicit alias for the legacy flat behavior so documentation can teach a uniform
verb-first CLI, but the bare form is **not** deprecated in this increment. The audit
loop reuses `cli._get_files`' enumeration behavior (recursion,
extension filtering) — with the difference that unrecognized files are *counted and
classified `unknown`*, not skipped, when explicitly targeted.

Terminal output: the synthesis §5 summary shape (readiness % by files *and bytes*,
blockers ranked by affected bytes, geolocation-plan census). Bytes-ranked blockers is the
detail that makes the report an executive artifact; keep it in the MVP.

### Python API

```python
from ncarnate.audit import audit_path, AuditOptions
report = audit_path("/archive", AuditOptions(recursive=True, mode="metadata",
                                             checksum=None))
report.summary.ready_bytes; report.files[0].issues
```

`audit_path` is the one public entry; the CLI is a thin shell over it. It gets added to
`ncarnate/__init__.py.__all__` and `docs/api.rst` (which currently promises "a single
`recompress` entry point" at `api.rst:4` — that sentence changes).

### Testing (this is the credibility mechanism, not a chore)

1. **Agreement tests** — for every fixture under `tests/fixtures/data/` — **5 trimmed
   real HDF-EOS2 granules** (`tests/fixtures/data/hdfeos2/`: `amsre_5daysnow_trim.hdf`,
   `amsre_seaice12km_trim.hdf`, `mod03_trim.hdf`, `myd05_trim.hdf`, `raingrid_trim.hdf`)
   and **4 synthetic netCDF** (`tests/fixtures/data/netcdf/`: `endianness.nc`,
   `nested_groups.nc`, `packed_fill.nc`, `unlimited_dim.nc`): `audit` predicts `ready` ⇒
   `recompress` must succeed; predicts a blocker code ⇒ `recompress` must raise the mapped
   exception. This single parametrized test keeps the taxonomy honest forever. (Reuse the
   existing `conftest.py` fixture discovery.)
2. **Contract tests** — JSONL records validate against a checked-in schema; codes are
   append-only (test fails if a known code disappears); CSV row count == JSONL count.
3. **Read-only guarantee** — audit a fixture tree, assert mtimes/checksums unchanged
   (cheap, and it *is* the product's central promise).
4. `raw_granules`-marked tests extend agreement checks to full local granules, self-skip
   in CI (existing pattern in `tests/test_raw_granules.py`).

## Key decisions (resolves synthesis §8)

1. **Scope = narrow.** This increment is `ncarnate audit` (metadata mode) + the manifest
   contract. Both sources recommend it, the grounding confirms the raw material exists,
   and every bigger option consumes this one's output. *(§8.1)*
2. **Subcommand, not a separate package.** Reuse beats extraction; the extractable seam
   is the schema, not the code. *(§8.2)*
3. **Binary vs text = two products sharing a contract, not one framework.** ncarnate
   stays the binary-track tool. The record schema is format-agnostic (nothing in it is
   HDF-specific except code strings and `structures[]` contents), so a future text-track
   auditor emits the same schema with its own code namespace (`TEXT_*`). No shared
   framework is built until two real producers exist. *(§8.3)*
4. **Text-track ownership deferred; default = with the format reader.** When built,
   `BaseScientificTextParser` starts life in/alongside `noaa-gml-file-reader` as the
   reference implementation — not in ncarnate, which never touches ASCII. Extracting a
   `sci-text` package is a later, evidence-driven move. *(§8.4)*
5. **pyOpenSci pre-submission inquiry files in parallel, not after.** It is 1–2 weeks,
   low-commitment, and converts the development-history question from a guess into an
   editor's answer while the audit work builds the very history being asked about.
   Outbound → **Erick sends it** (and note the whole evidence path assumes the work is
   *pushed publicly*, which is currently gated by the standing local-only rule). *(§8.5)*
6. **Second spinoff: leaning `ncarnate.xarray`, decided later.** Cheapest surface, meets
   users in-workflow, and audit telemetry (which products people scan) should inform it.
   Recorded as a leaning, not a commitment. *(§8.6)*
7. **pydlock stays narrow.** No streaming/directory/format-spec investment; if
   `sealed-rocrate` ever happens it uses `age` as the crypto backend. pydlock is out of
   family v1. *(§8.7)*
8. **License: MIT, inherited.** `audit` is part of ncarnate (MIT, shipped, cited via
   Zenodo). The copyleft aspiration applies to future standalone science projects, not
   retro-relicensing the flagship.
9. **This doc lives in `internal planning` originally, moves with the build.** With
   implementation started, this design has been copied into `ncarnate/docs/design/` and
   **this copy is authoritative** (ncarnate's sdist already excludes `docs/design` — dev
   material, correctly).
10. **`structures[]` appears in metadata mode for netCDF/HDF5 too, not only HDF4/EOS**
    *(resolves former open question 1)*. The netCDF3/HDF5 branch already walks
    dims/vars/types/attrs to compute `already_modern`, so populating `structures[]` from
    that walk is free — no extra I/O, no array reads. Emitting it uniformly across formats
    keeps the record schema format-agnostic (a consumer never special-cases "HDF4 has
    structures, netCDF doesn't"). The `structures[]` entry for a modern file describes its
    groups/variables at the same metadata depth; HDF-specific richness (projection,
    geolocation plan) is simply absent where it doesn't apply.

## Alternatives considered

- **Maximal first increment (Gemini's legacy→PyTorch/PySpark pipeline).** Rejected as the
  *start*: it presumes an inventory/feasibility layer that doesn't exist yet (the audit),
  has ~10× the surface, and its cloud-native concerns (S3 throttling, serverless
  indexing) are premature for a tool with no institutional users yet. It stays the north
  star; the manifest is its intake format.
- **Mid increment (`ncarnate-lake` orchestration).** Rejected for the same dependency
  reason — lake's first step *is* "audit the tree". Building lake first means building
  audit anyway, inside a bigger blast radius.
- **Separate `ncarnate-audit` package.** Rejected: duplicates format/EOS/projection/limit
  logic or forces an unstable extension API; also splits the JOSS story instead of
  strengthening the flagship's.
- **Re-implementing checks as standalone audit rules** (a rules engine, decoupled from
  converter exceptions). Rejected: guaranteed drift between what audit predicts and what
  convert does; the whole value is agreement. Calling the converter's own predicates and
  reading `exc.code` keeps rule identity in one place — drift then requires changing the
  converter itself, at which point the agreement tests catch it.
- **Separate report format vs manifest format.** Rejected: two schemas describing the
  same facts always diverge; one record schema, two projections (JSONL contract, CSV
  view).
- **Full argparse subparser migration now** (moving legacy conversion under a mandatory
  `convert` verb). Rejected for this increment: breaks every existing `ncarnate <path>`
  invocation and script; the pre-dispatch shim gets subcommands without a compat break.

## Risks

- **Contract frozen too early.** Stable codes are the product, but v1 might carve the
  wrong joints. Mitigated by deriving every v1 code from an already-exercised exception
  site, `schema_version` + `RULESET_VERSION` fields from day one, and append-only
  discipline (add codes, never repurpose).
- **Prediction/reality gap.** Metadata-only `ready` can still fail at conversion (corrupt
  payloads, packed-geolocation surprises live behind array reads). Mitigated by the
  honesty rule (mode recorded, "ready" defined as depth-relative), agreement tests on
  every fixture, and `preflight` mode as the designed escape hatch. This gap is also the
  *fixture factory*: every real-world mismatch is a new test fixture and a public issue —
  exactly the external-use evidence JOSS wants.
- **CLI regression.** The pre-dispatch shim is deliberately dumb; 8 existing CLI tests
  (`tests/test_cli.py`) plus new shim tests must pass unmodified (except additions).
- **Perf on huge trees.** Metadata mode is I/O-light but `pyhdf` open/parse per file adds
  up over 10⁶ files. MVP is single-process with `tqdm` (matches existing CLI); the
  per-file worker function is written pure (path in → record out) so a `--jobs N`
  process pool is a later flag, not a redesign.
- **Evidence path is gated on publishing.** All the JOSS/pyOpenSci value assumes public,
  incremental commits — currently every repo is local-only by standing rule. If that
  gate stays closed while audit gets built, build it in normal small commits anyway
  (honest history survives a delayed push; a squashed dump doesn't).

## Open questions

*(Resolved before build — nothing here blocks increments 1–3. Retained for provenance.)*

- ~~Should `structures[]` appear in metadata mode for netCDF/HDF5 files (`already_modern`),
  or only for HDF4/EOS?~~ **Resolved → Key decision 10 (yes, for both — free during the
  structure walk).**
- ~~Exact `ncarnate convert --manifest` flag surface (status filters, destination
  templating).~~ **Out of scope for this build.** Designed in **increment 2's own design
  pass** (a fast-follow, separate doc); only the record schema is frozen here. No fork for
  increments 1–3.

## Rollout / sequencing (in `~/projects/ncarnate`, each its own branch)

1. **Scaffold:** CLI pre-dispatch shim + `audit` parser + `models.py`/`codes.py` +
   discovery + format detection + terminal summary (statuses: `already_modern`,
   `unknown`, `unsafe` only). Ships something honest immediately.
2. **The core:** EOS metadata inspection (`inspect.py`), predicate classification
   (`classify.py`), full taxonomy + v1 codes, agreement tests over all fixtures.
3. **The contract:** JSONL/CSV emission, `--checksum sha256`, schema contract tests,
   docs (`api.rst`, README golden path "audit an archive in 5 minutes"), CHANGELOG.
4. **Fast-follows (separate designs):** `ncarnate convert --manifest`, `--mode sample`,
   HTML report, `--jobs`.

In parallel (owner actions, not code): Erick files the pyOpenSci pre-submission inquiry;
the public-push decision for ncarnate's ongoing work gets made deliberately.
