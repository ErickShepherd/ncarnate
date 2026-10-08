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
python -m pip install -e ".[test,release]" "ruff==0.15.*"
python -m pip install -r docs/requirements.txt
```

Compatible pip wheels can supply the native dependencies. If a wheel is not
available for your platform and Python version, use conda-forge or install the
native libraries and build tools described in the README. Ordinary conversion
and the hard memory-limit worker have different platform coverage: the worker
supports Windows and Linux and deliberately refuses macOS.

## Making a change

1. Fork the repository and create a topic branch off `main`.
2. Make your change with a focused commit history.
3. **Add or update tests.** The suite runs entirely offline against small
   committed fixtures trimmed from real granules; a new format or fix should
   come with a fixture-backed test that pins the behaviour.
4. Run the checks locally:
   ```console
   ruff check .
   python -m pytest -q
   python -m pytest -q tools/tests
   python tools/validate_release_metadata.py
   cffconvert --validate
   python -m sphinx -W --keep-going -b html docs docs/_build/html
   ```
5. Open a pull request describing the change and, for a conversion change, how
   it preserves the fidelity contract (stored values unchanged; output verified
   against the source before it replaces anything).

For full-size regression evidence, fetch and verify the catalogued originals
and independent reference with
`python tools/corpus.py DATA_DIRECTORY --fetch --include-references`.
Set `NCARNATE_GRANULE_DIR` to that directory and
run `python tools/test_full_corpus.py --junitxml full-granules.xml`. That check
fails on missing files or skipped/missing tests; ordinary pytest remains usable
without the external corpus. Keep downloaded inputs and reports outside the
tracked source tree. The manual full-granule GitHub workflow retains its reports.

The Zarr demonstration requires Python 3.11 or newer and is a separate package:
install `companions/zarr-demo` after the candidate ncarnate package, then run
`python -m pytest -q companions/zarr-demo/tests`. Its CI job builds and tests the
installed wheel outside both source packages.

To exercise release installations locally, build to a directory outside the
checkout with `python -m build --outdir PATH_TO_ARTIFACTS`, then run
`python tools/test_distribution.py PATH_TO_ARTIFACTS --kind wheel` and repeat
with `--kind sdist`. Each run creates a clean environment and tests the installed
package. PyPI and TestPyPI wait for these jobs and the full source CI. Publication
is a separate maintainer action; see `docs/releasing.rst`.

## Scope and design

ncarnate deliberately **fails loud** on constructs it cannot convert correctly
rather than guessing — a wrong coordinate is worse than a refused conversion.
New support should extend that contract, not weaken it: prefer a clear,
tested error over a silent approximation. When in doubt, open an issue to
discuss the approach before a large change.

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
