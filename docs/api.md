# API reference

The public API is small: a single {py:func}`~ncarnate.recompress` entry point, a
file-format detector, and the exception hierarchy. Everything below is imported
directly from the top-level `ncarnate` package.

## Conversion and recompression

```{eval-rst}
.. autofunction:: ncarnate.recompress
```

## Format detection

```{eval-rst}
.. autoclass:: ncarnate.FileFormat
   :members:
   :undoc-members:

.. autofunction:: ncarnate.detect_format
```

## Exceptions

All errors ncarnate raises deliberately derive from
{py:class}`~ncarnate.errors.NcarnateError`.

```{eval-rst}
.. automodule:: ncarnate.errors
   :members:
   :show-inheritance:
```
