#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The verified-netCDF4 handoff contract — the *consumer* side of the seam.

Step 5 froze ``OperationResult.to_record()`` as a versioned JSON Schema; step 6
(a separate downstream package, the Zarr tail) is the first artifact to consume
it. This module ships that contract inside the wheel and gives a consumer two
gates it must pass a *received* record through before it materializes anything:

* :func:`validate_handoff` — the record is a well-formed handoff per the frozen
  schema (draft-07, ``additionalProperties: false`` on every structural
  object). Uses a tiny stdlib JSON-Schema-subset validator — no third-party
  dependency (the audit-contract spec constraint), extended with ``$ref``
  resolution (the recursive group tree) and schema-valued
  ``additionalProperties`` (the open-valued ``adapter_versions`` map).

* :func:`check_materializable` — the record is not merely *valid* but *safe to
  build a store from*. A schema-valid record can still be a trap: the degraded
  read-back record (:func:`ncarnate.core._minimal_result`) carries an empty
  ``structure`` under a ``verified`` status, and a naive consumer would
  "successfully" materialize an **empty** store from it. This gate refuses that
  class (and an unknown ``schema_version``, and any record still bearing the
  ``RESULT_READBACK_INCOMPLETE`` warning) loudly.

**Consumer obligations the record does NOT carry (read this before writing a
consumer).** The record is *metadata only* — it describes shapes / dtypes /
dimensions / encodings / coordinate identities, but holds **no array bytes**
(not even the reconstructed lat/lon coordinate *values*). Every byte a store
needs lives in the ``destination`` netCDF4 (ncarnate's verified output). A
consumer therefore MUST:

1. read array data from the ``destination`` file, never re-open ``source.path``
   (the original granule — out of scope, and the G5 gate proves you never need
   it to know the store's *structure*);
2. verify the located destination file against ``destination.sha256`` before
   reading it, and refuse on mismatch — ``canonical_form`` deliberately drops
   the path/size/digest, so the digest is the only durable byte identity;
3. treat ``destination.path`` as **advisory** (it is absolute and
   machine-specific); the record + its destination file are one handoff unit;
4. pass the record through :func:`check_materializable` (this refuses the
   empty-store trap and unknown versions).

**Hostile-record bound.** Every public gate first runs
:func:`precheck_handoff`, an *iterative* walk that bounds nesting depth
(``HANDOFF_MAX_DEPTH``) and node count (``HANDOFF_MAX_NODES``), refuses
reference cycles, and refuses non-JSON values (``bytes``, non-finite floats,
non-string keys, arbitrary objects). A record that breaks any bound is refused
with :class:`~ncarnate.errors.HandoffError` (or a reason string from the
non-raising helpers) — never a leaked ``RecursionError`` or ``TypeError``.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

import json
import math
from functools import lru_cache
from importlib import resources
from typing import Any

from ncarnate.audit.codes import HANDOFF_NOT_MATERIALIZABLE
from ncarnate.audit.codes import HANDOFF_SCHEMA_INVALID
from ncarnate.audit.codes import RESULT_READBACK_INCOMPLETE
from ncarnate.errors import HandoffError
from ncarnate.result import OPERATION_RESULT_SCHEMA_VERSION

_SCHEMA_RESOURCE = "handoff.schema.json"

# The record-complexity bounds every public gate enforces before any
# recursive work. Depth counts nested containers including the root: a real
# record is ~9 deep at a variable attribute value and gains two levels per
# nested netCDF group, so 128 leaves ample room while staying far below the
# interpreter's recursion limit (the schema walk spends at most two frames per
# level). The node bound caps the work a single record can demand of a
# consumer; full-granule record sizes still need qualification.
HANDOFF_MAX_DEPTH = 128
HANDOFF_MAX_NODES = 2_000_000


def _scalar_error(value : Any, path : str) -> str | None:

    # A JSON scalar is str / bool / None / int / finite float. Anything else
    # (bytes, numpy, sets, objects, NaN/Infinity) cannot be a handoff value.
    if value is None or isinstance(value, (str, bool)):
        return None
    if isinstance(value, int):
        return None
    if isinstance(value, float):
        if math.isfinite(value):
            return None
        return f"{path}: non-finite float {value!r} is not a JSON value"
    return f"{path}: {type(value).__name__} is not a JSON value"


def _children(container : Any, path : str):

    if isinstance(container, dict):
        return ((f"{path}.{key}" if isinstance(key, str) else path, key, value)
                for key, value in container.items())
    return ((f"{path}[{index}]", "", item) for index, item in enumerate(container))


def precheck_handoff(record : Any, *,
                     max_depth : int | None = None,
                     max_nodes : int | None = None) -> str | None:

    '''

    Bounded, **iterative**, non-raising structural precheck of an untrusted
    record. Returns a reason string if the record exceeds ``max_depth``
    nested containers (default ``HANDOFF_MAX_DEPTH``), exceeds ``max_nodes``
    total values (default ``HANDOFF_MAX_NODES``), contains a reference cycle,
    a non-string object key, or a non-JSON value; ``None`` if it is within
    bounds. Runs before the recursive schema walk in every public gate so a
    hostile record is refused deliberately instead of leaking a
    ``RecursionError``. Containers are ``dict`` / ``list`` / ``tuple``; the
    typed schema positions still require lists.

    '''

    if max_depth is None:
        max_depth = HANDOFF_MAX_DEPTH
    if max_nodes is None:
        max_nodes = HANDOFF_MAX_NODES

    if not isinstance(record, (dict, list, tuple)):
        return _scalar_error(record, "$")

    nodes = 1
    frames = [(id(record), _children(record, "$"))]
    on_path = {id(record)}

    while frames:
        container_id, iterator = frames[-1]
        try:
            path, key, child = next(iterator)
        except StopIteration:
            frames.pop()
            on_path.discard(container_id)
            continue

        if not isinstance(key, str):
            return f"{path}: object key is not a string"

        nodes += 1
        if nodes > max_nodes:
            return (
                f"record exceeds the handoff node bound ({max_nodes} values)"
            )

        if isinstance(child, (dict, list, tuple)):
            if id(child) in on_path:
                return f"{path}: record contains a reference cycle"
            if len(frames) >= max_depth:
                return (
                    f"{path}: record exceeds the handoff nesting bound "
                    f"({max_depth} levels)"
                )
            frames.append((id(child), _children(child, path)))
            on_path.add(id(child))
            continue

        reason = _scalar_error(child, path)
        if reason is not None:
            return reason

    return None


def handoff_schema_path():

    '''

    The shipped, importlib-resources path to the frozen ``handoff.schema.json``.
    Resolves inside the installed wheel — a consumer that depends on
    ``ncarnate`` reads the single frozen contract, never a vendored copy.

    '''

    return resources.files("ncarnate.schemas").joinpath(_SCHEMA_RESOURCE)


@lru_cache(maxsize=1)
def load_handoff_schema() -> dict[str, Any]:

    '''

    Load and cache the frozen handoff JSON Schema as a dict.

    '''

    return json.loads(handoff_schema_path().read_text(encoding="utf-8"))


# --- a minimal stdlib JSON-Schema-subset validator ------------------------
# The subset the frozen schema uses. Extends the audit-contract validator
# (tests/audit/test_contract.py) with $ref resolution (the recursive group
# tree) and schema-valued additionalProperties (the adapter_versions map). No
# third-party dependency — the audit-contract spec constraint.

_JSON_TYPES = {
    "object": dict, "array": list, "string": str, "null": type(None),
}


def _matches_type(instance : Any, json_type : str) -> bool:

    if json_type == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if json_type == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if json_type == "boolean":
        return isinstance(instance, bool)
    return isinstance(instance, _JSON_TYPES[json_type])


def _resolve_ref(ref : str, root : dict) -> dict:

    # Local JSON pointer only, e.g. "#/definitions/groupNode".
    if not ref.startswith("#/"):
        raise HandoffError(f"only local schema refs are supported: {ref!r}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _schema_errors(instance : Any, schema : dict, root : dict,
                   path : str = "$") -> list[str]:

    # $ref: resolve against the root and validate against the target. The
    # instance shrinks with depth (groups -> [] at a leaf), so the recursive
    # groupNode ref cannot loop.
    if "$ref" in schema:
        return _schema_errors(instance, _resolve_ref(schema["$ref"], root), root, path)

    errors : list[str] = []

    if "type" in schema:
        types = schema["type"]
        types = [types] if isinstance(types, str) else types
        if not any(_matches_type(instance, t) for t in types):
            return [f"{path}: expected {types}, got {type(instance).__name__}"]

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required {key!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key in instance:
            if key in properties:
                continue
            if additional is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(additional, dict):
                errors += _schema_errors(instance[key], additional, root, f"{path}.{key}")
        for key, subschema in properties.items():
            if key in instance:
                errors += _schema_errors(instance[key], subschema, root, f"{path}.{key}")

    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            errors += _schema_errors(item, schema["items"], root, f"{path}[{index}]")

    return errors


def schema_errors(record : dict[str, Any]) -> list[str]:

    '''

    Return the list of schema violations in ``record`` against the frozen
    handoff schema — empty iff the record is well-formed. Non-raising; the
    caller decides whether to raise, log, or aggregate. A record outside the
    :func:`precheck_handoff` bounds is reported as a single violation without
    entering the recursive schema walk.

    '''

    reason = precheck_handoff(record)
    if reason is not None:
        return [reason]

    schema = load_handoff_schema()
    return _schema_errors(record, schema, schema)


def validate_handoff(record : dict[str, Any]) -> None:

    '''

    Raise :class:`~ncarnate.errors.HandoffError` unless ``record`` is a
    well-formed handoff per the frozen schema. This is a *structural* gate; it
    does NOT guarantee the record is safe to materialize a store from — pass it
    through :func:`check_materializable` for that.

    '''

    errors = schema_errors(record)
    if errors:
        raise HandoffError(
            "record is not a valid handoff: " + "; ".join(errors),
            code=HANDOFF_SCHEMA_INVALID,
        )


def _variable_count(group : Any) -> int:

    # Total variables anywhere in the group tree, walked iteratively (no
    # recursion to exhaust) and once per node (a cycle cannot loop it).
    # Defensive: this runs over an untrusted record, so a non-dict node or
    # non-list field contributes zero rather than raising.
    total = 0
    stack = [group]
    seen : set[int] = set()
    while stack:
        node = stack.pop()
        if not isinstance(node, dict) or id(node) in seen:
            continue
        seen.add(id(node))
        variables = node.get("variables")
        if isinstance(variables, list):
            total += len(variables)
        children = node.get("groups")
        if isinstance(children, list):
            stack.extend(children)
    return total


def materializability_error(record : Any) -> str | None:

    '''

    Return a human-readable reason ``record`` is unsafe to materialize a store
    from, or ``None`` if it is safe. Intended to run *after*
    :func:`validate_handoff` (see :func:`check_materializable`), but written
    **defensively** so it never leaks a stdlib exception on a hostile record at
    this untrusted boundary — a malformed shape fails closed to *unsafe* (a
    reason string), never to ``None`` (safe). Refuses, in order:

    * a record outside the :func:`precheck_handoff` bounds (nesting depth,
      node count, a reference cycle, or a non-JSON value such as a NaN
      ``size_bytes``);
    * a non-object record, or an unknown ``schema_version`` (this ncarnate
      expects ``OPERATION_RESULT_SCHEMA_VERSION``);
    * a degraded read-back record — one still bearing the
      ``RESULT_READBACK_INCOMPLETE`` warning (the write was verified but the
      structure was never read back, so the record cannot describe the store);
    * an empty ``structure`` (no variables anywhere) while the destination is
      non-empty (``size_bytes > 0``) — the silent-empty-store trap: a
      schema-valid, ``verified``-labelled record that a naive consumer would
      turn into an empty Zarr store.

    '''

    reason = precheck_handoff(record)
    if reason is not None:
        return reason

    if not isinstance(record, dict):
        return f"record is not a JSON object: {type(record).__name__}"

    version = record.get("schema_version")
    if version != OPERATION_RESULT_SCHEMA_VERSION:
        return (
            f"unknown schema_version {version!r}; this ncarnate consumes "
            f"version {OPERATION_RESULT_SCHEMA_VERSION}"
        )

    warnings = record.get("warnings")
    warnings = warnings if isinstance(warnings, list) else []
    if any(
        isinstance(w, dict) and w.get("code") == RESULT_READBACK_INCOMPLETE
        for w in warnings
    ):
        return (
            f"record carries a {RESULT_READBACK_INCOMPLETE} warning: the "
            "conversion was verified and committed but its structure was never "
            "read back, so the record cannot describe the store to build"
        )

    destination = record.get("destination")
    size_bytes = destination.get("size_bytes", 0) if isinstance(destination, dict) else 0
    if not isinstance(size_bytes, (int, float)) or isinstance(size_bytes, bool):
        size_bytes = 0
    if _variable_count(record.get("structure")) == 0 and size_bytes > 0:
        return (
            "record describes no variables but the destination is non-empty "
            f"({size_bytes} bytes): materializing it would yield a silently "
            "empty store"
        )

    return None


def check_materializable(record : dict[str, Any]) -> None:

    '''

    Raise :class:`~ncarnate.errors.HandoffError` unless ``record`` is safe to
    materialize a store from. Runs :func:`validate_handoff` first (a
    non-well-formed record is never materializable), then the semantic gate of
    :func:`materializability_error`.

    '''

    validate_handoff(record)
    reason = materializability_error(record)
    if reason is not None:
        raise HandoffError(
            "record is not materializable: " + reason,
            code=HANDOFF_NOT_MATERIALIZABLE,
        )
