# Brand assets

The ncarnate mark: a phoenix rising from an orbited globe — legacy satellite and
geospatial data reincarnated as modern netCDF4. The Earth is drawn as a
latitude/longitude graticule (the gridded scientific data); an orbital band
sweeps around it (a nod to the satellite granules ncarnate reincarnates); and the
phoenix rises from it, an ember crest at its head — the spark of rebirth.

## Files

| File | Use |
|---|---|
| `ncarnate.svg` | Primary mark — full colour on the rounded navy tile (app-icon / avatar). |
| `ncarnate-mono.svg` | Single-ink navy silhouette, transparent — one-colour contexts on light backgrounds. |
| `ncarnate-white.svg` | Single-ink off-white silhouette, transparent — for dark backgrounds. |
| `ncarnate-wordmark.svg` / `-white.svg` | The word alone (Sora SemiBold, outlined). |
| `ncarnate-lockup.png` / `-dark.png` | **Shipped lockup** — source mark tile (raster) + wordmark, horizontal. `-dark` recolours the wordmark off-white for dark backgrounds (the mark tile reads on both themes). |
| `ncarnate-mark.png` | The source mark tile alone, with transparent rounded corners (favicon / social / reuse). |
| `ncarnate-lockup.svg` / `-dark.svg` | Fully-vector lockup — **work in progress**, not yet shipped (see below). |

The project README header swaps `ncarnate-lockup.png` ⇄ `-dark.png` by
`prefers-color-scheme` via `<picture>`. The wordmark inside is glyph **outlines**
(no font needed to render); it is rasterized into the shipped PNG lockup.

### Vector globe: deferred

The mark's thin globe **graticule** has not yet re-drawn cleanly as vectors — a
generated two-tilt globe (`compose_logo.py` + `globe.py`) matches at display scale
but its acceptance against the source is still open. Rather than block the release on
it, the **shipped lockup embeds the approved `ncarnate-source.png` mark as raster**
(`build_raster_lockup.py`), which is crisp at the README's display size. The vector
mark (`ncarnate.svg`, `ncarnate-lockup.svg`) remains in-repo as work in progress;
when it is accepted, swap the `<image>` in `build_raster_lockup.py` back to the
vector paths and the README layout is unchanged. See
`docs/plans/2026-07-11-logo-fidelity-regen.md`.

## Palette

| | Hex | Role |
|---|---|---|
| Deep navy | `#152A47` | Field / primary ink |
| Warm off-white | `#F2EDE1` | Structure (phoenix + graticule) |
| Ember | `#E8843C` | Crest flame — the spark of rebirth |

Wordmark type: **Sora SemiBold** (weight 600), SIL Open Font License 1.1.

## Regenerating

`ncarnate-source.png` is the approved raster — a flat, hard-edged emblem made by
an image model (the pictorial mark), used here as a **placement reference**: the
phoenix is traced from it, while the globe/orbit geometry (centre, radius, tilt)
is *measured* from it and then generated as vector primitives, not traced.

`compose_logo.py` does the hybrid composition (deps: `vtracer`, `pillow`, `scipy`,
`numpy`; `globe.py` is pure-stdlib): it splits the raster into field / phoenix /
ember, masks the globe + orbit out of the structure so only the organic phoenix is
traced, generates the graticule globe + orbit ring via `globe.py`, and layers them
(tile → globe → orbit-back → phoenix → orbit-front → ember). The globe geometry
(`CX, CY, R, TILT, MERIDIANS, PARALLELS`, orbit params) lives in constants at the
top of `compose_logo.py`; to re-fit it to a new raster, overlay `globe.py --out`
on the raster and nudge until the limb/graticule line up.

```bash
# deps: vtracer, fonttools, pillow, scipy, numpy  (+ cairosvg or @resvg/resvg-js to rasterise for the eyeball check)
python3 compose_logo.py ncarnate-source.png ncarnate.svg   # mark + -mono + -white

# fetch Sora and instance to weight 600:
#   curl -sL -o sora-var.ttf "https://raw.githubusercontent.com/google/fonts/main/ofl/sora/Sora%5Bwght%5D.ttf"
#   python3 -c "from fontTools import ttLib; from fontTools.varLib.instancer import instantiateVariableFont as I; f=ttLib.TTFont('sora-var.ttf'); I(f,{'wght':600},inplace=True); f.save('sora-600.ttf')"
python3 build_lockup.py --text ncarnate --font sora-600.ttf --mark ncarnate.svg \
    --field '#152A47' --structure '#F2EDE1' --out-prefix ncarnate   # wordmark + light/dark VECTOR lockups (WIP)

# shipped lockup: raster source mark + wordmark, light/dark PNG (deps: fonttools, pillow, numpy, scipy, cairosvg)
python3 build_raster_lockup.py --font sora-600.ttf                 # ncarnate-lockup{,-dark}.png + ncarnate-mark.png
```

Verify on both GitHub themes before committing — render each lockup on `#ffffff`
and `#0d1117` and confirm the wordmark is legible on each. The globe is generated,
so also zoom to a meridian×parallel crossing near the central meridian and confirm
uniform stroke width through the crossing, true concentric arcs, even meridian
spacing, and clean round caps.
