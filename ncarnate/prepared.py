"""Strict, explicit-output plans and digest-verified resumption.

These checks assume a cooperative filesystem. They do not prevent another
process replacing a path between verification and use. The legacy lazy
execute_batch API remains available for callers that own that coordination.
"""
import os
from dataclasses import dataclass

from ncarnate.core import Plan, _plan_from_path, execute
from ncarnate.destinations import validate_destinations
from ncarnate.errors import NcarnateError
from ncarnate.hashing import sha256_of_file
from ncarnate.handoff import validate_handoff
from ncarnate.journal import unlinked_path
from ncarnate.checkpoints import CheckpointJournal, load_records, records_path


@dataclass(frozen=True)
class PreparedPlan:
    plan: Plan
    source_size: int
    source_sha256: str


def _unlinked(path):
    try:
        return unlinked_path(path)
    except OSError as error:
        raise NcarnateError(f"link boundary is not allowed: {path}", code="DESTINATION_COLLISION") from error


def _check_parent(destination):
    parent = os.path.dirname(destination)
    if not os.path.isdir(parent) or not os.access(parent, os.W_OK | os.X_OK):
        raise NcarnateError(f"output parent must be an existing writable directory: {parent}", code="DESTINATION_COLLISION")


def prepare(source, destination, **encoding) -> PreparedPlan:
    """Bind source bytes and frozen options to an explicit, separate output.

Existing outputs are allowed at preparation so they can be resumed. Execution
will only accept one with matching completion evidence; it never overwrites it.
No HDF4 execution runtime is needed to prepare a plan.
"""
    if destination is None:
        raise NcarnateError("an explicit output is required", code="DESTINATION_COLLISION")
    source = _unlinked(source)
    destination = _unlinked(destination)
    _check_parent(destination)
    forbidden = set(encoding) - {"zlib", "shuffle", "complevel", "geolocation"}
    if forbidden:
        raise TypeError(f"unknown encoding options: {sorted(forbidden)}")
    plan = _plan_from_path(source, destination, **encoding)
    validate_destinations([(plan.source, plan.destination, plan.source)], allow_existing=True)
    return PreparedPlan(plan, os.path.getsize(plan.source), sha256_of_file(plan.source))


def prepare_batch(pairs, **encoding) -> tuple[PreparedPlan, ...]:
    """Consume a finite iterable of (source, destination), checking all paths.

Only small plan descriptors are retained; no scientific arrays are loaded.
Callers with unbounded streams must partition into coordinated finite batches.
"""
    prepared = tuple(prepare(source, destination, **encoding) for source, destination in pairs)
    _validate_batch(prepared, allow_existing=True, check_source=False)
    return prepared


def _check_source(item):
    _unlinked(item.plan.source)
    if (not os.path.isfile(item.plan.source)
            or os.path.getsize(item.plan.source) != item.source_size
            or sha256_of_file(item.plan.source) != item.source_sha256):
        raise NcarnateError(f"source changed since preparation: {item.plan.source}", code="SOURCE_CHANGED")


def _validate_batch(items, *, allow_existing, check_source=True):
    for item in items:
        if check_source:
            _check_source(item)
        _unlinked(item.plan.destination)
        _check_parent(item.plan.destination)
    validate_destinations([(p.plan.source, p.plan.destination, p.plan.source) for p in items],
                          allow_existing=allow_existing)


def _resume_record(item, record):
    validate_handoff(record)
    p = item.plan
    source = record["source"]
    destination = record["destination"]
    if (source["sha256"] != item.source_sha256 or source["size_bytes"] != item.source_size
            or source["detected_format"] != p.detected_format.name
            or record["operation"] != p.operation or record["options"] != p.options.to_record()
            or os.path.normcase(os.path.abspath(destination["path"])) != os.path.normcase(p.destination)
            or not os.path.isfile(p.destination)
            or os.path.getsize(p.destination) != destination["size_bytes"]
            or sha256_of_file(p.destination) != destination["sha256"]):
        raise NcarnateError(f"completion evidence does not match: {p.destination}", code="RESUME_MISMATCH")


def execute_prepared_batch(items, *, journal=None, resume=False):
    """Return complete handoff records, checkpointing each item atomically.

Every source/destination and every requested resume is checked before writes.
An existing file without matching journal evidence is refused. Journal failure
is reported separately: already committed netCDF files remain intact.
"""
    items = tuple(items)
    _validate_batch(items, allow_existing=resume)
    records = {}
    journal_version = None
    if journal is not None:
        journal = _unlinked(journal)
        if not resume and os.path.lexists(journal):
            raise NcarnateError("existing journal requires resume=True or a new journal path", code="JOURNAL_UNAVAILABLE")
        entries = [(p.plan.source, p.plan.destination, p.plan.source) for p in items]
        validate_destinations(entries + [(os.path.abspath(journal), None, "journal")], allow_existing=resume)
        # Include aliases of both inputs and outputs, including hard links.
        for p in items:
            for path in (p.plan.source, p.plan.destination):
                if (os.path.normcase(os.path.abspath(journal)) == os.path.normcase(path)
                        or (os.path.exists(journal) and os.path.exists(path) and os.path.samefile(journal, path))):
                    raise NcarnateError("journal aliases a conversion path", code="DESTINATION_COLLISION")
        if resume and os.path.exists(journal):
            try:
                records, journal_version = load_records(journal)
            except (OSError, ValueError, TypeError, KeyError, RecursionError, NcarnateError) as error:
                raise NcarnateError(f"invalid resume journal: {error}", code="RESUME_MISMATCH") from error
    elif resume:
        raise NcarnateError("resume requires a completion journal", code="RESUME_MISMATCH")
    reused = {}
    for item in items:
        key = os.path.normcase(item.plan.destination)
        if os.path.lexists(item.plan.destination):
            if key not in records:
                raise NcarnateError("existing output has no completion evidence", code="RESUME_MISMATCH")
            _resume_record(item, records[key])
            reused[key] = records[key]
    writer = None
    if journal is not None:
        sidecar = records_path(journal)
        validate_destinations(entries + [(sidecar, None, "journal records")], allow_existing=resume)
        if any(os.path.normcase(os.path.realpath(p.plan.source)).startswith(os.path.normcase(sidecar) + os.sep) for p in items):
            raise NcarnateError("source is inside the journal records directory", code="DESTINATION_COLLISION")
        try:
            writer = CheckpointJournal(journal, records, journal_version)
        except (OSError, ValueError) as error:
            raise NcarnateError(f"cannot reserve result journal {journal}: {error}; no conversions started", code="JOURNAL_UNAVAILABLE") from error
    output = []
    try:
        for item in items:
            key = os.path.normcase(item.plan.destination)
            if writer is not None:
                try:
                    writer.reserve(item.plan.destination)
                except OSError as error:
                    raise NcarnateError("cannot reserve result journal; no further conversions started", code="JOURNAL_UNAVAILABLE") from error
            _check_source(item)
            if key in reused:
                _resume_record(item, reused[key])
                record = reused[key]
            else:
                _unlinked(item.plan.destination)
                if os.path.lexists(item.plan.destination):
                    raise NcarnateError("destination appeared after preflight", code="DESTINATION_COLLISION")
                record = execute(item.plan).to_record()
            output.append(record)
            if writer is not None:
                try:
                    writer.publish_record(record)
                except (OSError, ValueError, NcarnateError) as error:
                    raise NcarnateError("conversion completed but its resume checkpoint could not be saved", code="JOURNAL_WRITE_FAILED") from error
    finally:
        if writer is not None:
            try:
                writer.close()
            except OSError:
                pass  # preserve the primary conversion/reporting exception
    return output


def execute_prepared(item, *, journal=None, resume=False):
    """Execute or verify one prepared plan, returning its handoff record."""
    return execute_prepared_batch((item,), journal=journal, resume=resume)[0]
