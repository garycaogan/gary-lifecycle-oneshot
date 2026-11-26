"""The three caption kinds of this genre, drawn with PIL as full-frame transparent PNGs:
narration phrases (lower third), keyword titles (upper middle, pinyin above rare characters), poem lines (top, source
in songti below). Each kind owns its own zone, so nothing depends on timing to stay apart, and `check` proves it:
at every moment the boxes of the captions on screen must not intersect and must stay inside the platform safe area.
Fonts are open-source (SIL OFL 1.1) files in ../fonts (fonts/fonts.json; doctor --fetch-fonts downloads the big ones).

build(project, style) → <project>/gen/caps_<style>/*.png + events.json"""
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

import config

W, H = 1080, 1920
# Safe area: 110 px each side; nothing below y=1520 (Douyin / Channels put the account name, caption and buttons there)
# or above y=160 (status bar and top tabs).
SAFE = (110, 160, W - 110, 1520)
POEM_Y = (330, 445)          # centres of poem line 1 / line 2; the source sits 95 px under the last line
TITLE_Y = 820                # centre of the keyword title; pinyin above it
SUB_Y = 1380                 # centre of the narration phrases
BAND = (1250, 1530)          # faint dark gradient behind the narration zone, on every frame
TITLE_HOLD = 1.5             # 叫法: how long a title holds the narration slot (s)

# Every style draws solid opaque dark strokes plus a soft shadow: the white type must stay readable on sunlit leaves,
# white walls and sky (the first cut of the bamboo film used a 2 px half-transparent stroke and was unreadable there).
STYLES = {
    # 墨韵 (default): brush titles in warm white, poems in LXGW WenKai, bold sans narration
    "moyun": {"title": ("MaShanZheng-Regular.ttf", None), "poem": ("LXGWWenKai-Medium.ttf", None),
              "sub": ("NotoSansSC-VF.ttf", 700), "src": ("NotoSerifSC-VF.ttf", 700),
              "title_fill": (255, 248, 231), "poem_fill": (255, 248, 231), "sub_fill": (255, 255, 255),
              "src_fill": (240, 232, 210), "stroke": (24, 18, 12), "title_px": {1: 260, 2: 215, 3: 190, 4: 170},
              "title_stroke": 9, "poem_px": 92, "poem_stroke": 6, "sub_px": 64, "sub_stroke": 6},
    # 金书: running-script titles in pale gold, poems in heavy songti, black-weight sans narration
    "jinshu": {"title": ("ZhiMangXing-Regular.ttf", None), "poem": ("NotoSerifSC-VF.ttf", 900),
               "sub": ("NotoSansSC-VF.ttf", 900), "src": ("NotoSerifSC-VF.ttf", 700),
               "title_fill": (248, 228, 160), "poem_fill": (255, 250, 238), "sub_fill": (255, 255, 255),
               "src_fill": (248, 228, 160), "stroke": (26, 18, 6), "title_px": {1: 280, 2: 230, 3: 200, 4: 180},
               "title_stroke": 9, "poem_px": 84, "poem_stroke": 6, "sub_px": 62, "sub_stroke": 6},
    # 宋韵: heavy songti titles, WenKai poems, warm-tinted sans narration
    "songyun": {"title": ("NotoSerifSC-VF.ttf", 900), "poem": ("LXGWWenKai-Medium.ttf", None),
                "sub": ("NotoSansSC-VF.ttf", 700), "src": ("NotoSerifSC-VF.ttf", 600),
                "title_fill": (255, 255, 255), "poem_fill": (255, 246, 214), "sub_fill": (255, 246, 214),
                "src_fill": (255, 246, 214), "stroke": (20, 20, 20), "title_px": {1: 230, 2: 190, 3: 170, 4: 150},
                "title_stroke": 8, "poem_px": 90, "poem_stroke": 6, "sub_px": 66, "sub_stroke": 6},
    # 叫法 (the look of the 1M-like 《水的叫法》 short): extra-black slanted sans narration in white with a hard drop shadow, no stroke, no
    # band; single-word heavy songti titles at the same height that replace the narration while they show, fading in
    # with a warm glow and a thin rule under them. Poems (if any) keep the top zone.
    "jiaofa": {"title": ("NotoSerifSC-VF.ttf", 900), "poem": ("NotoSerifSC-VF.ttf", 900),
               "sub": ("NotoSansSC-VF.ttf", 900), "src": ("NotoSerifSC-VF.ttf", 700),
               "title_fill": (255, 255, 255), "poem_fill": (255, 255, 255), "sub_fill": (255, 255, 255),
               "src_fill": (255, 255, 255), "stroke": (20, 20, 20), "title_px": {1: 180, 2: 160, 3: 140, 4: 125},
               "title_stroke": 0, "poem_px": 84, "poem_stroke": 5, "sub_px": 64, "sub_stroke": 0,
               "layout": "jiaofa", "sub_y": 1182, "title_y": 1188, "skew": 0.2, "band": False},
}


def font(spec, size):
    name, weight = spec
    p = config.font_path(name)
    if not p.is_file():
        raise SystemExit(f"缺字体 {name}：先跑 doctor --fetch-fonts")
    f = ImageFont.truetype(str(p), size)
    if weight:
        f.set_variation_by_axes([weight])
    return f


class Timing:
    """Character times of the narration (voice/chars.json) shifted by the film's lead-in."""

    def __init__(self, pj):
        self.chars = json.load(open(pj.p("voice/chars.json"), encoding="utf-8"))
        self.lines = json.load(open(pj.p("voice/lines.json"), encoding="utf-8"))
        self.lead = pj.lead

    def at(self, li, text, nth=0):
        """(start, end) in the film of `text` inside script line li (punctuation ignored)."""
        cs = [c for c in self.chars if c["line"] == li]
        s = "".join(c["c"] for c in cs)
        want = re.sub(r"[^\w]", "", text)
        i = -1
        for _ in range(nth + 1):
            i = s.find(want, i + 1)
            if i < 0:
                raise SystemExit(f"字幕：第 {li} 句里找不到「{text}」（line 从 0 数，照稿子原文写）")
        return cs[i]["s"] + self.lead, cs[i + len(want) - 1]["e"] + self.lead


class Layer:
    """Text items drawn with an opaque stroke over a soft drop shadow (or, with `hard`, a crisp offset shadow and no
    stroke; `skew` slants the whole layer like an italic around its own baseline; `glow` adds a warm halo and `rule` a
    thin line under the text, both scaled by `glow`, and `alpha` fades the whole layer)."""

    def __init__(self, st, hard=False, skew=0.0, glow=0.0, rule=None, alpha=1.0, shadow=1):
        self.st, self.items = st, []
        self.hard, self.skew, self.glow, self.rule, self.alpha, self.shadow = hard, skew, glow, rule, alpha, shadow

    def text(self, centre, s, f, fill, stroke, spacing=0):
        d = ImageDraw.Draw(Image.new("L", (1, 1)))
        total = (sum(d.textlength(ch, font=f) for ch in s) + spacing * (len(s) - 1)) if spacing else d.textlength(s, font=f)
        self.items.append((centre[0] - total / 2, centre[1], s, f, fill, stroke, spacing))

    def render(self):
        txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dt, ds = ImageDraw.Draw(txt), ImageDraw.Draw(sh)
        for x0, y, s, f, fill, stroke, spacing in self.items:
            pos, x = [], x0
            for ch in (s if spacing else [s]):
                pos.append((x, ch))
                x += dt.textlength(ch, font=f) + spacing
            for x, t in pos:
                sw = stroke if self.hard else stroke + 2
                ds.text((x, y), t, font=f, fill=(0, 0, 0, 200), anchor="lm", stroke_width=sw, stroke_fill=(0, 0, 0, 200))
                dt.text((x, y), t, font=f, fill=fill + (255,), anchor="lm", stroke_width=stroke, stroke_fill=self.st["stroke"] + (255,))
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if self.hard:
            a = sh.getchannel("A").point(lambda v: v * 170 // 200)
            hard = Image.new("RGBA", (W, H), (10, 10, 10, 0))
            hard.putalpha(a)
            out.alpha_composite(hard, (4, 4))
        else:
            soft = sh.filter(ImageFilter.GaussianBlur(7))
            for _ in range(self.shadow):   # 叫法 titles: twice, so white type holds on white water and sky
                out.alpha_composite(soft, (3, 5))
        if self.glow > 0:
            halo = Image.new("RGBA", (W, H), (255, 226, 170, 0))
            halo.putalpha(txt.getchannel("A").filter(ImageFilter.GaussianBlur(16)).point(lambda v: min(255, int(v * 1.6 * self.glow))))
            out.alpha_composite(halo)
            if self.rule:
                cx, y, half = self.rule
                line = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ld = ImageDraw.Draw(line)
                for x in range(int(cx - half), int(cx + half)):
                    u = 1 - abs(x - cx) / half
                    ld.line([(x, y - 1), (x, y + 1)], fill=(255, 240, 210, int(235 * min(1, u * 2.5) * self.glow)))
                out.alpha_composite(line.filter(ImageFilter.GaussianBlur(0.8)))
        out.alpha_composite(txt)
        if self.skew:
            yc = self.items[0][1] if self.items else H / 2
            out = out.transform((W, H), Image.AFFINE, (1, self.skew, -self.skew * yc, 0, 1, 0), resample=Image.BICUBIC)
        if self.alpha < 1:
            out.putalpha(out.getchannel("A").point(lambda v: int(v * self.alpha)))
        bbox = out.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
        return out, bbox


def build(pj, style=None):
    cap = pj.film.get("captions") or {}
    style = style or cap.get("style", "moyun")
    if style not in STYLES:
        raise SystemExit(f"没有这套字幕样式：{style}（可选 {', '.join(STYLES)}）")
    st = STYLES[style]
    tm = Timing(pj)
    end = pj.end()
    out_dir = pj.p(f"gen/caps_{style}/x").parent
    for f in out_dir.glob("*.png"):
        f.unlink()
    titles = cap.get("titles", [])
    poems = cap.get("poems", [])
    ev = []

    def save(layer, name, t0, t1, kind, text):
        img, bbox = layer.render()
        p = out_dir / f"{name}.png"
        img.save(p)
        ev.append({"png": str(p.relative_to(pj.dir)), "t0": round(t0, 3), "t1": round(t1, 3), "kind": kind,
                   "text": text, "box": list(bbox) if bbox else None})

    # 1. narration phrases, split at the script's punctuation; phrases shown as poems are left out
    poem_phr = {re.sub(r"[^\w]", "", p) for po in poems for p in po["lines"]}
    phrases = []
    for L in tm.lines:
        for ph in [p for p in re.split(r"[，。、；：！？,.;:!?]", L["text"]) if p.strip()]:
            if re.sub(r"[^\w]", "", ph) in poem_phr:
                continue
            s, e = tm.at(L["i"], ph)
            phrases.append([ph.strip(), s, e])
    meas = ImageDraw.Draw(Image.new("L", (1, 1)))
    jiaofa = st.get("layout") == "jiaofa"
    sub_y, title_y = st.get("sub_y", SUB_Y), st.get("title_y", TITLE_Y)
    poem_starts = [tm.at(po["line"], po["lines"][0])[0] - 0.15 for po in poems]
    seen, starts = {}, []
    for t in titles:
        n = seen.get((t["line"], t.get("anchor") or t["text"]), 0)
        seen[(t["line"], t.get("anchor") or t["text"])] = n + 1
        starts.append(tm.at(t["line"], t.get("anchor") or t["text"], n)[0])
    # 叫法: a title owns the narration slot from its start to TITLE_HOLD later (or the next title); narration around it
    # is cut back (ends when the title comes in, or starts when it leaves) and dropped if too little of it is left.
    windows = []
    if jiaofa:
        for k, s0 in enumerate(starts):
            nxt = starts[k + 1] - 0.1 if k + 1 < len(starts) else end
            windows.append((s0 - 0.1, min(s0 + TITLE_HOLD, nxt, end)))
    for k, (ph, s, e) in enumerate(phrases):
        nxt = phrases[k + 1][1] - 0.05 if k + 1 < len(phrases) else end   # = the next phrase's t0
        t0, t1 = s - 0.05, (nxt if jiaofa else min(nxt, e + 0.5))
        for w0, w1 in windows:
            if t0 < w1 and w0 < t1:
                if t0 >= w0:
                    t0 = w1
                else:
                    t1 = min(t1, w0)
        if t1 - t0 < 0.3:
            continue
        size = st["sub_px"]
        while meas.textlength(ph, font=font(st["sub"], size)) > SAFE[2] - SAFE[0] - 40 - 2 * st["sub_stroke"]:
            size -= 2
        lay = Layer(st, hard=jiaofa, skew=st.get("skew", 0))
        lay.text((W / 2, sub_y), ph, font(st["sub"], size), st["sub_fill"], st["sub_stroke"])
        save(lay, f"sub{k:03d}", t0, t1, "sub", ph)

    # 2. keyword titles; a title leaves before the next title or a poem comes in
    for k, t in enumerate(titles):
        s = starts[k]
        nxt = [x for x in starts[k + 1:k + 2]]
        if jiaofa:
            word = t["text"]
            size = st["title_px"].get(len(word), max(110, 680 // max(1, len(word))))
            f = font(st["title"], size)
            w0, w1 = windows[k]
            # fade in over 0.4 s with the glow and the rule, hold the glow 0.3 s, let it go in 0.2 s, then plain
            states = [(0.2, 1.0), (0.4, 1.0), (0.6, 1.0), (0.8, 1.0), (1.0, 1.0), (1.0, 1.0), (1.0, 0.55), (1.0, 0.0)]
            steps = [0.08] * 5 + [0.3, 0.1, None]
            t0 = w0
            for j, ((alpha, glow), dt) in enumerate(zip(states, steps)):
                t1 = w1 if dt is None else min(w1, t0 + dt)
                if t1 - t0 < 1e-3:
                    break
                lay = Layer(st, glow=glow, rule=(W / 2, title_y + size * 0.62, 150), alpha=alpha, shadow=2)
                lay.text((W / 2, title_y), word, f, st["title_fill"], st["title_stroke"])
                save(lay, f"title{k:02d}_{j}", t0, t1, "title", word)
                t0 = t1
            continue
        t1 = min([s + 2.6, (nxt[0] - 0.1) if nxt else 1e9] + [p - 0.1 for p in poem_starts if p > s])
        word = t["text"]
        size = st["title_px"].get(len(word), max(110, 680 // max(1, len(word))))
        f = font(st["title"], size)
        while meas.textlength(word, font=f) > SAFE[2] - SAFE[0] - 40:
            size -= 6
            f = font(st["title"], size)
        lay = Layer(st)
        lay.text((W / 2, title_y), word, f, st["title_fill"], st["title_stroke"])
        if t.get("pinyin"):
            lay.text((W / 2, title_y - size * 0.5 - 60), t["pinyin"], font(st["sub"], 60), st["title_fill"], 5, spacing=6)
        save(lay, f"title{k:02d}", s - 0.1, t1, "title", word)

    # 3. poems: line 1 appears as it is read, line 2 joins it, the source sits under the last line
    pf, sf = font(st["poem"], st["poem_px"]), font(st["src"], 40)
    for k, po in enumerate(poems):
        ps, li = po["lines"], po["line"]
        if len(ps) > 2:
            raise SystemExit(f"古诗一次最多两句（第 {li} 句那首有 {len(ps)} 句）：拆成两条")
        starts_p = [tm.at(li, p)[0] for p in ps]
        endp = tm.lines[li]["e"] + tm.lead + 0.9
        if k + 1 < len(poems) and poems[k + 1]["line"] == li + 1:
            endp = min(endp, tm.at(poems[k + 1]["line"], poems[k + 1]["lines"][0])[0] - 0.6)
        for j in range(len(ps)):
            lay = Layer(st)
            for m, p in enumerate(ps[:j + 1]):
                lay.text((W / 2, POEM_Y[m]), p, pf, st["poem_fill"], st["poem_stroke"])
            lay.text((W / 2, POEM_Y[len(ps) - 1] + 95), po["source"], sf, st["src_fill"], 4)
            t1 = starts_p[j + 1] - 0.15 if j + 1 < len(ps) else endp   # = the next state's t0
            save(lay, f"poem{k}_{j}", starts_p[j] - 0.15, t1, "poem", " / ".join(ps[:j + 1]))

    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    for y in range(*BAND) if st.get("band", True) else ():
        u = (y - BAND[0]) / (BAND[1] - BAND[0])
        bd.line([(0, y), (W, y)], fill=(0, 0, 0, int(95 * (1 - abs(2 * u - 1)) ** 0.8)))
    band.save(out_dir / "band.png")
    data = {"style": style, "end": end, "safe": SAFE, "band": str((out_dir / "band.png").relative_to(pj.dir)),
            "events": sorted(ev, key=lambda x: x["t0"])}
    json.dump(data, open(out_dir / "events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    return out_dir


def check(out_dir):
    """At every moment the boxes of the captions shown together must not intersect and must stay inside SAFE."""
    data = json.load(open(Path(out_dir) / "events.json", encoding="utf-8"))
    ev, (sx0, sy0, sx1, sy1) = data["events"], data["safe"]
    outside = [(e["kind"], e["text"], e["box"]) for e in ev
               if e["box"] and (e["box"][0] < sx0 or e["box"][1] < sy0 or e["box"][2] > sx1 or e["box"][3] > sy1)]
    overlaps = []
    for i, a in enumerate(ev):
        for b in ev[i + 1:]:
            if a["t0"] < b["t1"] and b["t0"] < a["t1"] and a["box"] and b["box"]:
                ax0, ay0, ax1, ay1 = a["box"]
                bx0, by0, bx1, by1 = b["box"]
                if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                    overlaps.append((a["text"], b["text"], round(max(a["t0"], b["t0"]), 2)))
    zones = {k: [min(e["box"][1] for e in ev if e["kind"] == k), max(e["box"][3] for e in ev if e["kind"] == k)]
             for k in ("poem", "title", "sub") if any(e["kind"] == k for e in ev)}
    return {"style": data["style"], "events": len(ev), "overlaps": overlaps, "outside_safe": outside, "zones_y": zones}
