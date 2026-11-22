"""Frame renderer — every visual is drawn from case data at render time.

The look: archival-documentary. Near-black ground, film grain, serif headlines,
gold for the record, red for counter-evidence. A "LIVE CASE FILE" bar sits on
every frame because the status shown is the status computed that day.
"""
import os
import random
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .. import config

BG = (13, 15, 18)
PANEL = (21, 25, 32)
LINE = (38, 44, 54)
TEXT = (232, 230, 225)
DIM = (143, 150, 163)
GOLD = (212, 168, 75)
RED = (224, 90, 79)
STATUS = {"documented": (63, 182, 123), "supported_secondary": (127, 179, 224), "contested": (224, 164, 63),
          "unsupported": (154, 143, 127), "contradicted": (224, 90, 79), "unexamined": (120, 128, 142)}
TIER_ORDER = ["primary", "secondary", "tertiary", "unverified"]
TIER_NAME = {"primary": "Primary record", "secondary": "Scholarship", "tertiary": "Later retelling",
             "unverified": "Unverified"}

FONT_DIRS = [os.environ.get("BSI_FONT_DIR", ""), "/usr/share/fonts/truetype/google-fonts/ofl/crimsontext",
             "/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/dejavu",
             "/Library/Fonts", "C:/Windows/Fonts"]
FONT_FILES = {"serif": ["CrimsonText-SemiBold.ttf", "Crimson-Semibold.otf", "DejaVuSerif-Bold.ttf", "georgiab.ttf"],
              "serif_i": ["CrimsonText-Italic.ttf", "Crimson-Italic.otf", "DejaVuSerif-Italic.ttf", "georgiai.ttf"],
              "serif_r": ["CrimsonText-Regular.ttf", "Crimson-Roman.otf", "DejaVuSerif.ttf", "georgia.ttf"],
              "sans": ["Inter-Medium.otf", "DejaVuSans.ttf", "arial.ttf"],
              "sans_b": ["Inter-Bold.otf", "DejaVuSans-Bold.ttf", "arialbd.ttf"],
              "mono": ["DejaVuSansMono.ttf", "consola.ttf"]}


@lru_cache(maxsize=32)
def _font_path(kind: str):
    for f in FONT_FILES[kind]:          # preference order first, then location
        for d in FONT_DIRS:
            if d and (Path(d) / f).exists():
                return str(Path(d) / f)
        for root in ("/usr/share/fonts", "/usr/local/share/fonts"):
            hit = next(Path(root).rglob(f), None) if Path(root).exists() else None
            if hit:
                return str(hit)
    return None


@lru_cache(maxsize=256)
def font(kind: str, size: int):
    p = _font_path(kind)
    return ImageFont.truetype(p, size) if p else ImageFont.load_default(size)


def ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def mix(c, a, bg=BG):
    return tuple(int(bg[i] + (c[i] - bg[i]) * a) for i in range(3))


def wrap(draw, text, fnt, width):
    words, lines, cur = str(text or "").split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def text_block(draw, xy, text, fnt, width, fill, spacing=1.18, max_lines=None):
    x, y = xy
    lines = wrap(draw, text, fnt, width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;: ") + "…"
    lh = int(fnt.size * spacing)
    for ln in lines:
        draw.text((x, y), ln, font=fnt, fill=fill)
        y += lh
    return y


@lru_cache(maxsize=4)
def background(W, H):
    """Dark ground with vignette and fixed film grain."""
    img = Image.new("RGB", (W, H), BG)
    vign = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(vign)
    for i in range(24):
        a = int(110 * (i / 24) ** 2)
        inset = int(min(W, H) * 0.5 * (1 - i / 24))
        d.ellipse([-W * 0.2 + inset, -H * 0.3 + inset, W * 1.2 - inset, H * 1.3 - inset], fill=a)
    vign = vign.filter(ImageFilter.GaussianBlur(min(W, H) // 10))
    glow = Image.new("RGB", (W, H), (24, 27, 33))
    img = Image.composite(glow, img, vign)
    grain = Image.effect_noise((W // 2, H // 2), 22).resize((W, H))
    img = Image.blend(img, Image.merge("RGB", [grain] * 3).point(lambda v: 13 + (v - 128) // 12), 0.10)
    return img


def chrome(img, scene_label, status, as_of, portrait):
    """The persistent 'live case file' bar — the living-data frame on every shot."""
    W, H = img.size
    d = ImageDraw.Draw(img)
    u = min(W, H) / 1080
    pad = int(48 * u)
    y = H - int(64 * u)
    d.line([(pad, y - int(18 * u)), (W - pad, y - int(18 * u))], fill=LINE, width=max(1, int(2 * u)))
    col = STATUS.get(status, DIM)
    r = int(7 * u)
    d.ellipse([pad, y + int(6 * u) - r, pad + 2 * r, y + int(6 * u) + r], fill=col)
    f = font("sans_b", int(20 * u))
    d.text((pad + 3 * r, y - int(6 * u)), "LIVE CASE FILE", font=f, fill=TEXT)
    f2 = font("sans", int(20 * u))
    right = f"STATUS AS OF {as_of}" if not portrait else as_of
    d.text((W - pad - d.textlength(right, font=f2), y - int(6 * u)), right, font=f2, fill=DIM)
    if scene_label and not portrait:
        d.text((W // 2 - d.textlength(scene_label, font=f2) // 2, y - int(6 * u)), scene_label, font=f2, fill=DIM)
    return img


def badge(d, xy, label, col, size):
    f = font("sans_b", size)
    x, y = xy
    w = d.textlength(label, font=f)
    padx, pady = int(size * 0.8), int(size * 0.45)
    d.rounded_rectangle([x, y, x + w + 2 * padx, y + size + 2 * pady], radius=int(size * 0.9),
                        outline=col, width=max(2, size // 9))
    d.text((x + padx, y + pady - size * 0.08), label, font=f, fill=col)
    return x + w + 2 * padx


# ---------------------------------------------------------------- source images
def page_image(ev: dict, cache_dir: Path) -> Image.Image | None:
    """The actual document page, when its rights allow showing it."""
    if not rights_ok(ev.get("rights")):
        return None
    sha = ev["sha256"]
    hits = list(config.VAULT_DIR.glob(f"{sha[:2]}/{sha}*"))
    if not hits:
        return None
    src = hits[0]
    try:
        if src.suffix.lower() == ".pdf":
            out = cache_dir / f"{sha[:16]}_p{ev.get('page') or 1}"
            png = out.with_suffix(".png")
            if not png.exists():
                pg = str(ev.get("page") or 1)
                subprocess.run(["pdftoppm", "-f", pg, "-l", pg, "-r", "110", "-png", "-singlefile",
                                str(src), str(out)], check=True, capture_output=True, timeout=60)
            return Image.open(png).convert("RGB")
        if src.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}:
            return Image.open(src).convert("RGB")
    except Exception:
        return None
    return None


def rights_ok(rights: str | None) -> bool:
    r = (rights or "").lower()
    return any(k in r for k in ("public domain", "pd", "cc0", "cc by", "cc-by", "own", "no known copyright"))


def ken_burns(im: Image.Image, box_w, box_h, t, dur, seed):
    """Slow push-in across the document, direction varied per source."""
    rng = random.Random(seed)
    z0, z1 = 1.0, 1.10 + rng.random() * 0.06
    p = ease(t / max(dur, 0.1)) * 0.85 + (t / max(dur, 0.1)) * 0.15
    z = z0 + (z1 - z0) * p
    scale = max(box_w / im.width, box_h / im.height) * z
    nw, nh = int(im.width * scale), int(im.height * scale)
    big = im.resize((nw, nh), Image.BILINEAR)
    dx = (nw - box_w) * (0.5 + (rng.random() - 0.5) * 0.6 * p)
    dy = (nh - box_h) * (0.15 + 0.5 * p * rng.random())
    return big.crop((int(dx), int(dy), int(dx) + box_w, int(dy) + box_h))


# ---------------------------------------------------------------- scenes
def render(scene: dict, W: int, H: int, t: float, dur: float, ctx: dict) -> Image.Image:
    img = background(W, H).copy()
    portrait = H > W
    fn = {"title": s_title, "claim": s_claim, "timeline": s_timeline, "evidence": s_evidence,
          "scale": s_scale, "questions": s_questions, "note": s_note, "sources": s_sources}[scene["type"]]
    fn(img, scene, t, dur, ctx, portrait)
    labels = {"title": "", "claim": "THE CLAIM", "timeline": "THE PORTRAYALS", "evidence": "THE EVIDENCE",
              "scale": "WHERE IT STANDS", "questions": "WHAT WOULD SETTLE IT", "note": "PRODUCER'S NOTE",
              "sources": "SOURCES"}
    chrome(img, labels[scene["type"]], ctx["status"], ctx["as_of"], portrait)
    if ctx.get("caption"):
        burn_caption(img, ctx["caption"])
    return img


def margins(W, H, portrait):
    u = min(W, H) / 1080
    return u, int((72 if not portrait else 64) * u)


def s_title(img, sc, t, dur, ctx, portrait):
    W, H = img.size
    d = ImageDraw.Draw(img)
    u, m = margins(W, H, portrait)
    y = int(H * (0.16 if not portrait else 0.22))
    a1, a2, a3, a4 = (ease((t - k) / 0.7) for k in (0.0, 0.35, 0.8, 1.3))
    d.text((m, y), f"CASE FILE Nº {sc['case_no']:04d}", font=font("sans_b", int(24 * u)), fill=mix(GOLD, a1))
    y += int(56 * u)
    nf = font("serif", int((112 if not portrait else 104) * u))
    y = text_block(d, (m, y), sc["name"], nf, W - 2 * m, mix(TEXT, a2), spacing=1.02, max_lines=3)
    if sc["years"]:
        d.text((m, y + int(4 * u)), sc["years"], font=font("serif_r", int(40 * u)), fill=mix(DIM, a2))
        y += int(64 * u)
    y += int(24 * u)
    y = text_block(d, (m, y), sc["hook"], font("serif_i", int(46 * u)), W - 2 * m, mix(GOLD, a3), max_lines=2)
    y += int(20 * u)
    y = text_block(d, (m, y), sc["claim"], font("sans", int(30 * u)), int((W - 2 * m) * (0.8 if not portrait else 1)),
                   mix(TEXT, a3 * 0.85), spacing=1.35, max_lines=4)
    y += int(36 * u)
    badge(d, (m, y), STATUS_LABEL_SHORT(sc["status"]), mix(STATUS.get(sc["status"], DIM), a4), int(26 * u))


def STATUS_LABEL_SHORT(st):
    from .episode import STATUS_LABEL
    return STATUS_LABEL.get(st, st.upper())


def s_claim(img, sc, t, dur, ctx, portrait):
    W, H = img.size
    d = ImageDraw.Draw(img)
    u, m = margins(W, H, portrait)
    y = int(H * (0.14 if not portrait else 0.2))
    d.text((m, y), "THE CLAIM", font=font("sans_b", int(24 * u)), fill=GOLD)
    y += int(60 * u)
    a = ease(t / 0.8)
    y = text_block(d, (m, y), f"“{sc['claim']}”", font("serif", int(60 * u)), W - 2 * m, mix(TEXT, a),
                   spacing=1.12, max_lines=5)
    for i, alt in enumerate(sc["alternatives"]):
        ai = ease((t - 1.2 - i * 0.6) / 0.7)
        if ai <= 0:
            continue
        y += int(34 * u)
        d.text((m, y), "OR", font=font("sans_b", int(22 * u)), fill=mix(DIM, ai))
        y = text_block(d, (m + int(64 * u), y - int(6 * u)), alt, font("serif_i", int(38 * u)),
                       W - 2 * m - int(64 * u), mix(DIM, ai), max_lines=3)


def s_timeline(img, sc, t, dur, ctx, portrait):
    W, H = img.size
    d = ImageDraw.Draw(img)
    u, m = margins(W, H, portrait)
    pts = [p for p in sc["points"] if p["year"] is not None]
    d.text((m, int(H * 0.12)), f"EVERY PORTRAYAL OF {sc['name'].upper()}, BY DATE",
           font=font("sans_b", int(24 * u)), fill=GOLD)
    if not pts:
        return
    years = [p["year"] for p in pts] + [y for y in (sc["born"], sc["died"]) if y is not None]
    lo, hi = min(years), max(years)
    span = max(hi - lo, 20)
    lo -= span * 0.08
    hi += span * 0.08
    if not portrait:
        x0, x1, ay = m, W - m, int(H * 0.55)
        X = lambda yr: x0 + (yr - lo) / (hi - lo) * (x1 - x0)  # noqa: E731
        reveal = ease(t / 1.2)
        d.line([(x0, ay), (x0 + (x1 - x0) * reveal, ay)], fill=LINE, width=int(3 * u))
        if sc["born"] is not None and sc["died"] is not None:
            bx0, bx1 = X(sc["born"]), X(sc["died"])
            d.rectangle([bx0, ay - int(10 * u), bx0 + (bx1 - bx0) * reveal, ay + int(10 * u)], fill=mix(GOLD, 0.55))
            d.text((bx0, ay + int(22 * u)), "LIFETIME", font=font("sans_b", int(18 * u)), fill=mix(GOLD, reveal))
        n = len(pts)
        for i, p in enumerate(pts):
            a = ease((t - 1.0 - i * (max(dur - 3, 1) / max(n, 1)) * 0.8) / 0.6)
            if a <= 0:
                continue
            x = X(p["year"])
            col = GOLD if p["made_in_lifetime"] else DIM
            up = i % 2 == 0
            ty = ay - int(200 * u) if up else ay + int(70 * u)
            d.line([(x, ay), (x, ty + (int(150 * u) if up else 0))], fill=mix(col, a), width=int(2 * u))
            d.ellipse([x - 9 * u, ay - 9 * u, x + 9 * u, ay + 9 * u], fill=mix(col, a))
            lab = p["created_date"] or str(p["year"])
            d.text((x - int(8 * u), ty), lab, font=font("sans_b", int(24 * u)), fill=mix(col, a))
            text_block(d, (x - int(8 * u), ty + int(34 * u)), p["descriptor"], font("serif_i", int(28 * u)),
                       int(min(420 * u, W - x - m + 8 * u)), mix(TEXT, a), max_lines=3)
    else:
        x, y0, y1 = int(W * 0.18), int(H * 0.2), int(H * 0.86)
        Y = lambda yr: y0 + (yr - lo) / (hi - lo) * (y1 - y0)  # noqa: E731
        d.line([(x, y0), (x, y1)], fill=LINE, width=int(3 * u))
        if sc["born"] is not None and sc["died"] is not None:
            d.rectangle([x - 10 * u, Y(sc["born"]), x + 10 * u, Y(sc["died"])], fill=mix(GOLD, 0.55))
        for i, p in enumerate(pts):
            a = ease((t - 0.8 - i * 1.2) / 0.6)
            if a <= 0:
                continue
            yy = Y(p["year"])
            col = GOLD if p["made_in_lifetime"] else DIM
            d.ellipse([x - 9 * u, yy - 9 * u, x + 9 * u, yy + 9 * u], fill=mix(col, a))
            d.text((x + int(30 * u), yy - int(16 * u)), p["created_date"] or str(p["year"]),
                   font=font("sans_b", int(26 * u)), fill=mix(col, a))
            text_block(d, (x + int(30 * u), yy + int(16 * u)), p["descriptor"], font("serif_i", int(32 * u)),
                       W - x - int(30 * u) - m, mix(TEXT, a), max_lines=2)


def s_evidence(img, sc, t, dur, ctx, portrait):
    W, H = img.size
    d = ImageDraw.Draw(img)
    u, m = margins(W, H, portrait)
    e = sc["evidence"]
    col = {"supports": GOLD, "contradicts": RED}.get(e["stance"], DIM)
    pic = ctx["page_image"](e)
    if pic is not None:
        if not portrait:
            bw, bh, bx, by = int(W * 0.46), int(H * 0.74), m, int(H * 0.1)
        else:
            bw, bh, bx, by = W - 2 * m, int(H * 0.38), m, int(H * 0.08)
        frame = ken_burns(pic, bw, bh, t, dur, e["sha256"])
        frame = Image.blend(Image.new("RGB", frame.size, BG), frame, 0.92)
        img.paste(frame, (bx, by))
        d.rectangle([bx, by, bx + bw, by + bh], outline=mix(col, 0.8), width=int(3 * u))
        tx, ty, tw = (bx + bw + int(56 * u), int(H * 0.12), W - bx - bw - int(56 * u) - m) if not portrait \
            else (m, by + bh + int(48 * u), W - 2 * m)
    else:
        tx, ty, tw = m, int(H * (0.14 if not portrait else 0.18)), W - 2 * m
    a = ease(t / 0.6)
    stance = {"supports": "SUPPORTS", "contradicts": "CONTRADICTS", "context": "CONTEXT"}[e["stance"]]
    nx = badge(d, (tx, ty), stance, mix(col, a), int(22 * u))
    d.text((nx + int(18 * u), ty + int(8 * u)), f"EVIDENCE {sc['index']} OF {sc['of']}",
           font=font("sans_b", int(20 * u)), fill=mix(DIM, a))
    ty += int(72 * u)
    ty = text_block(d, (tx, ty), e["title"], font("serif", int((52 if pic is None else 44) * u)), tw,
                    mix(TEXT, a), spacing=1.08, max_lines=3)
    meta = " · ".join(x for x in [str(e["date"] or e["year"] or ""), e["holder"] or "",
                                   TIER_NAME.get(e["tier"], e["tier"])] if x)
    ty = text_block(d, (tx, ty + int(8 * u)), meta, font("sans", int(24 * u)), tw, mix(DIM, a), max_lines=2)
    if e["quote"]:
        aq = ease((t - 0.9) / 0.8)
        ty += int(30 * u)
        qf = font("serif_i", int((42 if pic is None else 36) * u))
        y_end = text_block(d, (tx + int(28 * u), ty), f"“{e['quote']}”", qf, tw - int(28 * u),
                           mix(TEXT, aq), spacing=1.25, max_lines=7)
        d.rectangle([tx, ty + int(6 * u), tx + int(6 * u), y_end - int(10 * u)], fill=mix(col, aq))
        ty = y_end
    if e.get("descriptor"):
        ty += int(18 * u)
        ty = text_block(d, (tx, ty), f"Portrays: {e['descriptor']}", font("sans", int(26 * u)), tw,
                        mix(GOLD, a), max_lines=2)
    cite = f"{sc['citation']} · sha256 {e['sha256'][:12]}…"
    if e["added_by"] != "human":
        cite += " · awaiting human review"
    d.text((tx, min(ty + int(30 * u), H - int(130 * u))), cite, font=font("mono", int(20 * u)), fill=DIM)


def s_scale(img, sc, t, dur, ctx, portrait):
    """Diverging bar chart: support to the right, counter-evidence to the left, by tier."""
    W, H = img.size
    d = ImageDraw.Draw(img)
    u, m = margins(W, H, portrait)
    col = STATUS.get(sc["status"], DIM)
    y = int(H * (0.11 if not portrait else 0.14))
    d.text((m, y), "WHERE THE CASE STANDS", font=font("sans_b", int(24 * u)), fill=GOLD)
    y += int(56 * u)
    a = ease(t / 0.8)
    d.text((m, y), sc["label"], font=font("serif", int((92 if not portrait else 84) * u)), fill=mix(col, a))
    y += int(130 * u)
    cx = W // 2 if not portrait else W // 2
    half = (W - 2 * m) // 2 - int(150 * u if not portrait else 40 * u)
    mx = max([1] + list(sc["supports"].values()) + list(sc["contradicts"].values()))
    g = ease((t - 0.6) / 1.4)
    d.text((cx - half, y), "◀ AGAINST", font=font("sans_b", int(20 * u)), fill=RED)
    lab = "FOR ▶"
    d.text((cx + half - d.textlength(lab, font=font("sans_b", int(20 * u))), y), lab,
           font=font("sans_b", int(20 * u)), fill=GOLD)
    y += int(46 * u)
    bh = int(46 * u)
    for tier in TIER_ORDER:
        s_, c_ = sc["supports"].get(tier, 0), sc["contradicts"].get(tier, 0)
        name = TIER_NAME[tier]
        nf = font("sans", int(22 * u))
        d.text((cx - d.textlength(name, font=nf) // 2, y - int(30 * u)), name, font=nf, fill=DIM)
        d.rectangle([cx - half, y, cx + half, y + bh], fill=PANEL)
        if s_:
            d.rectangle([cx, y, cx + half * s_ / mx * g, y + bh], fill=GOLD)
            d.text((cx + half * s_ / mx * g + int(12 * u), y + int(8 * u)), str(s_),
                   font=font("sans_b", int(26 * u)), fill=TEXT)
        if c_:
            d.rectangle([cx - half * c_ / mx * g, y, cx, y + bh], fill=RED)
            txt = str(c_)
            d.text((cx - half * c_ / mx * g - int(12 * u) - d.textlength(txt, font=font("sans_b", int(26 * u))),
                    y + int(8 * u)), txt, font=font("sans_b", int(26 * u)), fill=TEXT)
        d.line([(cx, y - int(6 * u)), (cx, y + bh + int(6 * u))], fill=TEXT, width=int(2 * u))
        y += bh + int(56 * u)
    note = "Computed from the evidence — weighted by source type. Changes when the evidence changes."
    text_block(d, (m, y), note, font("sans", int(22 * u)), W - 2 * m, DIM, max_lines=2)


def s_questions(img, sc, t, dur, ctx, portrait):
    W, H = img.size
    d = ImageDraw.Draw(img)
    u, m = margins(W, H, portrait)
    y = int(H * (0.12 if not portrait else 0.16))
    d.text((m, y), "WHAT WOULD SETTLE IT", font=font("sans_b", int(24 * u)), fill=GOLD)
    y += int(70 * u)
    for i, q in enumerate(sc["questions"]):
        a = ease((t - i * 0.9) / 0.7)
        d.text((m, y), f"{i + 1:02d}", font=font("serif", int(48 * u)), fill=mix(GOLD, a))
        y2 = text_block(d, (m + int(90 * u), y + int(4 * u)), q["q"], font("serif_r", int(40 * u)),
                        W - 2 * m - int(90 * u), mix(TEXT, a), max_lines=3)
        y2 = text_block(d, (m + int(90 * u), y2 + int(4 * u)), " · ".join(q["record_types"]),
                        font("sans", int(22 * u)), W - 2 * m - int(90 * u), mix(DIM, a), max_lines=2)
        y = y2 + int(40 * u)
    if sc.get("negative"):
        a = ease((t - 2.8) / 0.7)
        text_block(d, (m, y), f"Would count against it: {sc['negative']}", font("serif_i", int(32 * u)),
                   W - 2 * m, mix(RED, a), max_lines=3)


def s_note(img, sc, t, dur, ctx, portrait):
    W, H = img.size
    d = ImageDraw.Draw(img)
    u, m = margins(W, H, portrait)
    y = int(H * 0.16)
    d.text((m, y), "FROM THE PRODUCER", font=font("sans_b", int(24 * u)), fill=GOLD)
    text_block(d, (m, y + int(70 * u)), sc["text"], font("serif_r", int(50 * u)), W - 2 * m,
               mix(TEXT, ease(t / 0.8)), spacing=1.25, max_lines=9)


def s_sources(img, sc, t, dur, ctx, portrait):
    W, H = img.size
    d = ImageDraw.Draw(img)
    u, m = margins(W, H, portrait)
    y = int(H * 0.1)
    d.text((m, y), f"SOURCES · CASE FILE Nº {sc['case_no']:04d}", font=font("sans_b", int(24 * u)), fill=GOLD)
    y += int(64 * u)
    if not sc["sources"]:
        text_block(d, (m, y), "No sources linked yet. This case is open.", font("serif_i", int(44 * u)),
                   W - 2 * m, TEXT)
    for i, s in enumerate(sc["sources"][:8]):
        a = ease((t - i * 0.35) / 0.5)
        col = {"supports": GOLD, "contradicts": RED}.get(s["stance"], DIM)
        d.rectangle([m, y + int(6 * u), m + int(6 * u), y + int(56 * u)], fill=mix(col, a))
        y2 = text_block(d, (m + int(24 * u), y), s["title"], font("serif", int(32 * u)), W - 2 * m - int(24 * u),
                        mix(TEXT, a), max_lines=1)
        meta = " · ".join(x for x in [s["holder"] or "", TIER_NAME.get(s["tier"], ""), str(s["date"] or s["year"] or ""),
                                       f"sha256 {s['sha256'][:10]}…"] if x)
        text_block(d, (m + int(24 * u), y2), meta, font("sans", int(20 * u)), W - 2 * m - int(24 * u),
                   mix(DIM, a), max_lines=1)
        y += int(92 * u)
    d.text((m, H - int(150 * u)), "If you hold records that bear on this case, the file stays open.",
           font=font("serif_i", int(30 * u)), fill=GOLD)


def burn_caption(img, text):
    """Large, high-contrast captions for vertical video (most viewers watch muted)."""
    W, H = img.size
    d = ImageDraw.Draw(img)
    u = W / 1080
    f = font("sans_b", int(46 * u))
    lines = wrap(d, text, f, int(W * 0.84))[:3]
    lh = int(f.size * 1.22)
    y = int(H * 0.70) - lh * len(lines) // 2
    for ln in lines:
        w = d.textlength(ln, font=f)
        x = (W - w) / 2
        d.rounded_rectangle([x - 18 * u, y - 6 * u, x + w + 18 * u, y + lh - 2 * u], radius=int(10 * u),
                            fill=(0, 0, 0))
        d.text((x, y), ln, font=f, fill=(255, 255, 255))
        y += lh


def still(scene, W, H, ctx):
    """A fully revealed frame — for thumbnails and carousel slides."""
    return render(scene, W, H, 30.0, 30.0, {**ctx, "caption": None})


def thumbnail(script, W=1280, H=720, cache_dir: Path | None = None):
    img = background(W, H).copy()
    d = ImageDraw.Draw(img)
    u = H / 720
    t0 = script["scenes"][0]
    col = STATUS.get(script["status"], DIM)
    # the actual archival document, when its rights allow — the evidence is the hook
    doc = None
    if cache_dir is not None:
        for sc in script["scenes"]:
            if sc["type"] == "evidence":
                doc = page_image(sc["evidence"], cache_dir)
                if doc is not None:
                    break
    text_w = W - int(120 * u)
    if doc is not None:
        pw, ph = int(W * 0.40), int(H * 0.86)
        crop = ken_burns(doc, pw, ph, 0, 1, "thumb")
        crop = crop.rotate(-3, expand=True, fillcolor=BG, resample=Image.BICUBIC)
        img.paste(crop, (W - crop.width + int(30 * u), (H - crop.height) // 2))
        text_w = int(W * 0.56)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, int(18 * u), H], fill=col)
    d.text((int(60 * u), int(52 * u)), f"CASE FILE Nº {t0['case_no']:04d}", font=font("sans_b", int(26 * u)), fill=GOLD)
    y = text_block(d, (int(60 * u), int(100 * u)), t0["name"], font("serif", int(96 * u)), text_w,
                   TEXT, spacing=1.0, max_lines=3)
    hook = t0["hook"]
    if t0["name"].lower() in hook.lower():          # don't say the name twice
        hook = t0["claim"]
    y = text_block(d, (int(60 * u), y + int(14 * u)), hook, font("serif_i", int(46 * u)), text_w,
                   GOLD, max_lines=3)
    from .episode import STATUS_LABEL
    badge(d, (int(60 * u), H - int(120 * u)), STATUS_LABEL[script["status"]], col, int(34 * u))
    return img

