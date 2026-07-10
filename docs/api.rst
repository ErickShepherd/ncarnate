API reference
=============

The public API is small: a single :func:`~ncarnate.recompress` entry point, a
file-format detector, and the exception hierarchy. Everything below is imported
directly from the top-level ``ncarnate`` package.

Conversion and recompression
-----------------------------

.. autofunction:: ncarnate.recompress

Format detection
----------------

.. autoclass:: ncarnate.FileFormat
   :members:
   :undoc-members:

.. autofunction:: ncarnate.detect_format

Exceptions
----------

All errors ncarnate raises deliberately derive from :class:`~ncarnate.NcarnateError`.

.. automodule:: ncarnate.errors
   :members:
   :show-inheritance:
