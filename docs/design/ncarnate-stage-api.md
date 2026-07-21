# `inspect → plan → execute` — the ncarnate stage API (step 4B) — Design

> **Status:** built as the public stage-API increment. Scoped to the
> `inspect → plan → execute` integration boundary.
> Builds directly on **4A** ([`ncarnate-operation-result.md`](ncarnate-operation-result.md)) — `execute`
> returns the 4A `OperationResult`. Verified against `ncarnate` on `feat/stage-api-4b` off
> `main` `ba4d89f` (2026-07-20): `recompress` / `_recompress_result` at
> [`core.py:79`](../../ncarnate/core.py#L79), `preflight_destinations` at
> [`convert/preflight.py:100`](../../ncarnate/convert/preflight.py#L100), `_audit_file` / `audit_path`
> at [`audit/__init__.py:200`](../../ncarnate/audit/__init__.py#L200), `convert_manifest` loop at
> [`convert/__init__.py:130`](../../ncarnate/convert/__init__.py#L130).

## Context / problem

4A gave a downstream integration a structured result. 4B gives it the **verbs** to produce that result
directly — assess a file, plan a conversion, execute the plan — instead of reaching for the CLI or the
manifest machinery. Today the behavior exists but is fused inside three entry points; 4B extracts the
seams (a lift, not a rewrite) and makes the one-shot and manifest APIs thin callers, so `execute` is
the single execution path (gate G4, for a one-shot call). It also resolves the three non-blocking
follow-ups the 4A pre-merge review flagged.

## Goals & non-goals

**Goals**

1. Three public primitives, reusing the existing seams (not parallel reimplementations):
   - `inspect(source) -> AuditResult` ≙ the audit path's per-file assessor (`audit_path` on one file).
   - `plan(assessment, destination, …) -> Plan` — an **immutable** plan carrying the resolved source,
     the concrete destination (incl. the HDF4 `--in-place` derived `<stem>.nc` sibling), detected
     format, operation, and encoding options.
   - `execute(plan) -> OperationResult` — the verified write-then-atomic-replace engine wrapped to
     return the 4A result.
2. **Lazy batch iteration:** `execute_batch(plans) -> Iterator[OperationResult]` — stream results as
   each completes, single-threaded.
3. **One-shot and manifest APIs become callers of the primitives.** `recompress(...) -> str`
   (released, unchanged signature) becomes `plan → execute-core`; `convert_manifest` builds a `Plan`
   per preflighted record and calls `execute`.
4. **Two hard invariants, tested:** embedded/library operation (a) **never starts a nested worker
   pool** and (b) **never deletes a successful netCDF4 output**.
5. **Fold in the 4A follow-ups:** (1) `recompress` no longer hashes src+dst for a discarded result;
   (2) scope the `canonical_json` "across machines" wording; (3) a post-commit read-back error yields
   a minimal *verified* result with a warning, not a misreported failure.
6. Public names on `ncarnate.__all__` — the supported stage-API contract.

**Non-goals (scope fence)**

- **No `--jobs` / parallel execution** (step 16) — `execute_batch` is serial-lazy; the no-nested-pool
  invariant is the *floor* it must not cross.
- **No new whole-run collision engine** — the manifest's `preflight_destinations` already is the batch
  preflight; 4B does not duplicate it. `execute_batch` is lazy execution over already-planned items.
- **No JSON Schema freeze, no `retention` field** (step 5). No re-opening the 4A result shape (only a
  new `RESULT_READBACK_INCOMPLETE` warning code is added to the shared registry).
- **No change to `recompress`'s `-> str` signature** or to any audit/convert public shape beyond adding
  the primitives + the one warning code.

## Approach — the engine, layered

The 4A `_recompress_result` fused path-resolution + write/verify + result-build. 4B splits it into
reusable layers so `recompress`, `execute`, and `convert_manifest` share exactly the right slice:

```
_plan_from_path(src, dst, …) -> Plan        # realpath, detect, derive destination, guards
plan(assessment, …)          -> Plan        # same resolver, sourced from an AuditResult
_execute_core(plan)          -> dst_path    # build write/verify closures from plan.detected_format,
                                            #   _write_verified (atomic; never deletes a good output)
execute(plan)                -> OperationResult   # source identity (pre-write) + _execute_core + read-back
recompress(src, dst, …)      -> str         # _execute_core(_plan_from_path(...))  — cheap, no digests
convert_manifest             -> ConvertResult     # preflight -> Plan per record -> execute(plan)
execute_batch(plans)         -> Iterator[OperationResult]   # serial-lazy; no worker pool
```

- **`Plan`** — a `@dataclass(frozen=True)`: `source`, `destination` (concrete; `== source` for an
  in-place netCDF recompression), `detected_format`, `operation`, `options` (4A `EncodingOptions`).
  `in_place` is a derived property (`destination == source`). Immutable, so a plan is a faithful,
  reviewable description of what will happen — `execute` resolves no paths and reads no policy of its
  own.
- **`_execute_core`** contains the HDF4-vs-netCDF branch (unchanged closures); the HDF4 runtime gate
  (`require_hdf4_runtime`) fires here, before any output, preserving KD-L4. It is the only writer, and
  `_write_verified` is the only mover — a verified output is atomically renamed into place and never
  deleted.
- **`execute`** captures source identity *before* `_execute_core` (in-place safety, 4A KD10), then
  builds the result. **Read-back robustness (follow-up 3):** the write is already verified and
  committed by the time the read-back runs, so a read-back exception must not present a completed
  conversion as a failure — `execute` catches it, logs it, and returns a minimal result
  (`verification.status = "verified"`, empty structure) carrying a `RESULT_READBACK_INCOMPLETE`
  warning. The output is intact (never deleted).
- **`recompress` (follow-up 1):** goes through `_execute_core`, not `execute`, so a one-shot `-> str`
  caller no longer pays the source+output SHA-256 and the read-back it never sees. Behavior and return
  value are byte-identical to before (same resolver, same write/verify).

**`inspect`** wraps `audit_path` on a single file and returns `report.files[0]` — the audit's own
assessor, no parallel one. **`plan`** resolves `assessment.root/assessment.path` and delegates to
`_plan_from_path` (re-detecting from bytes, not trusting the assessment's declared format — the
untrusted-input rule), and honors an `assessment.status == "ready_no_geolocation"` prediction as
`geolocation=False`, mirroring the manifest's per-status override.

## Key decisions

- **KD1 — one execution path.** `execute` (via `_execute_core`) is the single writer; `recompress` and
  `convert_manifest` are callers. No behavior is duplicated — it is lifted.
- **KD2 — `Plan` is immutable and self-contained.** `execute` needs nothing but the plan: no ambient
  policy, no re-resolution. This is what makes a plan reviewable and a batch preflightable.
- **KD3 — the HDF4 runtime gate lives in `_execute_core`, not `plan`.** Planning is runtime-free (you
  can plan on a Windows-pip install); the refusal still fires before any output (KD-L4).
- **KD4 — read-back failure degrades, never misreports.** A committed, verified conversion is a
  success even if the rich read-back trips; the `RESULT_READBACK_INCOMPLETE` warning records the gap.
- **KD5 — `execute_batch` is serial-lazy.** A generator, one item at a time, no pool — the
  no-nested-pool invariant is structural, and lazy iteration lets a consumer stream without holding
  every result in memory.
- **KD6 — reuse `AuditResult` as the assessment.** `inspect` returns the audit record type; no new
  assessment shape (the plan/spec said reuse it).

## Alternatives considered

- **Make `recompress` call `execute` (full result) and return `.destination.path`.** Rejected
  (follow-up 1): re-introduces the discarded-digest cost on the hot one-shot path. `_execute_core` is
  the shared slice both need.
- **A new batch-preflight engine over assessments.** Rejected (non-goal): `preflight_destinations`
  already does whole-run collision refusal; duplicating it invites divergence. `execute_batch` is lazy
  execution, not re-planning.
- **`inspect` returns a fresh `Assessment` type.** Rejected (KD6): a parallel assessor is exactly what
  the plan warns against; `AuditResult` already carries format/structures/issues/plan.
- **Keep `plan` trusting `assessment.format`.** Rejected: the manifest is untrusted input;
  re-detecting from bytes is the established rule (a tiny extra read on the one-shot path).

## Risks

- **Refactor regression on the released `recompress`.** Mitigated by the existing recompress/HDF4/
  manifest suites (322+ tests) — the resolver and write/verify are ported verbatim into
  `_plan_from_path` / `_execute_core`; the golden 4A canonical hash must stay unchanged (execute
  produces the identical result).
- **Read-back degradation hides a real bug.** The minimal-result path logs the exception (traceback
  visible) and marks the record with a warning code, so it is never silent — a genuine reader bug stays
  discoverable while a whole-archive run survives.
- **`RESULT_READBACK_INCOMPLETE` in the code registry** bumps `RULESET_VERSION` (a convert/result code,
  per the `DESTINATION_COLLISION` precedent) — additive, append-only.

## Rollout / migration

1. `codes.py`: add `RESULT_READBACK_INCOMPLETE`, bump `RULESET_VERSION` 4 → 5.
2. `core.py`: `Plan` + `_plan_from_path` + `_execute_core` + `_verifier_for` + `execute` +
   `_minimal_result` + `execute_batch`; `recompress` becomes the thin caller (`_recompress_result`
   removed — `execute` supersedes it). Scope the `canonical_json` docstring (follow-up 2, in
   `result.py`).
3. `ncarnate/stage.py`: public `inspect` + `plan` (+ re-export `Plan`/`execute`/`execute_batch`).
4. `convert/__init__.py`: build a `Plan` per preflighted record, call `execute`.
5. `ncarnate/__init__.py`: export `inspect`, `plan`, `execute`, `execute_batch`, `Plan`.
6. Tests: the inspect→plan→execute flow; both invariants (monkeypatch pool constructors to raise; force
   a read-back error and assert the output survives + a verified-with-warning result); lazy-batch
   ordering; a public-API-only single-call G4 gate. Update the 4A white-box helper to the new engine.
7. Gauntlet (`pytest -q`, `ruff check .`, `build` + `twine check`) → `/pre-merge-review` → **local**
   `--no-ff` merge. **No push, no release** (owner-gated).
