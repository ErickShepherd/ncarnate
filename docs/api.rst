API reference
=============

The library supports one-shot conversion, read-only audit, staged planning,
explicit-output prepared jobs, resumable batches, verified handoff records,
and isolated conversion workers. The APIs below are available from ``ncarnate``.

Conversion and recompression
----------------------------

.. autofunction:: ncarnate.recompress

Read-only audit
---------------

``audit_path`` assesses an archive without modifying it: it discovers files,
detects formats, inspects metadata (never reading science arrays), classifies
each file into a stable status taxonomy, and returns an
:class:`~ncarnate.audit.models.AuditReport`. The per-file JSONL output is a
versioned migration-manifest contract.

.. autofunction:: ncarnate.audit_path

.. autoclass:: ncarnate.AuditOptions
   :members:
   :undoc-members:

Manifest-driven conversion
--------------------------

``convert_manifest`` executes a migration manifest produced by ``audit_path``
(or ``ncarnate audit --output``): for each record whose status is selected it
re-verifies the recorded ``sha256`` before touching the file, confines the
source and output paths, and drives :func:`~ncarnate.recompress` into a mirrored
output tree. Per-record failures are isolated and tallied into a
:class:`~ncarnate.convert.models.ConvertResult` — the archive is never mutated
unless ``in_place`` is set.

Because a manifest is untrusted input, the read containment base must be
operator-controlled: set ``ConvertOptions.root`` (the CLI ``--root``) to anchor
source resolution to a directory you control, or ``allow_manifest_root``
(``--allow-manifest-root``) to explicitly trust the manifest's own recorded
``root``. With neither, ``convert_manifest`` refuses the run rather than trust
an attacker-controllable base.

.. autofunction:: ncarnate.convert_manifest

.. autoclass:: ncarnate.ConvertOptions
   :members:
   :undoc-members:

Staged planning
---------------

``inspect`` reads metadata, ``plan`` chooses an operation, and ``execute``
produces an operation result. The legacy lazy ``execute_batch`` does not
preflight future destinations. Use prepared batches for that guarantee.

.. autofunction:: ncarnate.inspect

.. autofunction:: ncarnate.plan

.. autoclass:: ncarnate.Plan
   :members:

.. autofunction:: ncarnate.execute

.. autofunction:: ncarnate.execute_batch

Prepared jobs and verified resume
-----------------------------------

Preparation binds source bytes and immutable encoding options to a separate
explicit output. Execution returns a handoff dictionary. Resume requires a
matching completion journal and matching output bytes; merely finding a file
at the output path is insufficient.

The output parent must already exist and be writable. Directory aliases are
resolved during preparation; linked source, output and journal leaves are
refused. Keep the small journal descriptor and its sibling
``<journal>.records`` directory together. Each completed output has a separate
atomic checkpoint, limited to 64 MiB per record with no aggregate byte limit.
Earlier 2.3.0 development journals migrate when resumed. A record without an output digest
cannot authorize reuse. Concurrent journal writers and power-loss durability
are not supported.

Resume loads the completion records into caller memory; usage grows with the
journal. If checkpoint writing fails, the completed output is retained but
cannot be reused without valid completion evidence. Preserve it for inspection
and use a fresh destination if retrying the conversion. A records directory
without its descriptor is refused: retain both for diagnosis and start with a
new journal path. Interrupted development-journal migration can retry only when
the remaining checkpoint files agree with the original journal.

.. code-block:: python

   from ncarnate import prepare_batch, execute_prepared_batch

   jobs = prepare_batch([("first.hdf", "first.nc"), ("second.hdf", "second.nc")])
   records = execute_prepared_batch(jobs, journal="completed.json", resume=True)

.. autoclass:: ncarnate.PreparedPlan
   :members:

.. autofunction:: ncarnate.prepare

.. autofunction:: ncarnate.prepare_batch

.. autofunction:: ncarnate.execute_prepared

.. autofunction:: ncarnate.execute_prepared_batch

Memory-limited conversion
-------------------------

``execute_bounded`` returns a dictionary containing ``result`` (the handoff),
``resources`` and, when necessary, cleanup ``warnings``. It runs one conversion
worker with an OS-enforced cap and a timeout. Windows limits committed memory;
Linux limits address space. Neither cap includes the caller. macOS is refused
by this API; ordinary conversion does not require this API.

.. code-block:: python

   from ncarnate import prepare, execute_bounded

   report = execute_bounded(prepare("input.hdf", "output.nc"),
                            memory_bytes=512 * 1024**2,
                            array_bytes=4 * 1024**2,
                            timeout_seconds=600)

Destinations must be new files and support same-filesystem hard links. The
worker does not integrate with resume journals. A failed or timed-out worker
publishes no final output; a cleanup failure after publication returns a
successful result with a warning. Generated coordinate fields remain whole
arrays, while larger interpolation temporaries are tiled. A cap that is too
small can refuse an otherwise supported input.
The 64 MiB accepted minimum is a validation floor, not a practical sizing
recommendation: scientific-library imports alone can exceed it. Start with
the default 512 MiB and size the cap for the input and runtime.

.. autofunction:: ncarnate.execute_bounded

Handoff records
---------------

The handoff describes verified output structure and identity. Source paths
are advisory; consumers must locate their checkpoint and verify its digest.
Validation limits nesting and record complexity. The separately packaged
numeric Zarr demonstration illustrates one consumer; it is not part of the
main package or a production storage service.

.. autoclass:: ncarnate.OperationResult
   :members: to_record, canonical_form

.. autofunction:: ncarnate.canonical_json

.. autofunction:: ncarnate.validate_handoff

.. autofunction:: ncarnate.check_materializable

.. autofunction:: ncarnate.materializability_error

.. autofunction:: ncarnate.schema_errors

.. autofunction:: ncarnate.load_handoff_schema

Format detection
----------------

.. autoclass:: ncarnate.FileFormat
   :members:
   :undoc-members:

.. autofunction:: ncarnate.detect_format

Exceptions
----------

All errors ncarnate raises deliberately derive from
:class:`~ncarnate.errors.NcarnateError`.

.. automodule:: ncarnate.errors
   :members:
   :show-inheritance:
