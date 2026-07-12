#!/usr/bin/env python3
"""Generate a lat/lon graticule globe (and optional orbit ring) as TRUE vector primitives — the
structured counterpart to trace_logo.py.

Principle: *trace the organic, generate the structured.* A globe/graticule is regular parametric
geometry; tracing it from a raster reproduces the drawing's flaws (junctions blob, "arcs" wobble off
any true circle, stroke width swells, tapers fray). Instead we compute it: an orthographic sphere of
radius r, tilted by `beta` about the horizontal axis, with meridians and parallels emitted as STROKED
elliptical arcs. Every crossing is then an exact, uniform-width overlap with clean round caps — no
blobs, no wobble, resolution-independent at any zoom.

Geometry (orthographic, screen y-down). A lat/lon point (lat th, lon la) on a sphere of radius r,
tilted by beta about the x-axis (positive tips the north pole toward the viewer):
    x0 = r cos(th) sin(la);  y0 = r sin(th);  z0 = r cos(th) cos(la)
    X  = x0;  Yr = y0 cos(beta) - z0 sin(beta);  Z = y0 sin(beta) + z0 cos(beta)
    screen = (cx + X, cy - Yr);  visible iff Z > 0 (front hemisphere)
Every parallel/meridian is a circle on the sphere, so its projection is an ellipse P(t)=C+a·cos t+b·sin t
(a, b are 2-vectors). We emit only the visible (Z>0) sub-arc, as cubic Béziers that are exact for
elliptical arcs (control handle length = 4/3·tan(dt/4)·P'(t) over sub-arcs <=90 degrees). The sphere
silhouette is an exact <circle>. The result is a set of <path>/<circle> STROKES (fill:none), so the
compositor controls stroke width and colour uniformly.

Outputs: with --out FILE, a standalone SVG (optional navy tile) for eyeballing. With --json FILE, a
JSON bundle {geometry, limb, meridians[], parallels[], orbit_back, orbit_front} of path 'd' strings for
a compositor to layer with a traced organic mark.

Usage:
  globe.py --cx 647 --cy 790 --r 210 --tilt 12 --meridians 7 --parallels 5 \
           --orbit-rx 300 --orbit-ry 95 --orbit-rot -18 --orbit-dy -6 \
           --stroke 8 --color '#F2EDE1' --field '#152A47' --out globe.svg --json globe.json
"""
import argparse
import json
import math

TAU = 2.0 * math.pi


def cubic_arc(C, a, b, t0, t1, max_seg=math.pi / 2):
    """SVG path 'd' for the elliptical arc P(t)=C+a*cos t+b*sin t, t in [t0, t1], as exact cubics."""
    span = t1 - t0
    n = max(1, int(math.ceil(abs(span) / max_seg)))
    dt = span / n

    def P(t):
        return (C[0] + a[0] * math.cos(t) + b[0] * math.sin(t),
                C[1] + a[1] * math.cos(t) + b[1] * math.sin(t))

    def D(t):
        return (-a[0] * math.sin(t) + b[0] * math.cos(t),
                -a[1] * math.sin(t) + b[1] * math.cos(t))

    alpha = (4.0 / 3.0) * math.tan(dt / 4.0)
    p0 = P(t0)
    out = [f"M {p0[0]:.3f} {p0[1]:.3f}"]
    for i in range(n):
        ta, tb = t0 + i * dt, t0 + (i + 1) * dt
        pa, pb, da, db = P(ta), P(tb), D(ta), D(tb)
        c1 = (pa[0] + alpha * da[0], pa[1] + alpha * da[1])
        c2 = (pb[0] - alpha * db[0], pb[1] - alpha * db[1])
        out.append(f"C {c1[0]:.3f} {c1[1]:.3f} {c2[0]:.3f} {c2[1]:.3f} {pb[0]:.3f} {pb[1]:.3f}")
    return " ".join(out)


def parallel_path(cx, cy, r, beta, th):
    """Visible front arc of the parallel at latitude th (radians). Axis-aligned ellipse."""
    ct, st = math.cos(th), math.sin(th)
    cb, sb = math.cos(beta), math.sin(beta)
    # screen: sx = cx + (r ct) sin(la); sy = cy - (r st cb) + (r ct sb) cos(la)  -> param t = la
    C = (cx, cy - r * st * cb)
    a = (0.0, r * ct * sb)          # cos(la) coefficient
    b = (r * ct, 0.0)               # sin(la) coefficient
    # visible where Z = r st sb + r ct cb cos(la) > 0  ->  cos(la) > c0
    denom = ct * cb
    if abs(denom) < 1e-9:
        return None
    c0 = -(st * sb) / denom
    if c0 <= -1.0:
        return cubic_arc(C, a, b, -math.pi, math.pi)   # whole parallel visible
    if c0 >= 1.0:
        return None                                    # entirely on the far side
    la0 = math.acos(c0)
    return cubic_arc(C, a, b, -la0, la0)


def meridian_path(cx, cy, r, beta, la):
    """Visible front arc of the meridian at longitude la (radians). Rotated ellipse."""
    cl, sl = math.cos(la), math.sin(la)
    cb, sb = math.cos(beta), math.sin(beta)
    # param t = latitude; screen offsets from centre:
    #   sx-cx = (r sl) cos t ;  sy-cy = (r cl sb) cos t + (-r cb) sin t
    C = (cx, cy)
    a = (r * sl, r * cl * sb)       # cos t coefficient
    b = (0.0, -r * cb)              # sin t coefficient
    # visible where Z = r sin t sb + r cos t cl cb > 0  -> centred 180-degree arc at t_max. But a
    # meridian LINE only spans latitude [-90, 90]; the slice past a pole belongs to the antipodal
    # meridian, so clamp to the real latitude domain or it pokes a whisker past the pole.
    t_max = math.atan2(sb, cl * cb)
    t0 = max(t_max - math.pi / 2, -math.pi / 2)
    t1 = min(t_max + math.pi / 2, math.pi / 2)
    return cubic_arc(C, a, b, t0, t1)


def orbit_arcs(cx, cy, orx, ory, rot_deg, dx, dy):
    """A tilted ellipse ring split into front (nearer/lower) and back arcs at its major-axis ends."""
    rot = math.radians(rot_deg)
    cr, sr = math.cos(rot), math.sin(rot)
    C = (cx + dx, cy + dy)
    a = (orx * cr, orx * sr)        # major axis (cos t)
    b = (-ory * sr, ory * cr)       # minor axis (sin t)

    def mean_y(t0, t1, n=24):
        return sum(C[1] + a[1] * math.cos(t0 + (t1 - t0) * k / n)
                   + b[1] * math.sin(t0 + (t1 - t0) * k / n) for k in range(n + 1)) / (n + 1)

    half_a = cubic_arc(C, a, b, 0.0, math.pi)
    half_b = cubic_arc(C, a, b, math.pi, TAU)
    # front = the arc whose average screen-y is larger (nearer the viewer / lower on the tile)
    if mean_y(0.0, math.pi) >= mean_y(math.pi, TAU):
        return half_b, half_a          # back, front
    return half_a, half_b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cx", type=float, required=True)
    ap.add_argument("--cy", type=float, required=True)
    ap.add_argument("--r", type=float, required=True)
    ap.add_argument("--tilt", type=float, default=12.0, help="view tilt in degrees (north toward viewer)")
    ap.add_argument("--meridians", type=int, default=7, help="interior meridian count (excl. limb)")
    ap.add_argument("--parallels", type=int, default=5, help="interior parallel count (excl. poles)")
    ap.add_argument("--stroke", type=float, default=8.0)
    ap.add_argument("--limb-stroke", type=float, default=None, help="silhouette width (default: --stroke)")
    ap.add_argument("--color", default="#F2EDE1")
    ap.add_argument("--field", default="#152A47", help="tile colour for the standalone --out preview")
    ap.add_argument("--orbit-rx", type=float, default=None, help="orbit semi-major; omit to skip orbit")
    ap.add_argument("--orbit-ry", type=float, default=90.0)
    ap.add_argument("--orbit-rot", type=float, default=-18.0, help="orbit tilt in degrees")
    ap.add_argument("--orbit-dx", type=float, default=0.0)
    ap.add_argument("--orbit-dy", type=float, default=0.0)
    ap.add_argument("--orbit-stroke", type=float, default=None, help="orbit width (default: --stroke)")
    ap.add_argument("--width", type=int, default=None, help="standalone canvas size (default 2*(cx or cy))")
    ap.add_argument("--out", default=None, help="write a standalone preview SVG")
    ap.add_argument("--json", default=None, help="write a JSON path bundle for compositing")
    a = ap.parse_args()

    beta = math.radians(a.tilt)
    lons = [math.radians(x) for x in _interior(-90, 90, a.meridians)]
    lats = [math.radians(x) for x in _interior(-90, 90, a.parallels)]

    meridians = [meridian_path(a.cx, a.cy, a.r, beta, la) for la in lons]
    parallels = [p for p in (parallel_path(a.cx, a.cy, a.r, beta, th) for th in lats) if p]
    orbit_back = orbit_front = None
    if a.orbit_rx:
        orbit_back, orbit_front = orbit_arcs(a.cx, a.cy, a.orbit_rx, a.orbit_ry,
                                             a.orbit_rot, a.orbit_dx, a.orbit_dy)

    limb_w = a.limb_stroke if a.limb_stroke is not None else a.stroke
    orbit_w = a.orbit_stroke if a.orbit_stroke is not None else a.stroke
    bundle = {
        "geometry": dict(cx=a.cx, cy=a.cy, r=a.r, tilt=a.tilt, meridians=a.meridians,
                         parallels=a.parallels, stroke=a.stroke, limb_stroke=limb_w,
                         orbit_stroke=orbit_w, color=a.color),
        "limb": dict(cx=a.cx, cy=a.cy, r=a.r, stroke=limb_w),
        "meridians": meridians,
        "parallels": parallels,
        "orbit_back": orbit_back,
        "orbit_front": orbit_front,
    }

    if a.json:
        with open(a.json, "w") as f:
            json.dump(bundle, f, indent=1)

    if a.out:
        W = a.width or int(2 * max(a.cx, a.cy))
        graticule = graticule_svg(a.color, a.stroke, limb_w, a.cx, a.cy, a.r, meridians, parallels)
        body = (f'<rect width="{W}" height="{W}" rx="{0.16 * W:.0f}" fill="{a.field}"/>'
                + orbit_layer(a.orbit_rx, orbit_back, a.color, orbit_w)
                + graticule
                + orbit_layer(a.orbit_rx, orbit_front, a.color, orbit_w))
        with open(a.out, "w") as f:
            f.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" '
                    f'viewBox="0 0 {W} {W}">{body}</svg>\n')

    print(f"globe: {len(meridians)} meridians, {len(parallels)} parallels, "
          f"orbit={'yes' if a.orbit_rx else 'no'}"
          + (f"; wrote {a.out}" if a.out else "")
          + (f"; wrote {a.json}" if a.json else ""))


def _interior(lo, hi, n):
    """n evenly-spaced interior values across (lo, hi), excluding the endpoints."""
    if n <= 0:
        return []
    step = (hi - lo) / (n + 1)
    return [lo + step * (i + 1) for i in range(n)]


def graticule_svg(color, stroke, limb_w, cx, cy, r, meridians, parallels):
    lines = "".join(f'<path d="{d}"/>' for d in meridians + parallels)
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" '
            f'stroke-width="{limb_w}"/>'
            f'<g fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-linejoin="round">{lines}</g>')


def orbit_layer(has_orbit, d, color, w):
    if not has_orbit:
        return ""
    return (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{w}" '
            f'stroke-linecap="round"/>')


if __name__ == "__main__":
    main()
