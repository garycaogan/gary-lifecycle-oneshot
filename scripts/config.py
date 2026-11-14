"""Keys, ffmpeg, fonts and Chinese transcription for the oneshot scripts. Nothing here prints a key.

A key is looked up in this order: the environment (EVOLINK_API_KEY, RUNNINGHUB_API_KEY, APIMART_API_KEY), a `.env` next
to SKILL.md (copy .env.example), then a key file named by EVOLINK_KEY_FILE / RUNNINGHUB_KEY_FILE / APIMART_KEY_FILE. An
optional scripts/local_keys.py can add more key-file places for one machine: `key_files(name)` returns a list of paths."""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36"


def _load_env(path=SKILL / ".env"):
    """KEY=value lines; values already in the environment win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.split(" #")[0].strip().strip('"').strip("'"))


_load_env()

# env name: (key-file env, what it is for and where to get one)
KEYS = {
    "EVOLINK_API_KEY": ("EVOLINK_KEY_FILE", "配音、关键帧和 Gemini 审片，在 evolink.ai 控制台的 API Keys 里建"),
    "RUNNINGHUB_API_KEY": ("RUNNINGHUB_KEY_FILE", "H3 首尾帧出片，在 runninghub.cn 个人中心的「API 调用」里拿"),
    "APIMART_API_KEY": ("APIMART_KEY_FILE", "Suno 配乐，在 apimart.ai 控制台的令牌管理里建"),
}


def _read(path):
    try:
        p = Path(path).expanduser()
        return p.read_text(encoding="utf-8").strip() if p.is_file() else ""
    except (OSError, ValueError, TypeError):
        return ""


def _local_key_files(name):
    """Extra key-file places from the optional scripts/local_keys.py (absent in a fresh install)."""
    try:
        import local_keys
    except ImportError:
        return []
    return list(local_keys.key_files(name))


def key(name, required=True):
    file_env, what = KEYS[name]
    value = os.environ.get(name, "").strip()
    for path in ([] if value else [os.environ.get(file_env), *_local_key_files(name)]):
        value = _read(path) if path else ""
        if value:
            break
    if not value and required:
        raise SystemExit(f"缺少 {name}（{what}）：写进 {SKILL / '.env'}（格式见 .env.example），或者 export {name}=…")
    return value


# ------------------------------------------------------------------ ffmpeg

def _ffmpeg():
    """An ffmpeg with png and libx264: $FFMPEG, else the build the imageio-ffmpeg wheel ships, else the system one
    (Homebrew's ffmpeg is often built without the PNG encoder)."""
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return shutil.which("ffmpeg") or "ffmpeg"


FF = _ffmpeg()


def ff(*args, cwd=None):
    subprocess.run([FF, "-loglevel", "error", "-y", *map(str, args)], check=True, cwd=cwd)


def dur(f):
    """Duration in seconds: a WAV directly, anything else from ffmpeg's Duration line."""
    import re
    f = Path(f)
    if f.suffix.lower() == ".wav":
        try:
            with wave.open(str(f)) as w:
                return round(w.getnframes() / w.getframerate(), 6)
        except (wave.Error, EOFError):
            pass
    err = subprocess.run([FF, "-hide_banner", "-i", str(f)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not m:
        raise RuntimeError(f"读不出时长：{f}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def video_dur(f):
    """Length of the picture of an MP4, read from its video track header (exact without ffprobe)."""
    try:
        with open(f, "rb") as fh:
            def boxes(start, end):
                pos = start
                while pos + 8 <= end:
                    fh.seek(pos)
                    size, kind = struct.unpack(">I4s", fh.read(8))
                    head = 8
                    if size == 1:
                        size, head = struct.unpack(">Q", fh.read(8))[0], 16
                    elif size == 0:
                        size = end - pos
                    if size < head:
                        return
                    yield kind, pos + head, pos + size
                    pos += size

            def child(a, b, name):
                return next(((x, y) for k, x, y in boxes(a, b) if k == name), None)
            moov = child(0, fh.seek(0, 2), b"moov")
            for kind, a, b in boxes(*moov) if moov else []:
                mdia = child(a, b, b"mdia") if kind == b"trak" else None
                hdlr, mdhd = (child(*mdia, b"hdlr"), child(*mdia, b"mdhd")) if mdia else (None, None)
                if not hdlr or not mdhd:
                    continue
                fh.seek(hdlr[0] + 8)
                if fh.read(4) != b"vide":
                    continue
                fh.seek(mdhd[0])
                v1 = fh.read(4)[0] == 1
                fh.read(16 if v1 else 8)
                scale, length = struct.unpack(">IQ" if v1 else ">II", fh.read(12 if v1 else 8))
                if scale:
                    return round(length / scale, 6)
    except (OSError, struct.error):
        pass
    return dur(f)


# ------------------------------------------------------------------ fonts

FONTS = json.loads((SKILL / "fonts/fonts.json").read_text(encoding="utf-8"))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def font_path(name):
    return SKILL / "fonts" / name


def font_status():
    """[(file, ok, why)] for every font in fonts/fonts.json."""
    out = []
    for f in FONTS["fonts"]:
        p = font_path(f["file"])
        if not p.is_file():
            out.append((f["file"], False, "没下载"))
        elif p.stat().st_size != f["size"] or sha256(p) != f["sha256"]:
            out.append((f["file"], False, "文件和清单对不上（大小或 sha256）"))
        else:
            out.append((f["file"], True, ""))
    return out


def fetch_fonts():
    """Download the fonts that are missing from their official repositories and check size and sha256."""
    import httpx
    for f in FONTS["fonts"]:
        p = font_path(f["file"])
        if p.is_file() and sha256(p) == f["sha256"]:
            continue
        print(f"下载 {f['file']}（{f['size'] / 1e6:.1f} MB）← {f['url']}", flush=True)
        tmp = p.with_suffix(".part")
        with httpx.stream("GET", f["url"], headers={"User-Agent": UA}, timeout=600, follow_redirects=True) as r:
            if r.status_code >= 300:
                raise SystemExit(f"下载失败 {r.status_code}：{f['url']}（可以手动下载放进 fonts/，文件名 {f['file']}）")
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        got = sha256(tmp)
        if got != f["sha256"]:
            tmp.unlink()
            raise SystemExit(f"{f['file']} 的 sha256 对不上（{got[:12]}…，应为 {f['sha256'][:12]}…），没有保存")
        tmp.replace(p)
    print("字体齐了")


# ------------------------------------------------------------------ transcription (Chinese)

def audio_16k(path):
    """Mono 16 kHz float32 samples decoded with this skill's ffmpeg (the transcriber then needs no ffmpeg on PATH)."""
    import numpy as np
    raw = subprocess.run([FF, "-nostdin", "-i", str(path), "-threads", "0", "-f", "s16le", "-ac", "1", "-acodec",
                          "pcm_s16le", "-ar", "16000", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def asr():
    import importlib.util
    return next((m for m in ("mlx_whisper", "faster_whisper") if importlib.util.find_spec(m)), None)


def whisper_zh(path, prompt="以下是普通话的句子。"):
    """{segments: [{text, start, end, words: [{word, start, end}]}]} for Mandarin: mlx-whisper on Apple silicon,
    otherwise faster-whisper (ONESHOT_WHISPER_MODEL picks its model, default large-v3-turbo)."""
    engine = asr()
    if not engine:
        raise SystemExit("没装转写：Apple 芯片的 Mac 装 mlx-whisper，其他电脑装 faster-whisper（见 references/setup.md）")
    audio = audio_16k(path)
    if engine == "mlx_whisper":
        import mlx_whisper
        return mlx_whisper.transcribe(audio, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language="zh",
                                      word_timestamps=True, initial_prompt=prompt)
    from faster_whisper import WhisperModel
    model = WhisperModel(os.environ.get("ONESHOT_WHISPER_MODEL", "large-v3-turbo"), device="auto", compute_type="auto")
    segments, _ = model.transcribe(audio, language="zh", word_timestamps=True, initial_prompt=prompt)
    return {"segments": [{"text": s.text, "start": s.start, "end": s.end,
                          "words": [{"word": w.word, "start": w.start, "end": w.end} for w in s.words or []]}
                         for s in segments]}


def python_ok():
    return sys.version_info >= (3, 11)
