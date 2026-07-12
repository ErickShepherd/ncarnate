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
| `ncarnate-lockup.svg` / `-dark.svg` | Mark + wordmark, horizontal. `-dark` recolours the wordmark off-white for dark backgrounds (the colour mark is kept — it reads on both themes). |

All SVGs are true vector — the wordmark is glyph **outlines**, so no font is
needed to render them. The project README header swaps `ncarnate-lockup.svg` ⇄
`-dark.svg` by `prefers-color-scheme` via `<picture>`.

## Palette

| | Hex | Role |
|---|---|---|
| Deep navy | `#152A47` | Field / primary ink |
| Warm off-white | `#F2EDE1` | Structure (phoenix + graticule) |
| Ember | `#E8843C` | Crest flame — the spark of rebirth |

Wordmark type: **Sora SemiBold** (weight 600), SIL Open Font License 1.1.

## Regenerating

`ncarnate-source.png` is the approved raster — a flat, hard-edged emblem made by
an image model (the pictorial mark), which local tooling then turns into vector.
The mark is traced into a clean layered SVG; the wordmark and lockups are typeset
from the font as outlines.

`--supersample 2` upscales the raster before tracing so the spline tracer does
not *hook* where the thin graticule and orbit lines cross (a 1× trace overshoots
those T-junctions); `--precision 2` offsets the resulting point-count growth so
the SVGs stay small.

```bash
# deps: vtracer, fonttools, pillow, scipy, numpy  (+ cairosvg or @resvg/resvg-js to rasterise for the eyeball check)
python3 trace_logo.py ncarnate-source.png ncarnate.svg \
    --field '#152A47' --structure '#F2EDE1' --accent '#E8843C' \
    --supersample 2 --precision 2   # mark + -mono + -white

# fetch Sora and instance to weight 600:
#   curl -sL -o sora-var.ttf "https://raw.githubusercontent.com/google/fonts/main/ofl/sora/Sora%5Bwght%5D.ttf"
#   python3 -c "from fontTools import ttLib; from fontTools.varLib.instancer import instantiateVariableFont as I; f=ttLib.TTFont('sora-var.ttf'); I(f,{'wght':600},inplace=True); f.save('sora-600.ttf')"
python3 build_lockup.py --text ncarnate --font sora-600.ttf --mark ncarnate.svg \
    --field '#152A47' --structure '#F2EDE1' --out-prefix ncarnate   # wordmark + light/dark lockups
```

Verify on both GitHub themes before committing — render each lockup on `#ffffff`
and `#0d1117` and confirm the wordmark is legible on each. The mark's thin
graticule/orbit lines are the fidelity-critical part; trace from the
highest-resolution raster available.
