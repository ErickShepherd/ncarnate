# Contributing to ncarnate

Thanks for your interest in ncarnate. Contributions — bug reports, fixes,
documentation, and new format support — are welcome.

## Reporting issues

Please open an issue at
<https://github.com/ErickShepherd/ncarnate/issues>. A good report includes:

- what you ran (the exact command or code) and what happened,
- the input file's format (netCDF3/4, HDF5, HDF4/HDF-EOS2) and, if possible, a
  small sample or its structure (`ncdump -h`, or the HDF-EOS `StructMetadata`),
- your OS, Python version, and the installed versions of `ncarnate`, `netCDF4`,
  and `pyhdf`.

Because ncarnate makes a **fidelity guarantee** (see `docs/fidelity-notes.md`),
any case where a converted or recompressed file does *not* re-read identically
to its source is treated as a correctness bug — please report it.

## Seeking support

For usage questions, open a
[Discussion](https://github.com/ErickShepherd/ncarnate/discussions) if enabled,
or an issue labelled `question`. The README covers installation (including the
`pyhdf`-on-Windows caveat), CLI and library usage, and the supported inputs.

## Development setup

```console
git clone https://github.com/ErickShepherd/ncarnate
cd ncarnate
pip install -e ".[test]"
```

On Linux (x86_64) and macOS (arm64) every dependency — including `pyhdf` —
installs as a binary wheel. On other platforms the HDF4 path may need a system
HDF4 library first (see the README).

## Making a change

1. Fork the repository and create a topic branch off `main`.
2. Make your change with a focused commit history.
3. **Add or update tests.** The suite runs entirely offline against small
   committed fixtures trimmed from real granules; a new format or fix should
   come with a fixture-backed test that pins the behaviour.
4. Run the checks locally:
   ```console
   ruff check .
   pytest
   ```
5. Open a pull request describing the change and, for a conversion change, how
   it preserves the fidelity contract (stored values unchanged; output verified
   against the source before it replaces anything).

## Scope and design

ncarnate deliberately **fails loud** on constructs it cannot convert correctly
rather than guessing — a wrong coordinate is worse than a refused conversion.
New support should extend that contract, not weaken it: prefer a clear,
tested error over a silent approximation. When in doubt, open an issue to
discuss the approach before a large change.

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
