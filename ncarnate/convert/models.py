#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The convert data models: stdlib dataclasses describing how a
manifest-driven convert run behaves (``ConvertOptions``) and what it did
(``ConvertResult`` — the per-file ``ConvertRecord`` outcomes).

``ConvertOptions`` mirrors the ``recompress`` encoding flags (design
§Per-status conversion parameters) so a manifest run and the legacy
positional form encode identically, and defaults to the safety-first
posture the spec pins: only the ``ready`` status is converted (KD8) and the
archive is never mutated unless ``in_place`` is set (KD3).

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
from dataclasses import dataclass, field


@dataclass
class ConvertOptions:

    '''

    How a manifest-driven convert run behaves.

    ``out_dir`` is the mirrored output root (the non-destructive default,
    KD3); ``in_place`` opts into recompressing sources where they sit.
    ``statuses`` selects which audited statuses to act on and defaults to
    ``{"ready"}`` only (KD8) — the operator widens it after reading the
    report. ``allow_unverified`` relaxes the mandatory sha256 gate (KD2)
    for a ``null``-hash record; ``skip_existing`` makes an ``out_dir`` run
    resumable; ``root`` is the operator-supplied containment base a source
    path resolves under (§Risks); ``allow_manifest_root`` opts into trusting
    the manifest's own recorded ``root`` as that base when ``root`` is not
    given — untrusted by default, since the manifest is untrusted input. The
    encoding flags (``zlib``/``shuffle``/``complevel``/``geolocation``) share
    ``recompress``'s defaults.

    '''

    out_dir             : str | None = None
    statuses            : set[str] = field(default_factory=lambda: {"ready"})
    allow_unverified    : bool = False
    in_place            : bool = False
    skip_existing       : bool = False
    root                : str | None = None
    allow_manifest_root : bool = False
    zlib                : bool = True
    shuffle             : bool = True
    complevel           : int = 7
    geolocation         : bool = True


@dataclass
class ConvertRecord:

    '''

    One file's outcome. ``path`` is the manifest-relative source path;
    ``reason`` explains a skip or failure (a converted file needs none, so
    it defaults to ``None``).

    '''

    path   : str
    reason : str | None = None


@dataclass
class ConvertResult:

    '''

    The end-of-run tally: the files ``converted``, those ``skipped`` (a
    status not selected, a blocker, an already-present output), and those
    ``failed`` (a sha256 mismatch, a containment rejection, a conversion
    error). The exit code is non-zero iff ``failed`` is non-empty.

    '''

    converted : list[ConvertRecord] = field(default_factory=list)
    skipped   : list[ConvertRecord] = field(default_factory=list)
    failed    : list[ConvertRecord] = field(default_factory=list)

    @property
    def exit_code(self) -> int:

        '''

        The process exit code: non-zero **iff any selected record failed**
        (§The per-record loop). A skip — a blocker or a non-selected status —
        is not a failure and never sets it, mirroring the audit's ``main``
        returning an ``int``.

        '''

        return 1 if self.failed else 0
