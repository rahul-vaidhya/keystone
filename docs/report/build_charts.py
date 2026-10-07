"""Emit the report's two bar charts as inline SVG (static, print). Values copied from
backend/eval/results/*.md."""
import sys

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e4e3dc"


def grouped(title, cats, series, ymax, w=640, h=230):
    left, right, top, bottom = 44, 12, 30, 40
    pw, ph = w - left - right, h - top - bottom
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{title}" '
           'xmlns="http://www.w3.org/2000/svg" font-family="Inter, Segoe UI, sans-serif">']
    for i in range(6):
        v = ymax * i / 5
        y = top + ph - ph * v / ymax
        out.append(f'<line x1="{left}" x2="{w-right}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{left-6}" y="{y+3.5:.1f}" font-size="10" text-anchor="end" fill="{MUTED}">{v:.1f}</text>')
    gw = pw / len(cats)
    ns = len(series)
    bw = min(34, (gw - 18) / ns)
    for ci, cat in enumerate(cats):
        gx = left + gw * ci + (gw - bw * ns - 2 * (ns - 1)) / 2
        for si, (name, color, vals) in enumerate(series):
            v = vals[ci]
            bh = ph * v / ymax
            x = gx + si * (bw + 2)
            y = top + ph - bh
            r = 4
            out.append(f'<path d="M{x:.1f},{top+ph:.1f} V{y+r:.1f} Q{x:.1f},{y:.1f} {x+r:.1f},{y:.1f} '
                       f'H{x+bw-r:.1f} Q{x+bw:.1f},{y:.1f} {x+bw:.1f},{y+r:.1f} V{top+ph:.1f} Z" fill="{color}"/>')
            out.append(f'<text x="{x+bw/2:.1f}" y="{y-4:.1f}" font-size="9.5" text-anchor="middle" fill="{INK}">{v:.3f}</text>')
        out.append(f'<text x="{left+gw*ci+gw/2:.1f}" y="{top+ph+16:.1f}" font-size="11" text-anchor="middle" fill="{INK}">{cat}</text>')
    out.append(f'<line x1="{left}" x2="{w-right}" y1="{top+ph}" y2="{top+ph}" stroke="{MUTED}" stroke-width="1"/>')
    if ns > 1:
        lx = left
        for name, color, _ in series:
            out.append(f'<rect x="{lx}" y="8" width="10" height="10" rx="2" fill="{color}"/>')
            out.append(f'<text x="{lx+14}" y="17" font-size="11" fill="{INK}">{name}</text>')
            lx += 14 + 7 * len(name) + 18
    out.append("</svg>")
    return "\n".join(out)


chart_parser = grouped(
    "Textbook eval MRR@10 by method, hosted parser vs local text layer",
    ["tf-idf (lnc.ltc)", "BM25", "hybrid (RRF)", "dense"],
    [("Hosted parser (cloudflare-ai) - before fix", ORANGE, [0.769, 0.798, 0.858, 0.877]),
     ("Local pypdf text layer - after fix", BLUE, [0.854, 0.909, 0.935, 0.935])],
    1.0,
)
chart_scifact = grouped(
    "SciFact nDCG@10 by method",
    ["tf-idf", "BM25", "BM25+zones", "BM25+champ.", "dense", "hybrid RRF"],
    [("nDCG@10", BLUE, [0.6186, 0.6863, 0.6553, 0.6863, 0.7166, 0.7349])],
    0.8,
    h=190,
)

tpl = open(sys.argv[1], encoding="utf-8").read()
out = tpl.replace("{{CHART_PARSER}}", chart_parser).replace("{{CHART_SCIFACT}}", chart_scifact)
open(sys.argv[2], "w", encoding="utf-8").write(out)
print("ok")
