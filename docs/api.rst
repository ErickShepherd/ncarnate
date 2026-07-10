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
