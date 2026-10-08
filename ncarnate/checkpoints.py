"""Atomic per-record resume checkpoints; no aggregate journal size ceiling.

The small journal descriptor owns a sibling records directory. Earlier 2.3.0
development journals are migrated without losing their prior records.
Concurrent writers and power-loss durability are not claimed.
"""
import hashlib
import json
import os
from pathlib import Path

from ncarnate.handoff import validate_handoff
from ncarnate.journal import ResultJournal, unlinked_path

MAX_RECORD_BYTES = 64 * 1024 * 1024
SCHEMA = "ncarnate-resume-2"


def records_path(journal):
    return unlinked_path(journal) + ".records"


def record_key(record):
    return os.path.normcase(os.path.abspath(record["destination"]["path"]))


def record_name(record):
    return hashlib.sha256(record_key(record).encode("utf-8")).hexdigest() + ".json"


def _read(path):
    if os.path.getsize(path) > MAX_RECORD_BYTES:
        raise ValueError("checkpoint record exceeds 64 MiB")
    with open(unlinked_path(path), encoding="utf-8") as stream:
        return json.load(stream)


def load_records(journal):
    saved = _read(journal)
    if not isinstance(saved, dict):
        raise ValueError("invalid resume descriptor")
    version = saved.get("schema_version")
    if version == "ncarnate-resume-1":
        values = saved.get("records")
        if not isinstance(values, list):
            raise ValueError("resume journal must contain a records list")
        pairs = ((None, record) for record in values)
    elif version == SCHEMA:
        directory = unlinked_path(records_path(journal))
        if not os.path.isdir(directory):
            raise ValueError("resume records directory is missing")
        pairs = ((path.name, _read(path)) for path in Path(directory).glob("*.json"))
    else:
        raise ValueError("unknown resume journal version")
    records = {}
    for name, record in pairs:
        validate_handoff(record)
        if name is not None and name != record_name(record):
            raise ValueError("checkpoint filename does not match its destination")
        # Legacy degraded records without a digest cannot authorize this output,
        # but must not prevent other completed outputs from being resumed.
        if not record["destination"]["sha256"]:
            continue
        key = record_key(record)
        if key in records:
            raise ValueError("duplicate completion records")
        records[key] = record
    return records, version


class CheckpointJournal:
    def __init__(self, journal, records, version):
        self.target = unlinked_path(journal)
        self.directory = records_path(journal)
        self.writer = None
        if version != SCHEMA:
            # Reserve first, so an unavailable parent cannot leave any outputs.
            descriptor = ResultJournal(self.target)
            created = False
            try:
                try:
                    os.mkdir(self.directory)
                    created = True
                except FileExistsError:
                    if version != "ncarnate-resume-1":
                        raise OSError(f"records directory already exists: {self.directory}")
                    self._check_interrupted_migration(records)
                for record in records.values():
                    self.reserve(record["destination"]["path"])
                    self.publish_record(record)
                descriptor.publish(json.dumps({"schema_version": SCHEMA}))
            except BaseException:
                try:
                    self.close()
                except OSError:
                    pass
                if created:
                    # Remove only files this attempt could have created. Never
                    # recursively remove an existing or externally changed tree.
                    for record in records.values():
                        try:
                            os.unlink(os.path.join(self.directory, record_name(record)))
                        except OSError:
                            pass
                    try:
                        os.rmdir(self.directory)
                    except OSError:
                        pass
                raise
            finally:
                descriptor.close()
                self.close()
        else:
            if not os.path.isdir(unlinked_path(self.directory)):
                raise OSError("resume records directory is missing")

    def _check_interrupted_migration(self, records):
        """Retry only a directory whose checkpoints agree with the old journal.

        Atomic checkpoint temps may remain after interruption. They are ignored
        and never deleted here; unknown files or changed records refuse reuse.
        """
        directory = Path(unlinked_path(self.directory))
        expected = {record_name(record): record for record in records.values()}
        for path in directory.iterdir():
            unlinked_path(path)
            if not path.is_file():
                raise OSError(f"unexpected entry in migration directory: {path}")
            if path.name in expected:
                if _read(path) != expected[path.name]:
                    raise ValueError(f"checkpoint conflicts with development journal: {path}")
            elif not path.name.startswith(".ncarnate-journal-"):
                raise OSError(f"unrecognized entry in migration directory: {path}")

    def reserve(self, destination):
        self.close()
        name = record_name({"destination": {"path": destination}})
        self.writer = ResultJournal(os.path.join(self.directory, name))

    def publish_record(self, record):
        if not record["destination"]["sha256"]:
            return
        text = json.dumps(record, ensure_ascii=True, allow_nan=False)
        if len(text.encode("utf-8")) + 1 > MAX_RECORD_BYTES:
            raise ValueError("checkpoint record exceeds 64 MiB; earlier checkpoints remain resumable")
        self.writer.publish(text)
        self.close()

    def close(self):
        if self.writer is not None:
            writer, self.writer = self.writer, None
            writer.close()
