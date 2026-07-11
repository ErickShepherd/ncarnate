API reference
=============

The public API is small: a :func:`~ncarnate.recompress` conversion/recompression
entry point, a read-only :func:`~ncarnate.audit_path` archive assessor, a
file-format detector, and the exception hierarchy. Everything below is imported
directly from the top-level ``ncarnate`` package.

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
