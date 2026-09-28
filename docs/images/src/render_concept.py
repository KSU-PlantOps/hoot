"""Preliminary isometric concept render of the HOOT enclosure and probe head.

Hand-rolled isometric projection in 2D rather than matplotlib's 3D axes: the
painter's order is explicit, so surface detail (display, vents, ports, gold
banding) always lands on top of the face it belongs to.

Proportions are taken from the real parts -- Raspberry Pi 5 footprint plus the
PoE HAT stack height, and an SCD-41 + SHT45 sensor head -- but this is a concept
image, not a manufacturing drawing.
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, FancyArrowPatch

C30, S30 = np.cos(np.pi / 6), np.sin(np.pi / 6)

GOLD  = "#FFC629"
BODY  = "#454B52"
INK   = "#231F20"
MUTED = "#6B6B6B"
EDGE  = "#191C1F"

def iso(x, y, z):
    """Isometric projection: +x right-and-up, +y left-and-up, +z straight up."""
    return np.array([(x - y) * C30, (x + y) * S30 + z])

def shade(color, f):
    if isinstance(color, str):
        c = np.array([int(color[i:i+2], 16) for i in (1, 3, 5)]) / 255.0
    else:
        c = np.array(color[:3], dtype=float)
    return tuple(np.clip(c * f, 0, 1))

# face light factors: top brightest, y=0 wall mid, x=0 wall darkest
F_TOP, F_RIGHT, F_LEFT = 1.00, 0.74, 0.52

def face(ax, pts3, color, ec=EDGE, lw=0.8, z=1):
    poly = Polygon([iso(*p) for p in pts3], closed=True, facecolor=color,
                   edgecolor=ec, linewidth=lw, zorder=z, joinstyle="miter")
    ax.add_patch(poly)
    return poly

def draw_box(ax, o, s, color, z=1):
    """Three visible faces of an axis-aligned box, drawn back to front."""
    x, y, zz = o; dx, dy, dz = s
    # left wall (x = x plane), right wall (y = y plane), top
    face(ax, [(x, y, zz), (x, y+dy, zz), (x, y+dy, zz+dz), (x, y, zz+dz)],
         shade(color, F_LEFT), z=z)
    face(ax, [(x, y, zz), (x+dx, y, zz), (x+dx, y, zz+dz), (x, y, zz+dz)],
         shade(color, F_RIGHT), z=z)
    face(ax, [(x, y, zz+dz), (x+dx, y, zz+dz), (x+dx, y+dy, zz+dz), (x, y+dy, zz+dz)],
         shade(color, F_TOP), z=z)

def on_right(ax, x0, x1, z0, z1, y, color, z=2, ec=None, lw=0.0):
    """Rectangle applied to a y = const wall."""
    face(ax, [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)], color,
         ec=ec if ec else color, lw=lw, z=z)

def on_left(ax, y0, y1, z0, z1, x, color, z=2, ec=None, lw=0.0):
    """Rectangle applied to an x = const wall."""
    face(ax, [(x, y0, z0), (x, y1, z0), (x, y1, z1), (x, y0, z1)], color,
         ec=ec if ec else color, lw=lw, z=z)

def on_top(ax, x0, x1, y0, y1, zc, color, z=2, ec=None, lw=0.0):
    face(ax, [(x0, y0, zc), (x1, y0, zc), (x1, y1, zc), (x0, y1, zc)], color,
         ec=ec if ec else color, lw=lw, z=z)

fig, ax = plt.subplots(figsize=(10.5, 4.4), dpi=260)
ax.set_aspect("equal"); ax.axis("off")
fig.patch.set_facecolor("white")

# ============================================================== probe head
HX, HY, HZ = 46.0, 30.0, 22.0
hox, hoy, hoz = -132.0, 8.0, 40.0
draw_box(ax, (hox, hoy, hoz), (HX, HY, HZ), shade(BODY, 1.10), z=1)
# louvres on the y = hoy wall
for i in range(5):
    zc = hoz + 3.6 + i * 3.3
    on_right(ax, hox + 5, hox + HX - 5, zc, zc + 1.7, hoy, shade(BODY, 0.40), z=2)
# louvres on the top
for i in range(4):
    xc = hox + 6 + i * 9.6
    on_top(ax, xc, xc + 5.4, hoy + 5, hoy + HY - 5, hoz + HZ, shade(BODY, 0.62), z=2)
# gold band ties it to the main unit
on_right(ax, hox, hox + HX, hoz + HZ - 4.2, hoz + HZ - 1.6, hoy, GOLD, z=3)

# =================================================================== cable
p0 = np.array([0.0, 46.0, 24.0])                    # gland on the main unit
p1 = np.array([hox + HX, hoy + HY / 2, hoz + HZ / 2])
t = np.linspace(0, 1, 240)[:, None]
c1 = p0 + np.array([-30, 34, 30])
c2 = p1 + np.array([46, 4, 26])
curve = (1-t)**3 * p0 + 3*(1-t)**2*t * c1 + 3*(1-t)*t**2 * c2 + t**3 * p1
pts = np.array([iso(*p) for p in curve])
ax.plot(pts[:, 0], pts[:, 1], color="#1F2225", lw=5.0, solid_capstyle="round", zorder=4)
ax.plot(pts[:, 0], pts[:, 1], color="#555B61", lw=1.8, solid_capstyle="round", zorder=5)

# =============================================================== main unit
BX, BY, BZ = 100.0, 70.0, 46.0
draw_box(ax, (0, 0, 0), (BX, BY, BZ), BODY, z=6)

# --- display on the y = 0 wall
ox, oz, ow, oh = 14.0, 15.0, 44.0, 24.0
on_right(ax, ox, ox+ow, oz, oz+oh, 0, "#1B1F23", z=7, ec="#0A0C0E", lw=1.0)
on_right(ax, ox+2.4, ox+ow-2.4, oz+2.4, oz+oh-2.4, 0, "#0C1014", z=8)
for yy, wf, col in [(0.72, 0.62, GOLD), (0.44, 0.80, "#9AA4B2"), (0.20, 0.46, "#9AA4B2")]:
    zc = oz + 3.4 + (oh - 6.8) * yy
    on_right(ax, ox+4.4, ox+4.4 + (ow-8.8)*wf, zc, zc+1.9, 0, col, z=9)
# gold identity band + status LED
on_right(ax, 0, BX, 5.5, 8.8, 0, GOLD, z=7)
on_right(ax, 68.0, 72.5, 20.0, 24.5, 0, "#3FB950", z=8)
# RJ45 / PoE at the far end of the same wall
on_right(ax, 80.0, 95.0, 14.0, 27.0, 0, "#1B1F23", z=7, ec="#0A0C0E", lw=1.0)
on_right(ax, 82.0, 93.0, 16.0, 25.0, 0, "#07090B", z=8)

# --- fan exhaust vents on the top
for i in range(7):
    xc = 10.0 + i * 12.0
    on_top(ax, xc, xc + 6.0, 14.0, 40.0, BZ, shade(BODY, 0.64), z=7)

# --- cable gland on the x = 0 wall
on_left(ax, 39.0, 53.0, 17.0, 31.0, 0, shade(BODY, 0.34), z=7, ec=EDGE, lw=0.8)
on_left(ax, 42.0, 50.0, 20.0, 28.0, 0, shade(BODY, 0.22), z=8)

# ================================================================== labels
def label(anchor3, txt, sub, tx, ty, ha="left"):
    a = iso(*anchor3) if len(anchor3) == 3 else anchor3
    ax.annotate("", xy=(a[0], a[1]), xytext=(tx, ty),
                arrowprops=dict(arrowstyle="-", color="#B0B3B2", lw=0.9,
                                shrinkA=0, shrinkB=3), zorder=20)
    ax.text(tx, ty + 3.0, txt, ha=ha, va="bottom", fontsize=9.2,
            fontweight="bold", color=INK, zorder=21)
    ax.text(tx, ty - 1.5, sub, ha=ha, va="top", fontsize=7.6, color=MUTED,
            zorder=21, linespacing=1.45)

# Labels sit outside the object silhouette; only the leader lines cross it.
label((36, 0, 27), "128 x 64 OLED",
      "IP address, live readings,\nBACnet device ID", 6, -46, ha="center")
label((88, 0, 20), "PoE in", "power + data, one drop", 122, 44)
label((58, 27, BZ), "Fan exhaust", "PoE HAT (F)", 118, 128)
# Leader lands on the cable itself, and leaves the label from its right-hand
# end so it never crosses the caption text.
label(pts[int(len(pts) * 0.42)], "1 m sensor lead",
      "keeps the sensing element out\nof the Pi's thermal plume", -120, 104, ha="right")
label((hox + HX/2, hoy, hoz + HZ/2), "Vented probe head",
      "SHT45 + TMP119 + SCD-41\n46 x 30 x 22 mm approx.", -252, 2)

# main unit caption - no leader needed, it is plainly the large object
ax.text(126, -18, "Main unit", ha="left", va="bottom",
        fontsize=10.5, fontweight="bold", color=INK, zorder=21)
ax.text(126, -22, "Raspberry Pi 5 +\nWaveshare PoE HAT (F)\n100 x 70 x 46 mm approx.",
        ha="left", va="top", fontsize=7.8, color=MUTED, zorder=21, linespacing=1.45)

ax.autoscale_view()
ax.set_xlim(-262, 250)
ax.set_ylim(-72, 152)
plt.subplots_adjust(0, 0, 1, 1)
out = "hoot-concept.png"
plt.savefig(out, dpi=260, bbox_inches="tight", facecolor="white", pad_inches=0.10)
print("wrote", out)
