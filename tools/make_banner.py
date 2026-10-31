"""Generate docs/assets/icecore-banner.svg: the Frozen Geometry banner, drawn as vector shapes."""
import math
import random
import sys

W, H = 1600, 560
WATER = 168            # waterline
CX = 1120              # iceberg centre line
APEX_Y = 540           # bottom of the underwater mass
random.seed(7)         # deterministic facets


def f(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def poly(points, fill, opacity=None, stroke=None):
    pts = " ".join(f"{f(x)},{f(y)}" for x, y in points)
    extra = f' fill-opacity="{opacity}"' if opacity is not None else ""
    extra += f' stroke="{stroke}" stroke-width="0.6" stroke-opacity="0.35"' if stroke else ""
    return f'<polygon points="{pts}" fill="{fill}"{extra}/>'


def hsl(h, s, l):
    return f"hsl({h:.0f},{s:.0f}%,{l:.0f}%)"


# ---------- underwater mass: rows of jittered points, strip-triangulated ----------
ROWS = 11
rows = []
for i in range(ROWS + 1):
    t = i / ROWS
    y = WATER + (APEX_Y - WATER) * t
    half = 330 * (1 - t) ** 0.85 * (1 + (random.uniform(-0.07, 0.07) if 0 < i < ROWS else 0))
    if i == ROWS:
        rows.append([(CX, APEX_Y)])
        continue
    n = max(2, round(2 * half / 64))
    row = []
    for k in range(n + 1):
        x = CX - half + 2 * half * k / n
        jx = 0 if k in (0, n) else random.uniform(-14, 14)
        jy = 0 if i == 0 else random.uniform(-9, 9)
        row.append((x + jx, y + jy))
    # the core line runs straight down the middle
    row.append((CX, y))
    row.sort()
    rows.append(row)

facets = []
for upper, lower in zip(rows, rows[1:]):
    a = b = 0
    while a < len(upper) - 1 or b < len(lower) - 1:
        if b == len(lower) - 1 or (a < len(upper) - 1 and upper[a + 1][0] <= lower[b + 1][0]):
            tri = [upper[a], upper[a + 1], lower[b]]
            a += 1
        else:
            tri = [upper[a], lower[b + 1], lower[b]]
            b += 1
        facets.append(tri)


def under_colour(tri):
    x = sum(p[0] for p in tri) / 3
    y = sum(p[1] for p in tri) / 3
    depth = (y - WATER) / (APEX_Y - WATER)
    side = (x - CX) / 330                      # -1 left (shadow) .. +1 right (lit)
    light = 50 - 28 * depth + 15 * side + random.uniform(-5, 5)
    hue = 210 - 14 * side + random.uniform(-3, 3)
    sat = 64 + 10 * side + random.uniform(-6, 6)
    return hsl(hue, sat, max(14, min(72, light)))


# ---------- tip above the water: hand-placed facets ----------
TIP = {
    "L": (880, WATER), "a": (955, 128), "b": (1028, 92), "P": (CX, 22), "c": (1196, 74),
    "d": (1268, 118), "R": (1372, WATER), "m1": (1010, 150), "m2": (CX, 120), "m3": (1215, 146),
    "s1": (1060, 70), "s2": (1160, 60),
}
TIP_FACETS = [
    ("L", "a", "m1", 84), ("a", "b", "m1", 88), ("b", "s1", "m1", 90), ("s1", "P", "m2", 93),
    ("s1", "m2", "m1", 86), ("m1", "m2", "L", 80), ("P", "s2", "m2", 99), ("s2", "c", "m3", 97),
    ("s2", "m3", "m2", 95), ("c", "d", "m3", 98), ("d", "R", "m3", 94), ("m3", "R", "m2", 90),
    ("m2", "R", "L", 88),
]

# ---------- distant ridges on the horizon ----------
def ridge(x0, x1, peaks, top):
    pts = [(x0, WATER)]
    for k in range(peaks):
        px = x0 + (x1 - x0) * (k + 0.5) / peaks + random.uniform(-20, 20)
        pts.append((px, WATER - top * random.uniform(0.45, 1.0)))
        pts.append((x0 + (x1 - x0) * (k + 1) / peaks, WATER - top * random.uniform(0.1, 0.3)))
    pts[-1] = (x1, WATER)
    return pts


out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
       f'aria-label="icecore — dbt telemetry, built on Snowflake Event Tables">',
       "<title>icecore — dbt telemetry, built on Snowflake Event Tables</title>",
       "<defs>",
       '<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2f5f93"/>'
       '<stop offset="1" stop-color="#7fb2df"/></linearGradient>',
       '<linearGradient id="sea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0d2c4f"/>'
       '<stop offset="1" stop-color="#050f1f"/></linearGradient>',
       '<radialGradient id="glow" cx="0.5" cy="0.45" r="0.5"><stop offset="0" stop-color="#3fb6ff" stop-opacity="0.35"/>'
       '<stop offset="1" stop-color="#3fb6ff" stop-opacity="0"/></radialGradient>',
       '<linearGradient id="fade" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#050f1f" stop-opacity="0.85"/>'
       '<stop offset="0.45" stop-color="#050f1f" stop-opacity="0"/></linearGradient>',
       "</defs>",
       f'<rect width="{W}" height="{WATER}" fill="url(#sky)"/>',
       f'<rect y="{WATER}" width="{W}" height="{H - WATER}" fill="url(#sea)"/>']

# horizon ridges
for x0, x1, peaks, top, fill in [(420, 900, 4, 38, "#c9dcef"), (1360, 1600, 3, 34, "#c5d9ed"), (600, 860, 3, 22, "#e3eef8")]:
    out.append(poly(ridge(x0, x1, peaks, top), fill, opacity=0.7))

# data rain in the water
for _ in range(34):
    x = random.choice([random.uniform(40, 760), random.uniform(1470, 1580)])
    y0 = WATER + random.uniform(10, 120)
    out.append(f'<line x1="{f(x)}" y1="{f(y0)}" x2="{f(x)}" y2="{f(y0 + random.uniform(60, 240))}" stroke="#5cc0ff" '
               f'stroke-opacity="{random.uniform(0.08, 0.22):.2f}" stroke-width="1" stroke-dasharray="2 7"/>')

# glow behind the mass
out.append(f'<ellipse cx="{CX}" cy="{(WATER + APEX_Y) / 2:.0f}" rx="420" ry="230" fill="url(#glow)"/>')

# underwater facets
for tri in facets:
    out.append(poly(tri, under_colour(tri), stroke="#bfe6ff"))

# strata: one line per layer, the ice-core metaphor (each layer a dbt run)
for row in rows[1:-1]:
    pts = " ".join(f"{f(x)},{f(y)}" for x, y in row)
    out.append(f'<polyline points="{pts}" fill="none" stroke="#e6f6ff" stroke-opacity="0.45" stroke-width="1.2"/>')

# the core: a line straight down the middle with a data point per layer
out.append(f'<line x1="{CX}" y1="{WATER}" x2="{CX}" y2="{APEX_Y}" stroke="#ffffff" stroke-opacity="0.75" stroke-width="1.4"/>')
for row in rows[1:-1]:
    y = row[[p[0] for p in row].index(CX)][1]
    out.append(f'<circle cx="{CX}" cy="{f(y)}" r="2.6" fill="#ffffff"/>')

# tip above the water
for a, b, c, light in TIP_FACETS:
    out.append(poly([TIP[a], TIP[b], TIP[c]], hsl(208, 45, light), stroke="#ffffff"))

# waterline
out.append(f'<rect y="{WATER}" width="{W}" height="6" fill="#9fd8ff" fill-opacity="0.10"/>')
out.append(f'<line x1="0" y1="{WATER}" x2="{W}" y2="{WATER}" stroke="#ffffff" stroke-opacity="0.35" stroke-width="1.2"/>')

# keep the text readable over the scene
out.append(f'<rect y="{WATER}" width="{W}" height="{H - WATER}" fill="url(#fade)"/>')

SANS = "'Avenir Next','Helvetica Neue','Segoe UI',Arial,sans-serif"
MONO = "'SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace"
out += [
    f'<text x="70" y="300" font-family="{SANS}" font-size="112" font-weight="400" letter-spacing="14" fill="#ffffff">'
    f'ICE<tspan fill="#6fd6ff" font-weight="600">CORE</tspan></text>',
    f'<text font-family="{MONO}" font-size="27" fill="#e8f3ff"><tspan x="74" y="366">dbt telemetry, built on</tspan>'
    f'<tspan x="74" y="404">Snowflake Event Tables.</tspan></text>',
    f'<text x="74" y="500" font-family="{SANS}" font-size="16" letter-spacing="5" fill="#b9d8f2">'
    "LOGS  /  TRACES  /  EVENTS  /  OBSERVABILITY</text>",
    "</svg>",
]

sys.stdout.write("\n".join(out) + "\n")
