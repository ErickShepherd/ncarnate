#!/usr/bin/env python3
"""Compose the ncarnate mark as a HYBRID: a *generated* graticule globe + orbit ring (true vector
primitives, from globe.py) with the *traced* phoenix (organic, from the raster). This replaces the
all-traced mark whose globe blobbed at junctions and wobbled off true arcs (see
docs/plans/2026-07-11-logo-fidelity-regen.md).

Principle: trace the organic, generate the structured. The phoenix is traced (correct for an organic
shape); the globe/orbit are computed strokes (uniform width, exact overlaps, resolution-independent).

Pipeline:
  1. Load ncarnate-source.png; split into field / structure (off-white) / accent (ember) masks.
  2. Isolate the phoenix from the structure mask: drop the globe graticule (thin lines removed by a
     morphological opening) and the orbit ring + limb (structured geometry below the bird), keeping a
     central TAIL ZONE so the tail stays whole. What remains is the organic bird only.
  3. Binary-trace the phoenix (+ the ember crest) into clean SVG paths.
  4. Generate the globe + orbit as vector primitives via globe.py at the geometry measured from the
     raster (CX,CY,R,TILT,... below).
  5. Layer: tile -> globe graticule (back) -> orbit back arc -> phoenix -> orbit front arc -> ember.
     The orbit weaves (back behind the globe, front in front of the tail); the phoenix sits in front
     of the globe, matching the source.

Outputs (like trace_logo.py): OUT.svg (colour on tile), OUT-mono.svg (field-ink silhouette,
transparent), OUT-white.svg (structure-ink silhouette, transparent).

Deps: vtracer, pillow, scipy, numpy (globe.py is pure-stdlib).  Usage:
  compose_logo.py ncarnate-source.png ncarnate.svg
"""
import argparse
import math
import os
import re
import tempfile

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_dilation, binary_opening, label
import vtracer

import globe

# ---- palette ----------------------------------------------------------------------------------
FIELD = "#152A47"
STRUCTURE = "#F2EDE1"
ACCENT = "#E8843C"

# ---- globe geometry (measured against ncarnate-source.png, 1254x1254) -------------------------
CX, CY, R = 655.0, 804.0, 207.0
TILT = 10.0
MERIDIANS, PARALLELS = 7, 5
GRAT_STROKE, LIMB_STROKE = 8.0, 9.0
ORBIT_RX, ORBIT_RY, ORBIT_ROT = 278.0, 78.0, -15.0
ORBIT_DX, ORBIT_DY, ORBIT_STROKE = 4.0, 0.0, 10.0

# ---- phoenix isolation knobs ------------------------------------------------------------------
OPEN_RADIUS = 6            # graticule lines (~<=12px) are removed; the solid tail/body survive
TAIL_TOP = 555            # above this y the whole mark is the bird; below it, only the tail
DISK_MARGIN = 1.02        # lower structure beyond R*margin (orbit swooshes, limb flares) is dropped
TAIL_WIDTH = 150          # width of the curved band tracking the tail down over the globe
# tail centreline (measured from the raster): the tail sweeps out to the right of the globe, then
# curves back to the bottom curl. Kept generous so the whole ribbon survives; orbit fragments that
# fall inside the band where the ring crosses are masked by the orbit FRONT arc drawn on top.
TAIL_PTS = [(650, 545), (730, 610), (808, 690), (828, 780), (780, 862),
            (700, 928), (648, 985), (626, 1045), (632, 1095)]

PARAMS = dict(colormode="binary", mode="spline", filter_speckle=6, corner_threshold=20,
              length_threshold=4.0, splice_threshold=45, path_precision=6)


def hexrgb(s):
    s = s.lstrip("#")
    return np.array([int(s[i:i + 2], 16) for i in (0, 2, 4)], float)


def trace(mask, tag):
    img = np.where(mask, 0, 255).astype(np.uint8)
    with tempfile.TemporaryDirectory() as t:
        p, s = os.path.join(t, f"{tag}.png"), os.path.join(t, f"{tag}.svg")
        Image.fromarray(img, "L").convert("RGB").save(p)
        vtracer.convert_image_to_svg_py(p, s, **PARAMS)
        return re.findall(r"<path[^>]*/>", open(s).read())


def recolor(paths, color):
    return "".join(re.sub(r'fill="#[0-9A-Fa-f]{6}"', f'fill="{color}"', p) for p in paths)


def isolate_phoenix(structure, w, h):
    """Return the organic phoenix mask: structure minus the globe graticule, orbit ring and limb."""
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.hypot(xx - CX, yy - CY)
    se = np.hypot(*np.mgrid[-OPEN_RADIUS:OPEN_RADIUS + 1, -OPEN_RADIUS:OPEN_RADIUS + 1]) <= OPEN_RADIUS
    solid = binary_opening(structure, structure=se)     # keeps thick blobs, kills thin graticule
    graticule = structure & ~solid & (dist < R * 1.1)   # thin lines, only within the globe disk

    # orbit band (the ellipse the procedural orbit will occupy), for removing the traced orbit
    band = Image.new("L", (w, h), 0)
    bd = ImageDraw.Draw(band)
    rot = math.radians(ORBIT_ROT)
    for orx, ory, wdt in ((ORBIT_RX, ORBIT_RY, 30), (ORBIT_RX + 10, ORBIT_RY + 12, 22)):
        pts = [(CX + ORBIT_DX + (orx * math.cos(k * math.tau / 720)) * math.cos(rot)
                - (ory * math.sin(k * math.tau / 720)) * math.sin(rot),
                CY + ORBIT_DY + (orx * math.cos(k * math.tau / 720)) * math.sin(rot)
                + (ory * math.sin(k * math.tau / 720)) * math.cos(rot)) for k in range(721)]
        bd.line(pts, fill=255, width=wdt, joint="curve")
    orbit_band = np.asarray(band) > 0

    tband = Image.new("L", (w, h), 0)
    ImageDraw.Draw(tband).line(TAIL_PTS, fill=255, width=TAIL_WIDTH, joint="curve")
    tail_zone = (np.asarray(tband) > 0) & (yy > TAIL_TOP)   # curved band hugging the tail

    remove = graticule.copy()
    remove |= orbit_band & ~tail_zone                                   # orbit over the globe face
    # Inside (and just beyond) the globe disk, the ONLY organic thing is the tail; the globe graticule
    # and orbit are redrawn procedurally. So drop every traced pixel there except the tail column.
    remove |= structure & (yy > TAIL_TOP) & (dist < R * 1.06) & ~tail_zone
    remove |= structure & (yy > TAIL_TOP + 5) & (dist > R * DISK_MARGIN) & ~tail_zone  # off-disk flares
    phoenix = structure & ~remove

    # drop small isolated specks (severed graticule/orbit crumbs the band happened to keep)
    lab, _ = label(phoenix)
    sizes = np.bincount(lab.ravel())
    keep = np.where(sizes >= 350)[0]
    phoenix = np.isin(lab, keep[keep != 0])
    return binary_dilation(phoenix, iterations=1)      # +1px overlaps the tile edge, kills the seam


def build(src, out):
    rgb = np.asarray(Image.open(src).convert("RGB"), float)
    h, w = rgb.shape[:2]
    luma = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    chroma = rgb.max(-1) - rgb.min(-1)
    anchors = [hexrgb(FIELD), hexrgb(STRUCTURE), hexrgb(ACCENT)]
    nearest = np.argmin(np.stack([((rgb - a) ** 2).sum(-1) for a in anchors], -1), -1)
    accent = (nearest == 2) & (chroma > 40)
    offish = (~accent) & (luma >= 140)
    lab, _ = label(offish)
    border = (set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])) - {0}
    outer = np.isin(lab, list(border))
    structure = offish & ~outer
    tile_mask = ~outer                       # the navy rounded tile (everything but the outer bg)

    phoenix = isolate_phoenix(structure, w, h)
    accent = binary_dilation(accent, iterations=1)
    phoenix_paths = trace(phoenix, "phoenix")
    accent_paths = trace(accent, "accent")

    # generated globe + orbit primitives
    orbit_back, orbit_front = globe.orbit_arcs(CX, CY, ORBIT_RX, ORBIT_RY, ORBIT_ROT, ORBIT_DX, ORBIT_DY)
    graticule = graticule_group(STRUCTURE)
    orbit_b = orbit_stroke(orbit_back, STRUCTURE)
    orbit_f = orbit_stroke(orbit_front, STRUCTURE)

    # tile: a rounded-rect primitive matching the source navy tile (no trace seam)
    tile = tile_rect(tile_mask, FIELD)

    def doc(body):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
                f'viewBox="0 0 {w} {h}">{body}</svg>\n')

    # Layer bottom->top: tile, orbit back arc (behind the globe), globe graticule, phoenix, orbit
    # FRONT arc (in front of the globe AND the tail — it masks any traced orbit fragment where the
    # ring crosses the tail, and reads as the near side of the ring), ember crest on top. The ring
    # weaves: behind the globe up top, in front of the globe + tail below.
    colour = (tile + orbit_b + graticule
              + f'<g>{recolor(phoenix_paths, STRUCTURE)}</g>' + orbit_f
              + f'<g>{recolor(accent_paths, ACCENT)}</g>')
    open(out, "w").write(doc(colour))

    # silhouettes: phoenix + globe + orbit + ember, flattened to one ink, transparent (no tile)
    def silhouette(ink):
        return doc(orbit_stroke(orbit_back, ink) + graticule_group(ink)
                   + f'<g>{recolor(phoenix_paths, ink)}</g>' + orbit_stroke(orbit_front, ink)
                   + f'<g>{recolor(accent_paths, ink)}</g>')

    base = os.path.splitext(out)[0]
    open(f"{base}-mono.svg", "w").write(silhouette(FIELD))
    open(f"{base}-white.svg", "w").write(silhouette(STRUCTURE))
    print(f"wrote {out} (+ -mono, -white); phoenix/accent paths = "
          f"{len(phoenix_paths)}/{len(accent_paths)}; globe={MERIDIANS}mer/{PARALLELS}par + orbit")


def graticule_group(ink):
    import math
    beta = math.radians(TILT)
    lons = [math.radians(x) for x in globe._interior(-90, 90, MERIDIANS)]
    lats = [math.radians(x) for x in globe._interior(-90, 90, PARALLELS)]
    lines = "".join(f'<path d="{globe.meridian_path(CX, CY, R, beta, la)}"/>' for la in lons)
    lines += "".join(f'<path d="{p}"/>' for p in
                     (globe.parallel_path(CX, CY, R, beta, th) for th in lats) if p)
    return (f'<circle cx="{CX}" cy="{CY}" r="{R}" fill="none" stroke="{ink}" '
            f'stroke-width="{LIMB_STROKE}"/>'
            f'<g fill="none" stroke="{ink}" stroke-width="{GRAT_STROKE}" stroke-linecap="round" '
            f'stroke-linejoin="round">{lines}</g>')


def orbit_stroke(d, ink):
    return (f'<path d="{d}" fill="none" stroke="{ink}" stroke-width="{ORBIT_STROKE}" '
            f'stroke-linecap="round"/>')


def tile_rect(mark_mask, color):
    ys, xs = np.where(mark_mask)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    full = np.where(mark_mask.sum(1) >= 0.98 * (x1 - x0))[0]
    r = int(full.min() - y0) if len(full) else int(0.12 * (x1 - x0))
    return (f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" '
            f'rx="{r}" ry="{r}" fill="{color}"/>')


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    a = ap.parse_args()
    build(a.src, a.out)
