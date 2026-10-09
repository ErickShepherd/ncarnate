Releasing ncarnate
==================

This guide describes the release process for ncarnate. Updating source metadata
does not publish to GitHub, PyPI, conda-forge, Read the Docs or Zenodo.

Before publication
------------------

Run the complete supported Python/platform CI matrix, including ordinary
conversion on macOS. Hard worker memory limits support Windows and Linux only.
The conda-forge CI lanes cover both Linux and Windows, including HDF4 inside
the isolated worker. A passing pip-wheel lane does not qualify conda's native
library loading; run both installation channels before release.
The separate companion CI job builds and tests the Zarr demonstration. Build
the documentation with warnings treated as errors, validate ``CITATION.cff``,
and run ``tools/validate_release_metadata.py``. The metadata check requires
PyYAML; citation validation uses ``cffconvert --validate``.

The version in ``ncarnate/constants.py`` drives Python packaging and Sphinx.
Keep the citation version, Zenodo version, changelog and conda recipe aligned.
The concept DOI in the README and citation identifies all versions; do not
reuse the previous version-specific DOI for a new release. The date in
``CITATION.cff`` must be the actual publication date, so it remains absent
during preparation. Add it, and date the changelog entry, when scheduling the
actual release. Those edits require rebuilding the source archive.

Document compatibility changes explicitly: encoding options now enforce the
immutable-plan contract, unsupported audit modes are refused, and invalid
UTF-8 result text cannot become a portable handoff. Historical handoff fixtures
retain their original producer versions to test backward readability.

The source build hook writes a zero timestamp in the gzip wrapper for canonical
archive inspection. It preserves the tar contents and their reproducible member
timestamps. This runs as part of ``python -m build`` before artifact tests and
hashing; do not modify published or already-tested archives afterward.

Build the source archive and wheel, run ``twine check``, inspect distribution
contents, and install both artifacts in clean environments. Private recovery
evidence and the companion must stay outside the main distributions. The
conda recipe is also excluded: it hashes the source archive and cannot be
embedded in the archive whose digest it records.

For a checkout enrolled in Project Publication, run its privacy, secret and
artifact admission checks against the complete candidate before committing or
pushing. Enrollment binds the local checkout, branch and Git configuration;
it does not approve flagged content or authorize publication. Keep private
scan reports and enrollment receipts outside the tracked source. Resolve
refusals through the configured review process and rerun checks after changes.
The installed commit broker must repeat its checks on the exact commit request.

Publication and downstream checks
------------------------------------

The publish workflow calls the full CI workflow from the same source revision.
Both PyPI and TestPyPI uploads wait for every CI job: source tests, pip and conda
platform matrices, docs, metadata, installed distributions and the Zarr companion.
The package job builds once. Clean environments install both its wheel and
source archive on Linux, Windows and macOS, then run the runtime tests outside
the checkout. The source-archive lane uses its packaged tests and fixtures;
the wheel lane uses the checkout's tests. Source CI separately checks packaging
contents. Publication uses
those same retained package files, without rebuilding them after testing.

The publish workflow rejects a GitHub release whose tag differs from the
package version and refuses an undated PyPI release. Publishing a full GitHub
release triggers the PyPI workflow; a manual workflow run can target TestPyPI
first. Manual production runs must select the matching version tag, not a
branch. Duplicate TestPyPI uploads fail instead of silently skipping existing
files; use a new candidate version for a changed test upload. These are
separate maintainer actions.

The first 2.3.0 upload encountered an outdated publisher that could not read
core metadata 2.5. The publisher is now pinned to a compatible version. A
one-time ``recover_230`` option, selected with target ``pypi`` from ``main``,
retries only the preserved distributions from that original release run.
It verifies the pinned run, all 19 successful validation jobs, the unchanged
release tag, and both package checksums before reaching the normal protected
PyPI environment. It does not rebuild packages or move the published tag.
The recovery is specific to 2.3.0 and cannot publish another release. Once
those files are uploaded, duplicate uploads still fail.

For full-size evidence, ``Full-granule validation`` runs automatically for
pull requests into ``main`` and pushes to ``main`` that change package code,
tests, the corpus tools, package metadata, or the full-granule workflow. It
can also be run manually in GitHub Actions on the candidate revision, including
releases containing only documentation changes. It restores a cache keyed by
the corpus manifest and downloads any missing catalogued original
granules and independent reference, verifies every pinned digest, and runs the
full-size tests. Missing data, unavailable mirrors, mismatched digests, test
failures, skipped tests and missing expected test cases all fail the job.
Verification and test reports are
retained even on failure. This network-dependent job is separate from the
normal publication gate; review its result before release. For an already
acquired local corpus, set ``NCARNATE_GRANULE_DIR`` and run
``python tools/test_full_corpus.py --junitxml PATH_TO_REPORT``.
Temporary download failures receive up to three attempts per URL with bounded
backoff; detailed errors appear directly in the job log. Cache hits never skip
checksum verification. The cache is saved only after all inputs verify, before
conversion tests run. See :doc:`ci-dependencies` for source availability and
the proposed durable mirror.

After PyPI publication, verify the downloaded archive matches the retained
release artifact and update the recipe digest if necessary. The local recipe
may carry a digest from the prepared archive; it is not proof that PyPI serves
those bytes. Submit the version and digest update to the existing
``conda-forge/ncarnate-feedstock`` and run its build/lint matrix. Do not reopen
an initial staged-recipes submission for the existing package.

Downstream automation and manual checks
---------------------------------------

* **PyPI:** a full GitHub release starts the workflow described above. The
  upload still needs passing CI and approval of the protected ``pypi``
  environment. A branch push or draft pull request does not publish a package.
  Verify the uploaded version, README rendering and retained artifact digests.
* **conda-forge:** the version-update bot can propose a feedstock update after
  PyPI publication. Review its version, digest and dependency changes, and wait
  for the feedstock's own checks before merging. If no update appears, use the
  documented bot update request or submit the recipe update. A main-repository
  merge does not itself update the conda-forge channel. See the
  `conda-forge maintainer guide <https://conda-forge.org/docs/maintainer/updating_pkgs/>`_.
* **Read the Docs:** ``latest`` tracks the default branch; ``stable`` tracks
  the highest stable version tag. Newly discovered named versions are inactive
  by default unless an automation rule activates them. After tagging, check
  the Versions dashboard, activate ``v2.3.1`` if necessary, and verify both its
  build and ``stable``. Trigger a build to resync tags if needed. Activation
  triggers a build; do not assume that discovering a tag published its docs.
  See `Read the Docs version management <https://docs.readthedocs.com/platform/stable/versions.html>`_.
* **Zenodo:** with the GitHub repository integration enabled, publishing a
  GitHub release triggers archiving. Check the integration result and verify
  that the record belongs to the existing concept DOI, with the correct
  version and actual release date. Do not create a separate deposit for a
  routine version update or invent a version DOI before Zenodo assigns it.
  See `Zenodo's GitHub release guide <https://help.zenodo.org/docs/github/archive-software/github-upload/>`_.

For 2.3.1, keep the citation and changelog undated while the PR is a draft.
Before tagging, set the actual publication date in both, rebuild and retest
the final artifacts, and refresh the local conda digest. The new
``convert_file`` API requires a separate output and, by default, hard-link
support on the output filesystem. Confirm its platform tests pass; do not
silently fall back to overwriting on filesystems without that support.
