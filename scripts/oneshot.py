"""一镜到底：万物的一生 — a Chinese vertical science short in which one plant (or animal) lives its whole life cycle in
one continuous shot. One project folder per film; its film.json is the only source of truth. Steps, in order:

    oneshot.py doctor [--fetch-fonts] [--fetch-model]  # Python, packages, ffmpeg, fonts, keys; downloads fonts / the transcription model
    oneshot.py write    --project P              # the agent writes film.json itself (no API); prints the checklist
    oneshot.py voice    --project P [--takes 1] [--use rec.wav]   # Doubao reads the whole script (or align your own
                                                 # recording) → voice/voice.wav + chars.json + lines.json
    oneshot.py tone     --project P --chars 茎 鞭 # pitch contour of those characters in the narration (tone check)
    oneshot.py fixline  --project P --line 1 [--speak 茎=经]   # re-read ONE sentence and splice it in
    oneshot.py keyframes --project P [--id K03] [--redo]       # GPT Image 2.5 keyframe chain → frames/
    oneshot.py plan     --project P              # segment times from the narration → gen/plan.json
    oneshot.py h3       --project P [--seg A] [--redo] [--dry-run]   # H3 first+last-frame clips → clips/
    oneshot.py music    --project P              # Suno v6 instrumental → music/bed.mp3
    oneshot.py captions --project P [--style moyun|jinshu|songyun]   # caption layers + overlap / safe-area check
    oneshot.py cut      --project P [--style S] [--caps-only]        # join, ease, cross-fade, captions, mix → out/
    oneshot.py check    --project P [--no-gemini]  # keyframe SSIM, seams, motion, contact sheets, Gemini review
    oneshot.py cost     --project P

Keys (Evolink, RunningHub, APIMart) come from the environment or the skill's .env (see config.py), never printed.
Every paid call lands in <project>/gen/ledger.jsonl."""
import argparse
import difflib
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
from config import FF, SKILL, UA, dur, ff

FPS = 30
CANVAS = (928, 1664)        # what H3 renders for 9:16 at 1.5 MP; keyframes are centre-cropped to it first
LOOK = ("Photorealistic nature documentary macro photograph, shot on a full-frame camera with a macro lens, natural soft "
        "daylight, rich true-to-life colours, crisp detail, shallow depth of field, no people, no animals unless named, "
        "no text, no watermark, no borders. Vertical 9:16 frame.")
POLYPHONES = set("茎长还行重着为便曾种落角处数朝冲背调差乐省宿曲率似结薄露藏冠降斗强传参单奇教尽圈称当难应间更")


# ------------------------------------------------------------------ project

class Project:
    def __init__(self, path):
        self.dir = Path(path).resolve()
        f = self.dir / "film.json"
        if not f.is_file():
            raise SystemExit(f"{f} 不存在：先照 references/film-format.md 写一份（范例 examples/bamboo/film.json）")
        self.film = json.loads(f.read_text(encoding="utf-8"))
        self.lead = float(self.film.get("lead", 0.5))
        self.tail = float(self.film.get("tail", 2.6))
        os.environ.setdefault("EVOLINK_LEDGER", str(self.dir / "gen/ledger.jsonl"))
        os.environ.setdefault("EVOLINK_BUDGET_CNY", str(self.film.get("budget_cny", 40)))
        (self.dir / "gen").mkdir(exist_ok=True)

    def p(self, *parts):
        out = self.dir.joinpath(*parts)
        out.parent.mkdir(parents=True, exist_ok=True)
        return out

    def ledger(self, **entry):
        with open(self.p("gen/ledger.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}, ensure_ascii=False) + "\n")

    def lines(self):
        return json.load(open(self.p("voice/lines.json"), encoding="utf-8"))

    def end(self):
        return round(self.lines()[-1]["e"] + self.lead + self.tail, 3)

    def plan(self):
        f = self.p("gen/plan.json")
        if not f.exists():
            raise SystemExit("还没有 gen/plan.json：先跑 voice 再跑 plan")
        return json.load(open(f, encoding="utf-8"))


def evo():
    import evolink
    return evolink


def rh():
    import runninghub
    return runninghub


def pmap(fn, items, workers=6):
    with ThreadPoolExecutor(max(1, workers)) as ex:
        return list(ex.map(fn, items))


def norm(s):
    return re.sub(r"[^\w]", "", s)


def frame_rgb(src, t, w=1080, h=1920):
    from PIL import Image
    raw = subprocess.run([FF, "-loglevel", "error", "-ss", f"{max(0, t):.3f}", "-i", str(src), "-frames:v", "1", "-vf",
                          f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return Image.frombytes("RGB", (w, h), raw)


def gray_frames(src, w=144, h=256):
    import numpy as np
    raw = subprocess.run([FF, "-loglevel", "error", "-i", str(src), "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)


def contact(files, labels, out, cols=9, w=220):
    from PIL import Image, ImageDraw, ImageFont
    files = [(f, l) for f, l in zip(files, labels) if Path(f).exists()]
    if not files:
        return
    h = int(w * 16 / 9)
    sheet = Image.new("RGB", (w * min(cols, len(files)), h * ((len(files) + cols - 1) // cols)), "black")
    try:
        font = ImageFont.truetype(str(config.font_path("NotoSansSC-VF.ttf")), 20)
    except OSError:
        font = ImageFont.load_default()
    for i, (f, lab) in enumerate(files):
        im = Image.open(f).convert("RGB").resize((w, h))
        ImageDraw.Draw(im).text((6, 4), lab, font=font, fill="yellow", stroke_width=2, stroke_fill="black")
        sheet.paste(im, ((i % cols) * w, (i // cols) * h))
    sheet.save(out, quality=85)


# ------------------------------------------------------------------ write

def cmd_write(pj, a):
    """No API: the agent running this skill writes film.json itself (references/method.md has the recipe)."""
    lines = pj.film.get("script") or []
    print("写稿由跑这个 skill 的 Agent 自己写，不调 API。照 references/method.md 的套路写 film.json，再按下面的清单自查：")
    print("  1. 第一句是反常识的钩子；一句一个阶段，名词「叫做 X」点出来；隔几句插一句古诗，出处核准；最后一句回到开头。")
    print("  2. 全稿 300–380 字（3.2 字/秒约 95–120 秒）；每个阶段在 segments 里有一段，段长 5–13 秒。")
    print("  3. 配音前先过一遍多音字和生僻字（下面列出来的），能换说法就换，换不了在 voice.speak 里写替读字。")
    text = "".join(lines)
    risky = sorted({c for c in text if c in POLYPHONES})
    rare = sorted({c for c in text if re.match(r"[一-鿿]", c) and not _common(c)})
    print("  多音字：", "、".join(risky) or "无")
    print("  生僻字：", "、".join(rare) or "无", "（这些字豆包和 Whisper 都容易出错，标题里给它们加 pinyin）")


def _common(c):
    """In GB2312 level 1 (the 3,755 most common characters)?"""
    try:
        b = c.encode("gb2312")
    except UnicodeEncodeError:
        return False
    return len(b) == 2 and 0xB0 <= b[0] <= 0xD7


# ------------------------------------------------------------------ voice

def voice_body(pj, text):
    v = pj.film["voice"]
    for k, val in (v.get("speak") or {}).items():
        text = text.replace(k, val)
    return {"model": v.get("model", "doubao-seed-audio-1-0"), "format": "wav", "sample_rate": 48000,
            "speech_rate": v.get("speech_rate", 1.07), "prompt": v["direction"].rstrip() + v.get("says", "她说：") + text}


def transcribe(pj, wav):
    out = Path(wav).with_suffix(".asr.json")
    if not out.exists():
        hint = pj.film["voice"].get("hint") or "以下是普通话的句子。"
        json.dump(config.whisper_zh(wav, hint), open(out, "w", encoding="utf-8"), ensure_ascii=False)
    return json.load(open(out, encoding="utf-8"))


def align(pj, wav):
    """Script characters onto the heard character times (a Whisper word's span split evenly over its characters).
    Captions keep the script's characters; Whisper's homophones (唾 for 箨) only lend their times."""
    tx = transcribe(pj, wav)
    heard = []
    for s in tx["segments"]:
        for w in s.get("words", []):
            t = norm(w["word"])
            if t:
                d = (w["end"] - w["start"]) / len(t)
                heard += [(c, w["start"] + i * d, w["start"] + (i + 1) * d) for i, c in enumerate(t)]
    lines = [l.strip() for l in pj.film["script"] if l.strip()]
    chars = [(li, c) for li, l in enumerate(lines) for c in l if re.match(r"\w", c)]
    sm = difflib.SequenceMatcher(a=[c for _, c in chars], b=[h[0] for h in heard], autojunk=False)
    times = [None] * len(chars)
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op in ("equal", "replace"):
            for k in range(min(a2 - a1, b2 - b1)):
                times[a1 + k] = heard[b1 + k][1:]
    for i in range(len(times)):
        if times[i] is None:
            prev = times[i - 1] if i else (0, 0)
            times[i] = (prev[1], prev[1] + 0.2)
    out = [{"line": li, "c": c, "s": round(t[0], 3), "e": round(t[1], 3)} for (li, c), t in zip(chars, times)]
    L = []
    for li, l in enumerate(lines):
        cs = [x for x in out if x["line"] == li]
        L.append({"i": li, "text": l, "s": cs[0]["s"], "e": cs[-1]["e"]})
    end = heard[-1][2] if heard else 0
    return out, L, round(sm.ratio(), 3), end


def finish_voice(pj, raw):
    """raw take → voice/current.wav (kept for splicing), voice/voice.wav (−16 LUFS), chars.json, lines.json."""
    chars, lines, ratio, end = align(pj, raw)
    cur = pj.p("voice/current.wav")
    if Path(raw).resolve() != cur.resolve():
        ff("-i", raw, "-c:a", "pcm_s16le", "-ar", 48000, cur)
        asr = Path(raw).with_suffix(".asr.json")
        if asr.exists():
            cur.with_suffix(".asr.json").write_text(asr.read_text(encoding="utf-8"), encoding="utf-8")
    d = dur(cur)
    ff("-i", cur, "-af", f"afade=t=out:st={d - 0.05:.3f}:d=0.05,apad=pad_dur=0.3,loudnorm=I=-16:TP=-1.5:LRA=9",
       "-ar", 48000, pj.p("voice/voice.wav"))
    json.dump(chars, open(pj.p("voice/chars.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(lines, open(pj.p("voice/lines.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    return ratio, end


def fit_take(pj, f, cps):
    """voice.fit_cps: a take read too fast or too slow is time-stretched (pitch kept, ffmpeg atempo) to the middle of
    target_cps instead of paying for another take. Doubao's speech_rate is no dial: the same 0.66 read the 水 script in
    93 s with one wording of the direction and in 140 s with another. Only within 0.7–1.45×; beyond that, read again."""
    lo, hi = pj.film["voice"].get("target_cps", [3.0, 3.5])
    if not pj.film["voice"].get("fit_cps") or lo <= cps <= hi:
        return f
    factor = round((lo + hi) / 2 / cps, 3)
    if not 0.7 <= factor <= 1.45:
        print(f"语速 {cps} 字/秒离目标太远（要拉 {factor} 倍），不硬拉：调 speech_rate 再读一条")
        return f
    out = Path(f).with_name(Path(f).stem + "_fit.wav")
    ff("-i", f, "-af", f"atempo={factor}", "-ar", 48000, out)
    print(f"{Path(f).name} 语速 {cps} 字/秒 → 按 {factor} 倍变速（音高不变）到约 {round(cps * factor, 2)} 字/秒：{out.name}")
    return out


def cmd_voice(pj, a):
    """Doubao Seed Audio reads the whole script in one call (the read only flows when it is one call). It bills by the
    length of the audio: a 110 s take is about ¥2.4. One take is usually enough; a misread sentence is fixed with
    `fixline`, not by reading the whole thing again."""
    if a.use:   # an existing recording (your own read, or a take from before): align it, no API call
        src = Path(a.use).resolve()
        if pj.film["voice"].get("fit_cps"):
            _, _, _, e0 = align(pj, src)
            src = fit_take(pj, src, round(len(norm("".join(pj.film["script"]))) / max(e0, 1), 2))
        ratio, end = finish_voice(pj, src)
        print("用现成录音", a.use, "对稿", ratio, "→ voice/voice.wav", round(dur(pj.p("voice/voice.wav")), 1), "秒")
        return
    cmd_write(pj, argparse.Namespace())   # prints the polyphone / rare-character list once more before paying
    e = evo()
    text = "".join(l.strip() for l in pj.film["script"])
    body = voice_body(pj, text)
    d = pj.p("voice/x").parent
    n0 = len(list(d.glob("take*.wav")))

    def one(i):
        st = e.wait(e.submit_audio(body), every=4, limit=900)
        if st["state"] != "done":
            return None
        f = d / f"take{i}.wav"
        e.download(st["url"], f)
        return f
    takes = [f for f in pmap(one, range(n0 + 1, n0 + 1 + a.takes), a.takes) if f]
    if not takes:
        raise SystemExit("配音没出来（Evolink 报错见上面）；稿子超过 400 字容易报 invalid_parameters")
    lo, hi = pj.film["voice"].get("target_cps", [3.0, 3.5])
    rows = []
    for f in takes:
        _, _, ratio, end = align(pj, f)
        cps = len(norm(text)) / max(end, 1)
        rows.append((ratio - (0 if lo <= cps <= hi else 0.2), f, ratio, round(end, 1), round(cps, 2)))
        print(f.name, "对稿", ratio, "时长", round(end, 1), "秒", "语速", round(cps, 2), "字/秒")
    rows.sort(key=lambda r: -r[0])
    best = rows[0]
    fitted = fit_take(pj, best[1], best[4])
    if fitted != best[1]:
        _, _, r2, e2 = align(pj, fitted)
        best = (best[0], fitted, r2, round(e2, 1), round(len(norm(text)) / max(e2, 1), 2))
    ratio, end = finish_voice(pj, best[1])
    print("用", best[1].name, "→ voice/voice.wav", round(dur(pj.p("voice/voice.wav")), 1), "秒")
    if not lo <= best[4] <= hi:
        rate = pj.film["voice"].get("speech_rate", 1.07)
        want = round(rate * (lo + hi) / 2 / best[4], 2)
        print(f"语速 {best[4]} 字/秒不在 {lo}–{hi}：把 voice.speech_rate 从 {rate} 改成 {want} 左右再出一条（字速大致和它成正比，"
              "但同一个数在不同的表演要求下快慢差很多：竹子那条温柔女声 1.07 约 3.2 字/秒，水那条男孩声 1.15 读到了 5.9 字/秒、0.62 是 3.5）")
    print("Whisper 对稿率低于 0.9 多半是同音字（唾/箨、黄/篁），逐句看 voice/lines.json；可疑的字用 tone 量声调，读错的句子用 fixline 重读。")


def pitch(samples, t0, t1):
    """Pitch track (Hz, 20 ms steps) by autocorrelation; voiced frames only."""
    import numpy as np
    out = []
    for t in np.arange(t0, t1, 0.02):
        x = samples[int(t * 16000):int(t * 16000) + 640]
        if len(x) < 640 or np.sqrt((x ** 2).mean()) < 0.01:
            continue
        x = x - x.mean()
        c = np.correlate(x, x, "full")[639:]
        k = 40 + int(np.argmax(c[40:200]))
        if c[k] > 0.3 * c[0]:
            out.append(round(16000 / k))
    return out


def cmd_tone(pj, a):
    """Whisper only gives characters, not tones. A first-tone character reads level; a fourth-tone one falls ~8+
    semitones. On the bamboo film 茎 (jīng) fell 242→154 Hz (−7.8 st) while 鞭 and 根 at the same phrase end stayed level:
    Doubao read it jìng, and so did 经 / 京 / 晶 put in its place — the sentence had to be reworded."""
    import math
    samples = config.audio_16k(pj.p("voice/current.wav"))
    chars = json.load(open(pj.p("voice/chars.json"), encoding="utf-8"))
    for c in chars:
        if c["c"] in a.chars:
            p = pitch(samples, c["s"], c["e"] + 0.05)
            # the first frames often still carry the end of the character before, so compare the highest point of
            # the first half with the median of the last three frames
            drop = round(12 * math.log2(sorted(p[-3:])[1] / max(p[:max(2, len(p) // 2)])), 1) if len(p) >= 4 else None
            print(f"第 {c['line']} 句 {c['c']} {c['s']:.2f}s 音高 {p}  头到尾 {drop} 个半音")
    print("平调（一声）头尾差在 ±3 个半音内；降 6 个半音以上像四声。拿同一句里确定是一声的字对照着看。")


def cmd_fixline(pj, a):
    """Re-read one sentence and splice it into voice/current.wav: Doubao reads just that sentence (a few cents), it is
    cut between the pauses around it, stretched to the old length (atempo, so nothing after it moves) and joined with
    30 ms cross-fades. Much cheaper than a new whole take and the rest of the read stays as it was."""
    e = evo()
    lines = pj.lines()
    i = a.line
    if not 0 <= i < len(lines):
        raise SystemExit(f"--line 从 0 数，0–{len(lines) - 1}")
    v = pj.film["voice"]
    if a.speak:
        v.setdefault("speak", {}).update(dict(x.split("=", 1) for x in a.speak))
    text = pj.film["script"][i].strip()
    st = e.wait(e.submit_audio(voice_body(pj, text)), every=3, limit=600)
    if st["state"] != "done":
        raise SystemExit("重读没出来：" + str(st.get("error")))
    one = pj.p(f"voice/line{i}_{int(time.time())}.wav")
    e.download(st["url"], one)
    tx = config.whisper_zh(one, v.get("hint") or "以下是普通话的句子。")
    words = [w for s in tx["segments"] for w in s.get("words", [])]
    s_new = max(0.0, words[0]["start"] - 0.12) if words else 0.0
    e_new = min(dur(one), words[-1]["end"] + 0.2) if words else dur(one)
    a0 = (lines[i - 1]["e"] + lines[i]["s"]) / 2 if i else max(0.0, lines[i]["s"] - 0.12)
    b0 = (lines[i]["e"] + lines[i + 1]["s"]) / 2 if i + 1 < len(lines) else lines[i]["e"] + 0.25
    tempo = (e_new - s_new) / (b0 - a0)
    if not 0.5 <= tempo <= 2.0:
        raise SystemExit(f"新读的这句和原来长度差太多（{tempo:.2f} 倍），换个 speech_rate 再试")
    x = 0.03
    cur = pj.p("voice/current.wav")
    tmp = pj.p("voice/current_new.wav")
    fc = (f"[0:a]atrim=0:{a0 + x:.3f},asetpts=PTS-STARTPTS[p0];"
          f"[1:a]aresample=48000,atrim={s_new:.3f}:{e_new:.3f},asetpts=PTS-STARTPTS,atempo={tempo:.4f}[mid];"
          f"[0:a]atrim={b0 - x:.3f},asetpts=PTS-STARTPTS[p2];"
          f"[p0][mid]acrossfade=d={x}:c1=tri:c2=tri[pm];[pm][p2]acrossfade=d={x}:c1=tri:c2=tri[out]")
    ff("-i", cur, "-i", one, "-filter_complex", fc, "-map", "[out]", "-ar", 48000, "-c:a", "pcm_s16le", tmp)
    cur.replace(pj.p(f"voice/current_before_fix{i}_{int(time.time())}.wav"))
    tmp.replace(cur)
    cur.with_suffix(".asr.json").unlink(missing_ok=True)
    ratio, end = finish_voice(pj, cur)
    print(f"第 {i} 句已重读接回（拉伸 {tempo:.2f} 倍），对稿 {ratio}；用 tone 再量一下那个字，听一下接口")
    if abs(tempo - 1) > 0.12:
        print(f"注意：单独读的这句和原来快慢差了 {abs(tempo - 1) * 100:.0f}%，接回去被拉成原长，可能听着偏快或偏慢；"
              "不合适就再跑一次 fixline（豆包每次读的快慢不一样），或者在 film.json 调 voice.speech_rate 再试")


# ------------------------------------------------------------------ keyframes

def h3jpg(pj, kid):
    png = pj.p(f"frames/{kid}.png")
    out = png.with_name(kid + "_h3.jpg")
    W, H = CANVAS if pj.film.get("aspect", "9:16") == "9:16" else CANVAS[::-1]
    ff("-i", png, "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}", "-q:v", 2, out)
    return out


def cmd_keyframes(pj, a):
    """GPT Image 2.5 draws the keyframe chain: the first is drawn fresh, every later one as an edit of the one before
    ("same place, same light, only the plant grows one step"), with `camera` when the camera has moved. Neighbouring
    keyframes then differ by growth or a camera move, not by a new scene, which is what lets H3 join them in one take.
    Each keyframe is centre-cropped to H3's canvas (928×1664) so first and last frame are framed identically."""
    e = evo()
    look = pj.film.get("look", LOOK)
    spec = pj.film["keyframes"]
    want = set(a.id or [])
    todo = [k for k in spec if (k["id"] in want if want else True)
            and (a.redo or not pj.p(f"frames/{k['id']}.png").exists())]
    done = {k["id"] for k in spec if pj.p(f"frames/{k['id']}.png").exists()} - {k["id"] for k in todo}

    def one(k):
        if k.get("from"):
            refs = [e.upload(h3jpg(pj, k["from"]))] + [e.upload(h3jpg(pj, r)) for r in k.get("also", [])]
            if k.get("camera"):
                prompt = ("Image 1 is the previous moment of the same continuous shot. Keep the same place, the same "
                          "plants, characters and props, lighting, colour grade and visual style as image 1; the camera has "
                          "now moved: "
                          + k["camera"] + " What the new frame shows: " + k["change"] + " " + look)
            else:
                prompt = ("Edit image 1. Keep exactly the same camera position, lens, framing, lighting, background and "
                          "setting; this is the next moment of the same continuous shot. Change only this: "
                          + k["change"] + " " + look)
        else:
            refs = [e.upload(h3jpg(pj, r)) for r in k.get("also", [])]
            prompt = k["prompt"] + " " + look
        st = e.wait(e.submit_image(prompt, size=pj.film.get("aspect", "9:16"), refs=refs), every=5, limit=900)
        if st["state"] != "done":
            return k["id"], "没出来：" + str(st.get("error"))
        e.download(st["url"], pj.p(f"frames/{k['id']}.png"))
        h3jpg(pj, k["id"])
        return k["id"], "ok"
    while todo:   # edits wait for the frame they start from
        wave = [k for k in todo if all(r in done for r in [k.get("from")] + k.get("also", []) if r)]
        if not wave:
            raise SystemExit("关键帧链断了：" + ", ".join(k["id"] + "←" + str(k.get("from")) for k in todo))
        for r in pmap(one, wave, 6):
            print(*r, flush=True)
            if r[1] == "ok":
                done.add(r[0])
        todo = [k for k in todo if k not in wave]   # a failed frame leaves the ones built on it blocked (stop above)
    ids = [k["id"] for k in spec]
    contact([pj.p(f"frames/{i}_h3.jpg") for i in ids], ids, pj.p("frames/contact.jpg"), cols=8)
    print("关键帧总览 frames/contact.jpg：相邻两张要是「长了一步」或「镜头移了一下」，不能像换了场景")


# ------------------------------------------------------------------ plan

def cmd_plan(pj, a):
    """Segment i runs from the first characters of its `at` phrase (+ shift) to the next segment's start; the first
    starts at 0, the last ends `tail` seconds after the last word. H3 renders 5–15 s; 5–13 s works best."""
    chars = json.load(open(pj.p("voice/chars.json"), encoding="utf-8"))
    keys = [c["c"] for c in chars]
    pos, rows = 0, []
    for i, s in enumerate(pj.film["segments"]):
        if i == 0:
            t0 = 0.0
        else:
            at = norm(s["at"])
            hit = next((j for j in range(pos, len(keys)) if "".join(keys[j:j + len(at)]) == at), None)
            if hit is None:
                raise SystemExit(f"段 {s['id']} 的 at「{s['at']}」在旁白里找不到（按顺序、照稿子原文写开头几个字）")
            t0 = round(chars[hit]["s"] + pj.lead + float(s.get("shift", 0)), 3)
            pos = hit + len(at)
        rows.append({"id": s["id"], "first": s["first"], "last": s["last"], "t0": t0})
    for i, r in enumerate(rows):
        r["t1"] = rows[i + 1]["t0"] if i + 1 < len(rows) else pj.end()
        r["span"] = round(r["t1"] - r["t0"], 3)
        r["seconds"] = round(max(5.0, min(15.0, r["span"])), 3)
    for r, nxt in zip(rows, rows[1:]):
        if r["last"] != nxt["first"]:
            raise SystemExit(f"段 {r['id']} 的尾帧 {r['last']} 和下一段 {nxt['id']} 的首帧 {nxt['first']} 不是同一张：接不上")
    json.dump({"end": pj.end(), "segments": rows}, open(pj.p("gen/plan.json"), "w", encoding="utf-8"), indent=1)
    for r in rows:
        flag = "" if 4.5 <= r["span"] <= 13.5 else ("  ← 短于 5 秒会按 5 秒出再加速" if r["span"] < 4.5 else "  ← 太长，拆成两段")
        print(f"{r['id']:3s} {r['first']}→{r['last']}  {r['t0']:7.2f}–{r['t1']:7.2f}  {r['span']:5.2f}s{flag}")
    print("片长", pj.end(), "秒")


# ------------------------------------------------------------------ h3

SHOT = "One single continuous photorealistic nature-documentary shot, no cuts."
LIGHT = "Natural soft daylight and a consistent true-to-life colour grade throughout; every change happens gradually and smoothly."


def h3_body(pj, seg):
    film = pj.film if pj else {}
    return ("integrated_multimodal_description: [Shot 1] " + film.get("shot", SHOT) + " " + seg["move"].strip() + " "
            + film.get("light", LIGHT) + "\n\noverall_soundscape: " + seg.get("sound", "Quiet natural "
            "ambience.").strip() + "\n\nnon_diegetic_music: N/A")


def cmd_h3(pj, a):
    """One H3 first+last-frame task per segment: first frame = its `first` keyframe, last frame = its `last` keyframe,
    so neighbouring segments meet on the same picture. Positive phrasing only (the workflow has no negative branch).
    Submitted task ids are kept in gen/h3_tasks.json: running the step again collects them instead of paying twice."""
    r = rh()
    plan = {s["id"]: s for s in pj.plan()["segments"]}
    want = set(a.seg or [])
    segs = [s for s in pj.film["segments"] if (s["id"] in want if want else True)]
    todo = [s for s in segs if a.dry_run or a.redo or not pj.p(f"clips/{s['id']}.mp4").exists()]
    missing = sorted({k for s in todo for k in (s["first"], s["last"]) if not pj.p(f"frames/{k}.png").exists()})
    if missing:
        raise SystemExit(f"这些关键帧还没有：{missing}，先跑 keyframes")
    aspect = pj.film.get("aspect", "9:16")
    if a.dry_run:
        for s in todo:
            text = r.fl_prompt(h3_body(pj, s), plan[s["id"]]["seconds"])
            pj.p(f"gen/prompts/{s['id']}.txt").write_text(text, encoding="utf-8")
            r.graph(text, "first.jpg", "last.jpg", plan[s["id"]]["seconds"], aspect, 1)
            n, eff = r.frames_for(plan[s["id"]]["seconds"])
            print(s["id"], f"{s['first']}→{s['last']}", f"{plan[s['id']]['span']:.2f}s → H3 {eff:.2f}s（{n} 帧）", f"gen/prompts/{s['id']}.txt")
        print("节点图检查通过；提示词在 gen/prompts/。没上传、没花钱。")
        return
    tasks_f = pj.p("gen/h3_tasks.json")
    tasks = json.load(open(tasks_f)) if tasks_f.exists() else {}

    def save_task(sid, task):
        tasks[sid] = task
        json.dump(tasks, open(tasks_f, "w"), indent=1)

    def collect(sid, task):
        while (st := r.poll(task))["state"] == "running":
            time.sleep(20)
        return st

    def one(s):
        sid, p = s["id"], plan[s["id"]]
        text = r.fl_prompt(h3_body(pj, s), p["seconds"])
        pj.p(f"gen/prompts/{sid}.txt").write_text(text, encoding="utf-8")
        if tasks.get(sid) and not a.redo:   # submitted before and never collected
            st = collect(sid, tasks[sid])
            if st["state"] == "done":
                r.download(st["url"], pj.p(f"clips/{sid}.mp4"))
                pj.ledger(kind="h3", seg=sid, task=tasks[sid], cost_cny=st.get("cost"), run_s=st.get("run_seconds"))
                save_task(sid, None)
                return sid, "ok（取回之前提交的）", st.get("cost")
        first, last = r.upload(h3jpg(pj, s["first"])), r.upload(h3jpg(pj, s["last"]))
        for attempt in range(1 + r.MACHINE_RETRIES):
            seed = random.randint(1, 2**31 - 1)
            g = r.graph(text, first, last, p["seconds"], aspect, seed)
            while True:
                try:
                    task = r.submit(g)
                    break
                except r.QueueFull:
                    time.sleep(30)
            save_task(sid, task)
            pj.ledger(kind="h3_submit", seg=sid, task=task, seed=seed)
            st = collect(sid, task)
            if st["state"] == "done":
                if a.redo and pj.p(f"clips/{sid}.mp4").exists():
                    pj.p(f"clips/{sid}.mp4").replace(pj.p(f"clips/{sid}_old{int(time.time())}.mp4"))
                r.download(st["url"], pj.p(f"clips/{sid}.mp4"))
                pj.ledger(kind="h3", seg=sid, task=task, cost_cny=st.get("cost"), run_s=st.get("run_seconds"),
                          seconds=p["seconds"], seed=seed)
                save_task(sid, None)
                return sid, "ok", st.get("cost")
            pj.ledger(kind="h3_failed", seg=sid, task=task, error=str(st.get("error"))[:200])
            save_task(sid, None)
            if not r.machine_fault(st.get("error")):
                return sid, "failed", st.get("error")
        return sid, "failed after machine retries"
    for res in pmap(one, todo, len(todo)):
        print(res, flush=True)
    print("每段首尾帧量一下：check 会算每段第一帧、最后一帧和关键帧的 SSIM")


# ------------------------------------------------------------------ music

def cmd_music(pj, a):
    m = pj.film.get("music") or {}
    if m.get("file"):
        print("用现成配乐", m["file"])
        return
    key = config.key("APIMART_API_KEY")

    def req(method, path, body=None):
        rq = urllib.request.Request("https://api.apimart.ai/v1" + path, method=method,
                                    data=None if body is None else json.dumps(body).encode(),
                                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": UA})
        return json.loads(urllib.request.urlopen(rq, timeout=60).read())
    total = pj.plan()["end"] if pj.p("gen/plan.json").exists() else 120
    body = {"model": "suno", "version": "v6", "custom": True, "instrumental": True, "title": pj.film.get("title", "bed")[:40],
            "prompt": "", "style": m.get("style", "quiet traditional Chinese instrumental, guzheng and soft piano, calm nature "
                                                   "documentary underscore, slow, peaceful, no drums"),
            "negative_tags": "vocals, singing, choir, drums", "duration": int(total + 15), "audio_format": "mp3"}
    tid = req("POST", "/music/generations", body)["data"][0]["task_id"]
    for _ in range(120):
        time.sleep(8)
        t = req("GET", f"/music/tasks/{tid}")["data"]
        if t.get("status") in ("completed", "failed"):
            break
    pj.ledger(kind="suno", task=tid, status=t.get("status"), cost_usd=t.get("cost"))
    songs = (t.get("result") or {}).get("music") or []
    if not songs:
        raise SystemExit("配乐没出来：" + json.dumps(t, ensure_ascii=False)[:300])
    for i, s in enumerate(songs):
        urllib.request.urlretrieve(s["audio_url"], pj.p(f"music/bed{i}.mp3"))
    pj.p("music/bed.mp3").write_bytes(pj.p("music/bed0.mp3").read_bytes())
    print("配乐 music/bed.mp3（另一首在 bed1.mp3，换的话复制过去）")


# ------------------------------------------------------------------ captions

def cmd_captions(pj, a):
    import captions
    out = captions.build(pj, a.style)
    res = captions.check(out)
    print(json.dumps(res, ensure_ascii=False))
    if res["overlaps"] or res["outside_safe"]:
        raise SystemExit("字幕有重叠或出了安全区（见上），改 film.json 的 captions 或 scripts/captions.py 的分区再跑")
    print(f"{out.relative_to(pj.dir)}：{res['events']} 条，0 重叠，0 出安全区")


# ------------------------------------------------------------------ cut

EASE_A, EASE_R = 2.0, 0.8   # ease-in: start at 1/(1+A) speed, back to normal over R seconds


def head_tail_motion(clip):
    import numpy as np
    fr = gray_frames(clip)
    d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
    return float(d[:6].mean()), float(d[-5:].mean())


def cmd_cut(pj, a):
    """Retime each clip onto its narration span, join the segments on their shared keyframes, lay the captions on and
    mix. Seams: the segment before a keyframe eases into it, but H3 often starts the next move at full speed on the
    first frame, which reads as a jolt; those segments get an ease-in (from a third of the speed back to normal over
    0.8 s) and a 10-frame cross-fade, the rest 6 frames. `ease_in` in film.json forces it on or off, otherwise the step
    decides from the clips' own motion."""
    from PIL import Image
    plan = pj.plan()
    segs = plan["segments"]
    film = {s["id"]: s for s in pj.film["segments"]}
    end = plan["end"]
    gen = pj.p("gen/asm/x").parent
    style = a.style or (pj.film.get("captions") or {}).get("style", "moyun")
    caps = pj.p(f"gen/caps_{style}/events.json")
    if not caps.exists():
        raise SystemExit(f"还没有 {style} 这套字幕：先跑 captions --style {style}")
    if not a.caps_only:
        motion = {s["id"]: head_tail_motion(pj.p(f"clips/{s['id']}.mp4")) for s in segs}
        ease, xfs = [], []
        for i, s in enumerate(segs):
            auto = i > 0 and motion[s["id"]][0] >= 3.0 and motion[s["id"]][0] >= 3 * motion[segs[i - 1]["id"]][1]
            e_on = film[s["id"]].get("ease_in", auto) if i else False
            ease.append(bool(e_on))
            xfs.append(0 if i == 0 else int(film[s["id"]].get("xf", 10 if e_on else 6)))
        half = [x / 2 / FPS for x in xfs] + [0.0]
        parts, rows = [], []
        for i, s in enumerate(segs):
            clip = pj.p(f"clips/{s['id']}.mp4")
            if not clip.exists():
                raise SystemExit(f"段 {s['id']} 还没有 H3 片段，先跑 h3")
            n = round((s["t1"] + half[i + 1] - (s["t0"] - half[i])) * FPS)
            src = config.video_dur(clip)
            factor = (n / FPS) / (src + (EASE_A * EASE_R / 2 if ease[i] else 0))
            t = "((PTS-STARTPTS)*TB)"
            warp = (f"if(lt({t},{EASE_R}),{t}+{EASE_A}*({t}-{t}*{t}/(2*{EASE_R})),{t}+{EASE_A * EASE_R / 2})".replace(",", "\\,")
                    if ease[i] else t)
            out = gen / f"{i:02d}_{s['id']}.mp4"
            ff("-i", clip, "-an", "-vf", f"setpts=({warp})*{factor:.5f}/TB,fps={FPS},scale=1080:1920:flags=lanczos,setsar=1,"
               "tpad=stop_mode=clone:stop_duration=1,format=yuv420p", "-frames:v", n, "-c:v", "libx264", "-crf", 12,
               "-preset", "fast", out)
            parts.append(out)
            rows.append({"seg": s["id"], "t0": s["t0"], "t1": s["t1"], "speed": round(1 / factor, 3), "ease_in": ease[i], "xf": xfs[i]})
        fc, last = [], "[0:v]"
        for i in range(1, len(parts)):
            fc.append(f"{last}[{i}:v]xfade=transition=fade:duration={xfs[i] / FPS:.4f}:offset={segs[i]['t0'] - half[i]:.4f}[x{i}]")
            last = f"[x{i}]"
        ff(*sum([["-i", p] for p in parts], []), "-filter_complex", ";".join(fc) or "[0:v]null[x0]", "-map", last if fc else "[x0]",
           "-t", f"{end:.3f}", "-c:v", "libx264", "-crf", 14, "-preset", "medium", "-r", FPS, gen / "picture.mp4")
        json.dump(rows, open(gen / "timeline.json", "w"), indent=1)
        for r in rows:
            print(r)
        mix_audio(pj, segs, end, gen)
    # captions: one transparent PNG per caption state (with the narration band), joined by the concat demuxer
    data = json.load(open(caps, encoding="utf-8"))
    ev = data["events"]
    band = Image.open(pj.dir / data["band"])
    cuts = sorted({0.0, end} | {round(min(max(e["t0"], 0), end), 3) for e in ev} | {round(min(e["t1"], end), 3) for e in ev})
    capdir = gen / "capstates"
    capdir.mkdir(exist_ok=True)
    for old in capdir.glob("*.png"):
        old.unlink()
    lst, p = [], None
    for k in range(len(cuts) - 1):
        t0, t1 = cuts[k], cuts[k + 1]
        if t1 - t0 < 1e-3:
            continue
        img = band.copy()
        for e in ev:
            if e["t0"] <= t0 + 1e-4 and e["t1"] >= t1 - 1e-4:
                img.alpha_composite(Image.open(pj.dir / e["png"]))
        p = capdir / f"{k:04d}.png"
        img.save(p)
        lst.append(f"file '{p.name}'\nduration {t1 - t0:.4f}")
    lst.append(f"file '{p.name}'")
    (capdir / "caps.txt").write_text("\n".join(lst) + "\n", encoding="utf-8")
    ff("-i", gen / "picture.mp4", "-f", "concat", "-safe", 0, "-i", capdir / "caps.txt", "-filter_complex",
       f"[1:v]fps={FPS},format=rgba[c];[0:v][c]overlay=0:0:format=auto:shortest=1,format=yuv420p[v]", "-map", "[v]",
       "-t", f"{end:.3f}", "-c:v", "libx264", "-crf", 16, "-preset", "medium", "-r", FPS, gen / "picture_caps.mp4")
    ff("-i", gen / "picture_caps.mp4", "-i", gen / "mix.wav", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
       "-b:a", "192k", "-movflags", "+faststart", "-shortest", gen / "final_master.mp4")
    ff("-i", gen / "final_master.mp4", "-c:v", "libx264", "-crf", 20, "-preset", "slow", "-maxrate", "14M", "-bufsize", "28M",
       "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", pj.p("out/final.mp4"))
    ff("-i", gen / "final_master.mp4", "-c:v", "libx264", "-b:v", "6M", "-maxrate", "8M", "-bufsize", "16M", "-preset", "slow",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", pj.p("out/final_share.mp4"))
    print("out/final.mp4", round(dur(pj.p("out/final.mp4")), 2), "秒（字幕样式", style + "）；手机转发用 out/final_share.mp4")


def mix_audio(pj, segs, end, gen):
    """Voice from `lead`; music bed about 13 dB under the voice and ducked when it speaks; each segment's own H3
    ambience (water, wind, birds) under both."""
    mix = pj.film.get("mix", {})
    bed = pj.dir / (pj.film.get("music") or {}).get("file", "music/bed.mp3")
    a_in = ["-i", pj.p("voice/voice.wav")]
    af = [f"[0:a]aresample=48000,adelay={int(pj.lead * 1000)}:all=1,apad[vo0]", "[vo0]asplit=2[vo][key]"]
    parts = ["[vo]"]
    n = 1
    if bed.exists():
        a_in += ["-i", bed]
        af += [f"[1:a]aresample=48000,loudnorm=I=-16:TP=-2,volume={mix.get('bed_db', -12)}dB,afade=t=in:d={mix.get('bed_fade_in', 1.5)},"
               f"afade=t=out:st={end - 3:.2f}:d=3[bedr]", "[bedr][key]sidechaincompress=threshold=0.05:ratio=3:attack=30:release=500[bed]"]
        parts.append("[bed]")
        n = 2
    else:
        af[1] = "[vo0]anull[vo]"
        print("没有配乐（music/bed.mp3），只混人声和 H3 环境声")
    for s in segs:
        span = s["t1"] - s["t0"]
        a_in += ["-i", pj.p(f"clips/{s['id']}.mp4")]
        af.append(f"[{n}:a]aresample=48000,atrim=0:{span + 0.3:.2f},apad=whole_dur={span + 0.3:.2f},afade=t=in:d=0.25,"
                  f"afade=t=out:st={span:.2f}:d=0.3,volume={mix.get('sfx_db', -15)}dB,adelay={int(s['t0'] * 1000)}:all=1[s{n}]")
        parts.append(f"[s{n}]")
        n += 1
    af.append("".join(parts) + f"amix=inputs={len(parts)}:normalize=0:duration=longest,atrim=0:{end:.3f},"
              f"loudnorm=I={mix.get('lufs', -14)}:TP=-1.2:LRA={mix.get('lra', 9)}[a]")
    ff(*a_in, "-filter_complex", ";".join(af), "-map", "[a]", "-ar", 48000, gen / "mix.wav")


# ------------------------------------------------------------------ check

def ssim(a, b):
    """SSIM of two grey images (numpy, Gaussian window σ=1.5)."""
    import numpy as np

    def blur(x):
        k = np.exp(-0.5 * (np.arange(-5, 6) / 1.5) ** 2)
        k /= k.sum()
        x = np.apply_along_axis(lambda r: np.convolve(r, k, "same"), 0, x)
        return np.apply_along_axis(lambda r: np.convolve(r, k, "same"), 1, x)
    a, b = a.astype(np.float64), b.astype(np.float64)
    c1, c2 = 6.5025, 58.5225
    m1, m2 = blur(a), blur(b)
    s1, s2, s12 = blur(a * a) - m1 ** 2, blur(b * b) - m2 ** 2, blur(a * b) - m1 * m2
    return float((((2 * m1 * m2 + c1) * (2 * s12 + c2)) / ((m1 ** 2 + m2 ** 2 + c1) * (s1 + s2 + c2))).mean())


REVIEW = ("这是一条竖屏中文科普短视频，整条设计成一个连续不断的长镜头（由多段 AI 视频首尾相接），有旁白、"
          "字幕（旁白短句、念到名词时的大号标题，可能还有古诗句加出处）、配乐和环境声。旁白稿如下：\n{script}\n"
          "请完整看完、听完，作为严格的审片人逐条列出具体问题，带时间点：(1) 画面明显跳变、闪烁、突然换景、变形崩坏、画面里出现文字；"
          "(2) 画面和旁白当时说的内容对不上；(3) 旁白读错字、多音字读错、语气不自然；(4) 字幕和语音不同步、字幕错字、古诗出处错误、"
          "字幕互相遮挡或看不清；(5) 配乐或环境声盖过人声、有怪声；(6) 节奏拖沓。最后打 1–10 分。用 JSON 回答：problems[] (t, type, "
          "detail, severity 1-3), score, verdict（中文）。")


def blank_runs(pic, segs):
    """Stretches where the picture loses almost all detail. A fast whip or a camera that swings up into open sky makes
    H3 paint a second of pure blue or white smear; a dark well or a pass through cloud also reads low but is meant.
    Bright and nearly featureless for 0.8 s or more is flagged for a redo; shorter or darker stretches are listed."""
    import numpy as np
    g = gray_frames(pic).astype(np.float32)
    det = np.abs(np.diff(g, axis=1)).mean(axis=(1, 2)) + np.abs(np.diff(g, axis=2)).mean(axis=(1, 2))
    lum = g.mean(axis=(1, 2))
    r = det / max(1e-6, float(np.median(det)))
    runs, i = [], 0
    while i < len(r):
        if r[i] < 0.35:
            j = i
            while j < len(r) and r[j] < 0.35:
                j += 1
            if j - i >= FPS // 2:
                runs.append((i / FPS, j / FPS, float(r[i:j].min()), float(lum[i:j].mean())))
            i = j
        else:
            i += 1
    seg_at = lambda t: next((s["id"] for s in segs if s["t0"] <= t < s["t1"]), segs[-1]["id"])
    out = ["", "## 糊帧和空帧（画面细节掉到整片中位数 35% 以下、连续 0.5 秒以上）", ""]
    if not runs:
        return out + ["没有。"]
    redo = []
    for t0, t1, low, lm in runs:
        bad = t1 - t0 >= 0.8 and low < 0.1 and lm > 60
        sid = seg_at((t0 + t1) / 2)
        out.append(f"- {sid} 段 {t0:.1f}–{t1:.1f} 秒，最低 {low:.0%}，亮度 {lm:.0f}" + ("  ← 亮而空：多半是甩到天空或糊成一片" if bad else ""))
        if bad and sid not in redo:
            redo.append(sid)
    if redo:
        out += ["", f"要重跑的段：{'、'.join(redo)}。把这几段的 move 改成镜头一直对着地面或主体（升高时同时往下看，"
                    "写成 rises ... while tilting down, keeping the landscape below in frame），幅度和速度降一档，"
                    "再 `h3 --seg <段> --redo`，然后 `cut`、`check`。"]
    else:
        out += ["", "都不长或偏暗（深井、水下、穿云这类是故意的），抽 out/contact.jpg 里对应的段看一眼就行。"]
    return out


def cmd_check(pj, a):
    """Measures, sheets and a review; every item Gemini lists has to be checked in the film by eye (it over-reports and
    is swayed by its prompt: it judged 茎 read as jìng right, and also 京 and 晶 as jìng)."""
    import numpy as np
    plan = pj.plan()
    segs = plan["segments"]
    final = pj.p("out/final.mp4")
    lines = ["# 检查", ""]
    # 1. did H3 honour the keyframes? first / last frame of each clip vs the keyframe it was given
    lines += ["## 首尾帧（SSIM，1 是一模一样；首尾两张关键帧互相比是对照）", "", "| 段 | 首帧 | 尾帧 | 两张关键帧互相 |", "| --- | --- | --- | --- |"]
    from PIL import Image
    for s in segs:
        clip = pj.p(f"clips/{s['id']}.mp4")
        if not clip.exists():
            continue
        fr = gray_frames(clip, 464, 832)
        for kid in (s["first"], s["last"]):
            if not pj.p(f"frames/{kid}_h3.jpg").exists():
                h3jpg(pj, kid)
        k0 = np.asarray(Image.open(pj.p(f"frames/{s['first']}_h3.jpg")).convert("L").resize((464, 832)), np.float32)
        k1 = np.asarray(Image.open(pj.p(f"frames/{s['last']}_h3.jpg")).convert("L").resize((464, 832)), np.float32)
        lines.append(f"| {s['id']} | {ssim(fr[0], k0):.3f} | {ssim(fr[-1], k1):.3f} | {ssim(k0, k1):.3f} |")
    # 2. seams and motion of the joined picture (no captions)
    pic = pj.p("gen/asm/picture.mp4")
    d = np.abs(np.diff(gray_frames(pic), axis=0)).mean(axis=(1, 2))
    med = float(np.median(d))
    lines += ["", f"## 接缝（帧差，整片中位数 {med:.2f}；接缝处最大值高过它就会看到跳）", "", "| 接缝 | 秒 | 接缝处最大 | 附近中位 |", "| --- | --- | --- | --- |"]
    bad = []
    for s in segs[1:]:
        i = round(s["t0"] * FPS)
        near = float(d[max(0, i - 5):i + 4].max())
        ctx = float(np.median(np.concatenate([d[max(0, i - 20):max(0, i - 6)], d[i + 5:i + 20]])))
        lines.append(f"| {s['id']} | {s['t0']:.2f} | {near:.2f} | {ctx:.2f} |")
        if near > max(med, 3 * ctx):
            bad.append(s["id"])
    lines += ["", ("值得看一眼的接缝：" + "、".join(bad) + "（看着跳就给这几段写 \"ease_in\": true 再 cut；还不行就重跑那段或改它的 move。"
                   "接缝后面紧跟一段快运镜时数值也会高，不一定是跳）")
              if bad else "接缝都在整片正常运动量以内。",
              "", f"运动量：每帧平均灰度变化 {float(d.mean()):.2f}（144×256），竹子范例是 8.9；镜头完全不动的片子在 1 以下。"]
    lines += blank_runs(pic, segs)
    # 3. contact sheets: three frames per segment, and one frame per title and per poem
    files, labels = [], []
    for s in segs:
        for k, t in enumerate([s["t0"] + 0.15, (s["t0"] + s["t1"]) / 2, s["t1"] - 0.2]):
            f = pj.p(f"gen/check/{s['id']}_{k}.jpg")
            frame_rgb(final, t, 220, 391).save(f, quality=85)
            files.append(f)
            labels.append(f"{s['id']}{'首中尾'[k]} {t:.1f}s")
    contact(files, labels, pj.p("out/contact.jpg"), cols=9)
    style = (pj.film.get("captions") or {}).get("style", "moyun")
    caps = pj.p(f"gen/caps_{style}/events.json")
    if caps.exists():
        ev = json.load(open(caps, encoding="utf-8"))["events"]
        pick = [e for e in ev if e["kind"] == "title" and not re.search(r"title\d+_[0-6]\.png$", e["png"])]
        last_state = {}
        for e in ev:
            if e["kind"] == "poem":
                last_state[e["png"].split("/")[-1].split("_")[0]] = e
        pick += list(last_state.values())
        files, labels = [], []
        for e in pick:
            t = (e["t0"] + e["t1"]) / 2
            f = pj.p(f"gen/check/cap_{len(files):02d}.jpg")
            frame_rgb(final, t, 360, 640).save(f, quality=88)
            files.append(f)
            labels.append(f"{t:.1f}s {e['text'][:8]}")
        contact(files, labels, pj.p("out/caption_check.jpg"), cols=6, w=360)
    lines += ["", "对照抽帧 out/contact.jpg（每段首中尾）、out/caption_check.jpg（每个标题、每首诗一帧）：逐张对着旁白看。"]
    # 4. Gemini watches and listens
    if not a.no_gemini:
        review = pj.p("out/review_540.mp4")
        ff("-i", final, "-vf", "scale=540:960", "-c:v", "libx264", "-crf", 28, "-preset", "fast", "-c:a", "aac", "-b:a", "96k", review)
        verdict = evo().gemini([REVIEW.format(script="\n".join(pj.film["script"])), review], max_tokens=8000)
        lines += ["", "## Gemini 审片（偏宽、会被提示带偏，每条回片子里亲眼核）", "", "```", verdict.strip(), "```"]
    pj.p("out/check.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def cmd_cost(pj, a):
    subs, done, h3, suno, gem = {}, {}, 0.0, 0.0, 0.0
    for line in open(pj.p("gen/ledger.jsonl"), encoding="utf-8"):
        x = json.loads(line)
        k = x.get("kind")
        if k == "submit":
            subs[x["task"]] = x.get("type")
        elif k == "task":
            done[x["task"]] = x.get("cost_cny") or 0
        elif k == "h3":
            h3 += x.get("cost_cny") or 0
        elif k == "gemini":
            gem += x.get("cost_cny_est") or 0
        elif k == "suno":
            suno += float(x.get("cost_usd") or 0) * 7.2
    by = {}
    for t, c in done.items():
        by[subs.get(t, "?")] = by.get(subs.get(t, "?"), 0) + c
    by.update({"h3": h3, "music": suno, "gemini": gem})
    names = {"image": "关键帧", "audio": "配音", "h3": "H3", "music": "配乐", "gemini": "审片"}
    for k, v in by.items():
        print(f"{names.get(k, k):6s} ¥{v:.2f}")
    print(f"合计   ¥{sum(by.values()):.2f}")


# ------------------------------------------------------------------ doctor

def cmd_doctor(pj, a):
    bad = []

    def line(good, what, fix="", must=True):
        print(("✓ " if good else ("✗ " if must else "· ")) + what + ("" if good or not fix else f" —— {fix}"))
        if must and not good:
            bad.append(what)
    line(config.python_ok(), f"Python {sys.version.split()[0]}", "要 3.11 或更新")
    for mod, pkg in [("httpx", "httpx"), ("PIL", "pillow"), ("numpy", "numpy")]:
        line(importlib.util.find_spec(mod), mod, f"pip install {pkg}")
    engine = config.asr()
    if engine == "cloud":
        line(True, "转写走云端 Whisper（~/.config/whisper-cloud/config.json）", "")
    elif engine:
        err = subprocess.run([sys.executable, "-c", f"import {engine}"], capture_output=True, text=True).stderr.strip()
        line(not err, f"转写 {engine}", "装上了但加载失败：" + (err.splitlines() or [""])[-1][:200])
    else:
        line(False, "转写", "Apple 芯片的 Mac 装 mlx-whisper，其他电脑装 faster-whisper")
    if engine and engine != "cloud" and a.fetch_model:
        config.fetch_model()
    if engine == "mlx_whisper":
        line(config.model_cached(), f"转写模型 {config.MLX_REPO}",
             "还没下载：跑 doctor --fetch-model（约 1.6 GB）。不先下的话第一次配音会自己下，但在 DeepSeek Harness、Codex 这类"
             "只放行工作区的沙盒里会改下到项目旁边的 .oneshot-models，每个工作区一份", must=False)
    try:
        caps = "\n".join(subprocess.run([FF, "-hide_banner", flag], capture_output=True, text=True).stdout
                         for flag in ("-filters", "-encoders"))
        line(True, f"ffmpeg {FF}")
        for name, pat in [("libx264", r"\slibx264\s"), ("PNG", r"\spng\s"), ("xfade", r"\sxfade\s"),
                          ("loudnorm", r"\sloudnorm\s"), ("sidechaincompress", r"\ssidechaincompress\s")]:
            line(re.search(pat, caps), f"  ffmpeg 带 {name}", "pip install imageio-ffmpeg，或用 FFMPEG= 指一个带它的 ffmpeg")
    except OSError:
        line(False, "ffmpeg", "pip install imageio-ffmpeg")
    if a.fetch_fonts:
        config.fetch_fonts()
    for f, ok, why in config.font_status():
        line(ok, f"字体 {f}", why + "：跑 doctor --fetch-fonts（从官方仓库下载并核 sha256）")
    g = rh().graph("doctor", "a.jpg", "b.jpg", 6, "9:16", 1)
    line(g["265"]["class_type"] == "MiniMaxH3ImageToVideo", "节点图 workflows/h3-ultimate-fl.json（首尾帧节点 265）")
    for name, (_, what) in config.KEYS.items():
        line(config.key(name, required=False), name, "没配置：" + what, must=name != "APIMART_API_KEY")
    if config.key("RUNNINGHUB_API_KEY", required=False):
        try:
            acc = rh().account()
            print(f"  RunningHub 余额 {acc.get('remainCoins')} RH 币 / ¥{acc.get('remainMoney')}，正在跑 {acc.get('currentTaskCounts')} 个任务")
        except Exception as exc:  # a wrong key or no network must not crash the check
            line(False, "RunningHub key 能用", str(exc)[:200])
    print("都齐了，可以开工" if not bad else "还缺：" + "、".join(bad))
    if bad:
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["doctor", "write", "voice", "tone", "fixline", "keyframes", "plan", "h3", "music",
                                     "captions", "cut", "check", "cost"])
    ap.add_argument("--project")
    ap.add_argument("--takes", type=int, default=1)
    ap.add_argument("--use", help="voice: align an existing recording instead of calling Doubao")
    ap.add_argument("--chars", nargs="*", default=[])
    ap.add_argument("--line", type=int)
    ap.add_argument("--speak", nargs="*", default=[], help="fixline: reader-only replacements, e.g. 茎=经")
    ap.add_argument("--id", nargs="*")
    ap.add_argument("--seg", nargs="*")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--style", choices=["moyun", "jinshu", "songyun", "jiaofa"])
    ap.add_argument("--caps-only", action="store_true", help="cut: reuse the joined picture and mix, lay captions on again")
    ap.add_argument("--no-gemini", action="store_true")
    ap.add_argument("--fetch-fonts", action="store_true")
    ap.add_argument("--fetch-model", action="store_true", help="doctor: download the transcription model now")
    a = ap.parse_args()
    if a.step == "doctor":
        return cmd_doctor(None, a)
    if not a.project:
        ap.error("--project is required")
    if a.step == "fixline" and a.line is None:
        ap.error("fixline 要 --line（从 0 数的句号）")
    globals()["cmd_" + a.step](Project(a.project), a)


if __name__ == "__main__":
    main()
