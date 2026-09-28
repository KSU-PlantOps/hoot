"""Generate the README banner and system diagram, in light and dark variants."""
from pathlib import Path
import sys

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace"

THEMES = {
    "light": dict(ink="#1f2328", muted="#59636e", faint="#818b98", panel="#f6f8fa",
                  line="#d0d7de", accent="#0969da", gold="#d99e00", goldfill="#fff8e1",
                  owl="#2d333b", owl_edge="#2d333b", eye="#ffffff", pupil="#1f2328",
                  unitfill="#ffffff"),
    "dark": dict(ink="#e6edf3", muted="#9198a1", faint="#6e7681", panel="#161b22",
                 line="#3d444d", accent="#4493f8", gold="#ffc629", goldfill="#2b2410",
                 owl="#3d444d", owl_edge="#656c76", eye="#f0f6fc", pupil="#0d1117",
                 unitfill="#0d1117"),
}


def banner(c):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 260" width="1200" height="260" role="img" aria-labelledby="t d">
  <title id="t">HOOT</title>
  <desc id="d">HOOT: Humidity and Operational Observation Terminal. A portable BACnet/IP temperature, humidity and CO2 probe.</desc>
  <!-- owl mark -->
  <g transform="translate(40 18)">
    <path d="M52 62 L40 8 L92 44 Z" fill="{c['owl']}" stroke="{c['owl_edge']}" stroke-width="3" stroke-linejoin="round"/>
    <path d="M168 62 L180 8 L128 44 Z" fill="{c['owl']}" stroke="{c['owl_edge']}" stroke-width="3" stroke-linejoin="round"/>
    <ellipse cx="110" cy="122" rx="88" ry="98" fill="{c['owl']}" stroke="{c['owl_edge']}" stroke-width="3"/>
    <circle cx="74" cy="104" r="36" fill="{c['eye']}" stroke="{c['gold']}" stroke-width="8"/>
    <circle cx="146" cy="104" r="36" fill="{c['eye']}" stroke="{c['gold']}" stroke-width="8"/>
    <circle cx="78" cy="106" r="15" fill="{c['pupil']}"/>
    <circle cx="142" cy="106" r="15" fill="{c['pupil']}"/>
    <circle cx="83" cy="100" r="4.5" fill="{c['eye']}"/>
    <circle cx="147" cy="100" r="4.5" fill="{c['eye']}"/>
    <path d="M100 138 L120 138 L110 158 Z" fill="{c['gold']}"/>
    <path d="M84 176 l8 8 l8 -8 M102 192 l8 8 l8 -8 M120 176 l8 8 l8 -8" fill="none" stroke="{c['faint']}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  </g>
  <!-- signal arcs: this owl talks BACnet -->
  <g fill="none" stroke="{c['accent']}" stroke-width="4" stroke-linecap="round" opacity="0.9">
    <path d="M236 70 a40 40 0 0 1 0 56"/>
    <path d="M252 56 a60 60 0 0 1 0 84"/>
  </g>
  <text x="300" y="118" font-family="{SANS}" font-size="92" font-weight="800" letter-spacing="6" fill="{c['ink']}">HOOT</text>
  <text x="304" y="160" font-family="{SANS}" font-size="24" fill="{c['muted']}"><tspan font-weight="700" fill="{c['gold']}">H</tspan>umidity &amp; <tspan font-weight="700" fill="{c['gold']}">O</tspan>perational <tspan font-weight="700" fill="{c['gold']}">O</tspan>bservation <tspan font-weight="700" fill="{c['gold']}">T</tspan>erminal</text>
  <text x="304" y="204" font-family="{SANS}" font-size="19" fill="{c['ink']}">A portable temperature, humidity &amp; CO₂ probe that any BAS discovers as a BACnet/IP device.</text>
</svg>
'''


def box(x, y, w, h, c, title, lines=(), fill=None, stroke=None, sw=1.5, tsize=15, mono=False):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill or c["panel"]}" stroke="{stroke or c["line"]}" stroke-width="{sw}"/>',
           f'<text x="{x + 14}" y="{y + 26}" font-family="{SANS}" font-size="{tsize}" font-weight="700" fill="{c["ink"]}">{title}</text>']
    for i, ln in enumerate(lines):
        fam = MONO if mono else SANS
        out.append(f'<text x="{x + 14}" y="{y + 48 + i * 19}" font-family="{fam}" font-size="12.5" fill="{c["muted"]}">{ln}</text>')
    return "\n    ".join(out)


def arrow(x1, y1, x2, y2, c, color=None, dash=False):
    d = ' stroke-dasharray="6 5"' if dash else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color or c["accent"]}" '
            f'stroke-width="2" marker-end="url(#arrow)"{d}/>')


def elbow(points, c, color=None):
    pts = " ".join(f"{x},{y}" for x, y in points)
    return (f'<polyline points="{pts}" fill="none" stroke="{color or c["accent"]}" stroke-width="2" '
            f'stroke-linejoin="round" marker-end="url(#arrow)"/>')


def diagram(c):
    parts = [
        # probe head
        box(20, 135, 210, 150, c, "Vented probe head",
            ["SHT45 · temp / RH", "TMP119 · reference temp", "SCD41 · CO₂ (true NDIR)"],
            stroke=c["gold"], sw=2),
        f'<text x="125" y="310" text-anchor="middle" font-family="{SANS}" font-size="12" fill="{c["faint"]}">at breathing height, in a return,</text>',
        f'<text x="125" y="327" text-anchor="middle" font-family="{SANS}" font-size="12" fill="{c["faint"]}">or beside the sensor under audit</text>',
        # cable
        f'<path d="M230 210 L 318 210" stroke="{c["ink"]}" stroke-width="4" fill="none" stroke-linecap="round"/>',
        f'<text x="265" y="197" text-anchor="middle" font-family="{SANS}" font-size="12.5" font-weight="700" fill="{c["ink"]}">1 m</text>',
        f'<text x="265" y="231" text-anchor="middle" font-family="{SANS}" font-size="11.5" fill="{c["muted"]}">I²C</text>',
        # unit
        f'<rect x="300" y="40" width="490" height="420" rx="14" fill="{c["unitfill"]}" stroke="{c["gold"]}" stroke-width="2.5"/>',
        f'<text x="320" y="72" font-family="{SANS}" font-size="17" font-weight="800" fill="{c["ink"]}">HOOT unit</text>',
        f'<text x="410" y="72" font-family="{SANS}" font-size="13" fill="{c["muted"]}">Raspberry Pi 5 + PoE HAT, out of the airstream</text>',
        box(320, 175, 205, 70, c, "Sensor drivers", ["per-channel faults, never stale"]),
        box(320, 290, 205, 70, c, "Calibrate + convert", ["offsets in SI · publish °F or °C"]),
        arrow(422, 245, 422, 286, c),
        box(570, 92, 200, 70, c, "BACnet/IP server", ["UDP 47808 · analog inputs"]),
        box(570, 180, 200, 70, c, "Local trend", ["SQLite · min / max / mean"]),
        box(570, 268, 200, 70, c, "Web UI + JSON API", ["port 8080 · works offline"]),
        box(570, 356, 200, 70, c, "OLED status", ["IP, live readings, health"]),
        elbow([(525, 325), (547, 325), (547, 127), (566, 127)], c),
        elbow([(547, 215), (566, 215)], c),
        elbow([(547, 303), (566, 303)], c),
        elbow([(547, 325), (547, 391), (566, 391)], c),
        # network trunk
        f'<rect x="808" y="80" width="30" height="358" rx="15" fill="{c["goldfill"]}" stroke="{c["gold"]}" stroke-width="2"/>',
        f'<text transform="translate(828 259) rotate(90)" text-anchor="middle" font-family="{SANS}" font-size="12" font-weight="700" letter-spacing="1" fill="{c["muted"]}">ONE ETHERNET DROP · PoE POWER + DATA</text>',
        f'<line x1="770" y1="127" x2="806" y2="127" stroke="{c["accent"]}" stroke-width="2"/>',
        f'<line x1="770" y1="303" x2="806" y2="303" stroke="{c["accent"]}" stroke-width="2"/>',
        # destinations
        box(880, 70, 300, 150, c, "Any BACnet/IP BAS",
            ["Niagara · Metasys · WebCTRL", "Delta · Desigo · EcoStruxure", "", "Who-Is / I-Am · ReadProperty · COV"]),
        box(880, 280, 300, 150, c, "Technician's laptop",
            ["Live status and trends", "Calibration and settings", "", "CSV · logs · support bundle"]),
        elbow([(840, 127), (876, 127)], c),
        elbow([(840, 340), (876, 340)], c),
        f'<text x="880" y="252" font-family="{SANS}" font-size="12" fill="{c["faint"]}">no driver · no gateway · no cloud</text>',
    ]
    body = "\n    ".join(parts)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 480" width="1200" height="480" role="img" aria-labelledby="t d">
  <title id="t">How HOOT fits together</title>
  <desc id="d">A vented probe head with SHT45, TMP119 and SCD41 sensors connects over a 1 m I2C cable to the HOOT unit, a Raspberry Pi 5 with a PoE HAT. Inside, sensor drivers feed calibration, which feeds a BACnet/IP server, a local SQLite trend, a web UI and JSON API, and an OLED status screen. One PoE Ethernet drop connects it to any BACnet/IP building automation system and to a technician's laptop.</desc>
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 0 L10 5 L0 10 z" fill="{c['accent']}"/>
    </marker>
  </defs>
    {body}
</svg>
'''


for name, c in THEMES.items():
    (OUT / f"banner-{name}.svg").write_text(banner(c))
    (OUT / f"architecture-{name}.svg").write_text(diagram(c))
print("ok")
