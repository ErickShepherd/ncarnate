#!/usr/bin/env python3
"""Compose horizontal RASTER lockups from the approved source mark tile + the typeset wordmark.

This is the interim, ship-now lockup: it embeds the trusted ``ncarnate-source.png`` mark AS RASTER
(the emblem whose thin globe graticule has resisted a clean vector re-stroke) rather than the
generated vector mark. Geometry (mark height, wordmark cap-fraction, gap, padding, vertical centering)
mirrors ``build_lockup.py`` exactly, so swapping back to a vector mark later leaves the README layout
untouched. The wordmark stays a true-vector glyph outline, rasterized here only for a single flat PNG.

Steps:
  1. Give the mark tile clean transparent corners (silhouette-derived alpha) so it reads on light AND dark.
  2. Typeset the wordmark from the font (same fontTools path as build_lockup.py).
  3. Compose a lockup SVG with the mark as an embedded base64 <image>, wordmark as vector paths.
  4. Rasterize to PNG at 3x display width via cairosvg — one per word colour (light/dark themes).

Deps: fonttools, pillow, numpy, scipy, cairosvg. Usage:
  build_raster_lockup.py  # uses the committed defaults below
"""
import argparse
import base64
import io

import cairosvg
import numpy as np
from PIL import Image
from scipy.ndimage import label, binary_erosion, gaussian_filter

from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.boundsPen import BoundsPen

# ---- inputs / palette (match compose_logo.py + build_lockup.py) -------------------------------
SRC = "ncarnate-source.png"
FONT = "sora-600.ttf"  # produced per brand/README "Regenerating" (override with --font)
TEXT = "ncarnate"
FIELD = "#152A47"      # navy word for light backgrounds
STRUCTURE = "#F2EDE1"  # cream word for dark backgrounds
TRACK = 12             # letter-spacing, font units (build_lockup default)
WORD_CAP_FRAC = 0.50   # word cap-height as a fraction of mark height (build_lockup default)
LK = 300               # mark height, design units (build_lockup default)
DISPLAY_W = 460        # README render width, CSS px
SCALE = 3              # rasterize at 3x for retina crispness


def transparent_mark(src):
    """Return the source tile as RGBA with the white page beyond the rounded tile made transparent.

    The tile is derived from its true silhouette, not a guessed rounded-rect: the white page shows only
    in the four rounded corners (the navy tile fills the frame edge-to-edge on the flat sides), so
    ``outer`` is every near-white blob touching the frame edge (all four corners at once). ``tile`` is
    the largest remaining component; eroding it a few px cuts just inside the navy so the antialiased
    navy/white rim (which read as white notches on a dark background) is dropped entirely."""
    rgb = np.asarray(Image.open(src).convert("RGB"), float)
    white = rgb.min(-1) >= 190                 # near-white in every channel (page + rim + cream ink); only
                                               # border-touching blobs become `outer`, so interior ink is safe
    lab, _ = label(white)
    border = (set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])) - {0}
    outer = np.isin(lab, list(border))         # all white blobs touching the frame edge = the 4 corners
    tile = ~outer
    tlab, _ = label(tile)                       # keep the largest tile blob, drop stray edge specks
    sizes = np.bincount(tlab.ravel())
    sizes[0] = 0
    tile = tlab == sizes.argmax()
    tile = binary_erosion(tile, iterations=3)   # cut inside the navy: no antialiased white rim survives

    ys, xs = np.where(tile)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    alpha = gaussian_filter(tile * 255.0, 0.8)  # 1px feather for a clean edge (navy -> transparent)
    out = Image.open(src).convert("RGBA")
    out.putalpha(Image.fromarray(alpha.astype(np.uint8), "L"))
    return out.crop((x0, y0, x1 + 1, y1 + 1))


def typeset(font, text, track):
    """Vector glyph outlines for the wordmark (copied from build_lockup.py). Returns (word_svg_group,
    ink-bbox width WW, height WH, cap-height CAP, ymax)."""
    f = TTFont(font)
    cmap = f.getBestCmap()
    gs = f.getGlyphSet()
    hmtx = f["hmtx"]
    CAP = getattr(f.get("OS/2"), "sCapHeight", 0) or int(0.7 * f["head"].unitsPerEm)
    paths, x = [], 0
    xmin = ymin = 1e9
    xmax = ymax = -1e9
    for ch in text:
        gname = cmap.get(ord(ch))
        if gname is None:
            raise SystemExit(f"font has no glyph for {ch!r}")
        pen = SVGPathPen(gs)
        gs[gname].draw(pen)
        d = pen.getCommands()
        if d:
            paths.append(f'<path transform="translate({x},0)" d="{d}"/>')
            bp = BoundsPen(gs)
            gs[gname].draw(bp)
            if bp.bounds:
                gx0, gy0, gx1, gy1 = bp.bounds
                xmin, ymin = min(xmin, x + gx0), min(ymin, gy0)
                xmax, ymax = max(xmax, x + gx1), max(ymax, gy1)
        x += hmtx[gname][0] + track
    WW, WH = xmax - xmin, ymax - ymin
    word = f'<g transform="translate({-xmin},{ymax}) scale(1,-1)">{"".join(paths)}</g>'
    return word, WW, WH, CAP, ymax


def main(src=SRC, font=FONT):
    mark = transparent_mark(src)
    MH = mark.size[1]
    buf = io.BytesIO()
    mark.save(buf, "PNG")
    href = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    word, WW, WH, CAP, ymax = typeset(font, TEXT, TRACK)

    # geometry — identical to build_lockup.py
    wscale = (LK * WORD_CAP_FRAC) / CAP
    gap, pad = LK * 0.15, LK * 0.12
    LKW = LK + gap + WW * wscale + pad
    mscale = LK / MH
    mw = mark.size[0] * mscale
    wy = LK / 2 - (CAP / 2 + (ymax - CAP)) * wscale

    def lockup_svg(word_color):
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{LKW:.0f}" height="{LK}" viewBox="0 0 {LKW:.0f} {LK}">'
            f'<image href="{href}" x="0" y="0" width="{mw:.2f}" height="{LK}"/>'
            f'<g transform="translate({LK + gap:.1f},{wy:.1f}) scale({wscale})">'
            f'<g fill="{word_color}">{word}</g></g>'
            f'</svg>'
        )

    out_w = DISPLAY_W * SCALE
    for color, name in ((FIELD, "ncarnate-lockup.png"), (STRUCTURE, "ncarnate-lockup-dark.png")):
        cairosvg.svg2png(bytestring=lockup_svg(color).encode(), write_to=name, output_width=out_w)
        print(f"wrote {name}  ({out_w}px wide, lockup {LKW:.0f}x{LK})")

    # also emit the transparent mark tile on its own (favicon/social/reuse), sized for reuse not print
    mark512 = mark.resize((512, 512), Image.LANCZOS)
    mark512.save("ncarnate-mark.png")
    print("wrote ncarnate-mark.png  (512x512, transparent corners)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Compose raster light/dark lockups from the source mark.")
    ap.add_argument("--src", default=SRC, help=f"source mark tile PNG (default: {SRC})")
    ap.add_argument("--font", default=FONT, help=f"wordmark font, Sora weight 600 (default: {FONT})")
    a = ap.parse_args()
    main(a.src, a.font)
