"""plan_hash behavioural properties (panel C6).

`plan_hash` is the field a consumer keys idempotency on, yet it was covered
only indirectly (via the canonical golden). These pin its contract directly:

* a stable **request** identity — same source + same options ⇒ same hash,
  across independent executes and regardless of the (excluded) destination;
* an **option delta** changes it;
* it is **recomputable from the record alone** — a consumer's tamper check;
* the documented **null-digest degradation** — a non-null `source.sha256`
  participates, a null one drops the identity back to
  ``{operation, options, format, size}`` so the excluded source *path* can no
  longer distinguish two sources (which a consumer must refuse to key on).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

from ncarnate import core

_PACKED_FILL = Path(__file__).parent / "fixtures" / "data" / "netcdf" / "packed_fill.nc"


def _execute(tmp_path, dst="out.nc", **plan_kw):
    plan = core._plan_from_path(str(_PACKED_FILL), str(tmp_path / dst), **plan_kw)
    return core.execute(plan)


def _recompute_plan_hash(record: dict) -> str:
    # A consumer recomputing the identity from the record's own fields — the
    # exact projection plan_hash() hashes (result.py), record-side.
    projection = {
        "operation": record["operation"],
        "options"  : record["options"],
        "source"   : {
            key: record["source"][key]
            for key in ("detected_format", "size_bytes", "sha256")
        },
    }
    payload = json.dumps(
        projection, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_same_request_same_hash(tmp_path):
    # Same source + options, different (excluded) destination -> same hash.
    a = _execute(tmp_path, "a.nc").plan_hash()
    b = _execute(tmp_path, "b.nc").plan_hash()
    assert a == b


def test_option_delta_changes_hash(tmp_path):
    a = _execute(tmp_path, "a.nc", complevel=7).plan_hash()
    b = _execute(tmp_path, "b.nc", complevel=1).plan_hash()
    assert a != b


def test_plan_hash_recomputable_from_record(tmp_path):
    record = _execute(tmp_path).to_record()
    assert _recompute_plan_hash(record) == record["plan_hash"]


def test_digest_participates_and_null_digest_degrades(tmp_path):
    result = _execute(tmp_path)
    full = result.plan_hash()

    # A non-null digest participates: a different digest -> a different hash.
    other = dataclasses.replace(
        result, source=dataclasses.replace(result.source, sha256="0" * 64)
    )
    assert other.plan_hash() != full

    # Under a null digest the excluded source *path* cannot distinguish two
    # sources sharing {operation, options, format, size} — the documented
    # degradation. (No current execute() path emits null; constructed here.)
    null_a = dataclasses.replace(
        result,
        source=dataclasses.replace(result.source, sha256=None, path="/granules/a.hdf"),
    )
    null_b = dataclasses.replace(
        result,
        source=dataclasses.replace(result.source, sha256=None, path="/granules/b.hdf"),
    )
    assert null_a.plan_hash() == null_b.plan_hash()
    assert null_a.plan_hash() != full
