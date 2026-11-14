"""Evolink: file upload, Doubao Seed Audio and GPT Image 2.5 tasks, Gemini review, and the project's spend ledger and
budget (a trimmed copy of the client our other video skills use, so this skill runs on its own). The key comes from config.key and is never
printed. Doubao bills by the length of the audio it reads: a 110 s narration take is about ¥2.4, one sentence a few
cents (measured 2026-11-06)."""
import base64
import json
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import config

API = "https://api.evolink.ai"
UA = {"User-Agent": config.UA}
IMAGE_MODEL = "gpt-image-2.5-sunburst"

# Gemini through Evolink, credits per million tokens (1 credit = ¥0.1, as the task API reports cost.cny = credits / 10).
# Public price page https://evolink.ai/gemini-3-8-flash read 2026-10-22: input 45.9, cache read 4.6, output 229.5
# (introductory price until 2026-12-31). Thinking tokens bill as output. The Gemini call returns no money figure, so
# ledger lines carry cost_cny_est; image and audio tasks carry the provider's own cost_cny.
GEMINI_CREDITS = {"gemini-3.8-flash": {"in": 45.9, "cached": 4.6, "out": 229.5}}
CNY_PER_CREDIT = 0.1
# what a task not yet finished is assumed to cost when checking the budget (a 2K 9:16 frame about ¥0.4, a whole-script
# Doubao take about ¥2, measured 2026-11-03)
RESERVE = {"image": 0.6, "audio": 2.5, "gemini": 0.15}


class BudgetExceeded(SystemExit):
    pass


_SPEND = threading.Lock()  # parallel submits check the budget one at a time, each seeing the ones before it
_FINISHED = set()


def key():
    return config.key("EVOLINK_API_KEY")


def _req(method, url, body=None, timeout=120):
    k = key()
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Authorization": "Bearer " + k, "Content-Type": "application/json", **UA})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace').replace(k, '[KEY]')[:600]}") from None


def ledger(entry):
    path = os.environ.get("EVOLINK_LEDGER")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}, ensure_ascii=False) + "\n")


def gemini_cost(model, tin, tout, think=0, cached=0):
    c = GEMINI_CREDITS.get(model) or GEMINI_CREDITS["gemini-3.8-flash"]
    tin, tout, think, cached = (x or 0 for x in (tin, tout, think, cached))
    credits = ((tin - cached) * c["in"] + cached * c["cached"] + (tout + think) * c["out"]) / 1e6
    return round(credits * CNY_PER_CREDIT, 4)


def spending(path=None):
    """(spent, reserved) in ¥ from the project ledger. Finished tasks count once each; submitted tasks with no result
    yet are reserved at the RESERVE estimate; Gemini counts its estimate."""
    path = path or os.environ.get("EVOLINK_LEDGER")
    if not path or not os.path.exists(path):
        return 0.0, 0.0
    subs, done, gem = {}, {}, 0.0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                x = json.loads(line)
            except ValueError:
                continue
            if x.get("kind") == "submit" and x.get("task"):
                subs[x["task"]] = x
            elif x.get("kind") == "task" and x.get("task"):
                done[x["task"]] = x.get("cost_cny") or 0
            elif x.get("kind") == "gemini":
                gem += x.get("cost_cny_est") or gemini_cost(x.get("model"), x.get("in"), x.get("out"), x.get("think"), x.get("cached"))
    # a task the provider hasn't finished within 6 hours never will; it stops holding budget
    fresh = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 6 * 3600))
    reserved = sum(RESERVE.get(x.get("type"), RESERVE["image"]) for t, x in subs.items() if t not in done and x.get("ts", "") >= fresh)
    return round(sum(done.values()) + gem, 3), round(reserved, 3)


def guard(estimate, what):
    """Stop before a paid call that would take the project past its budget (film.json budget_cny, passed in as
    EVOLINK_BUDGET_CNY). Pending tasks count at their reserve, so a batch can't overshoot by being submitted at once."""
    cap = os.environ.get("EVOLINK_BUDGET_CNY")
    if not cap:
        return
    spent, reserved = spending()
    if spent + reserved + estimate > float(cap):
        raise BudgetExceeded(f"budget stop: {what} (about ¥{estimate:.2f}) would pass the ¥{float(cap):.2f} budget — "
                             f"spent ¥{spent:.2f}, ¥{reserved:.2f} reserved for unfinished tasks. Raise budget_cny in "
                             "film.json or collect the pending tasks first.")


def upload(path):
    """Public link (kept ~72 h by the provider) for a local reference file."""
    import httpx
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as fh:
        r = httpx.post("https://files-api.evolink.ai/api/v1/files/upload/stream",
                       headers={"Authorization": "Bearer " + key(), **UA}, files={"file": (path.name, fh, mime)}, timeout=300)
    r.raise_for_status()
    d = r.json()
    url = (d.get("data") or d).get("file_url")
    if not url:
        raise RuntimeError("upload failed: " + json.dumps(d, ensure_ascii=False)[:300])
    return url


def submit_image(prompt, size="21:9", refs=()):
    body = {"model": IMAGE_MODEL, "prompt": prompt, "size": size, "resolution": "2K", "n": 1, "quality": "high"}
    if refs:
        body["image_urls"] = list(refs)
    with _SPEND:
        guard(RESERVE["image"], "an image")
        out = _req("POST", API + "/v1/images/generations", body)
        ledger({"kind": "submit", "type": "image", "task": out.get("id")})
    return out["id"]


def submit_audio(body):
    with _SPEND:
        guard(RESERVE["audio"], "a voice take")
        out = _req("POST", API + "/v1/audios/generations", body)
        ledger({"kind": "submit", "type": "audio", "task": out.get("id")})
    return out["id"]


def poll(task_id):
    """{state: running|done|failed, url, cost, error}. A network blip while polling reads as still running (the task
    keeps going at the provider)."""
    try:
        st = _req("GET", f"{API}/v1/tasks/{task_id}")
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"state": "running", "progress": "network"}
    status = st.get("status")
    out = {"state": "running", "progress": st.get("progress")}
    if status == "completed":
        res = st.get("results") or [r.get("url") for r in st.get("result_data") or []]
        first = res[0] if res else None
        out.update(state="done", url=first if isinstance(first, str) else (first or {}).get("url"))
    elif status in ("failed", "cancelled"):
        out.update(state="failed", error=json.dumps(st.get("error") or status, ensure_ascii=False)[:400])
    if status in ("completed", "failed", "cancelled"):
        cost = ((st.get("usage") or {}).get("cost") or {}).get("cny")
        out["cost"] = cost
        if task_id not in _FINISHED:  # polled again to retry a download: one ledger line per task is enough
            _FINISHED.add(task_id)
            ledger({"kind": "task", "task": task_id, "status": status, "cost_cny": cost})
    return out


def wait(task_id, every=6, limit=1800):
    """Poll until the task ends or `limit` seconds pass. A timeout leaves the task running at the provider."""
    t0 = time.monotonic()
    while True:
        st = poll(task_id)
        if st["state"] != "running":
            return st
        if time.monotonic() - t0 >= limit:
            return {**st, "state": "timeout"}
        time.sleep(every)


def download(url, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    tmp.write_bytes(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300).read())
    tmp.replace(out)
    return out


def gemini(parts, model="gemini-3.8-flash", max_tokens=16000, temperature=0.2, timeout=900, tries=3):
    """parts: str (text) or Path (video/audio/image, sent inline — keep videos under ~18 MB)."""
    guard(RESERVE["gemini"], "a Gemini call")
    out_parts = []
    for p in parts:
        if isinstance(p, Path):
            mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            out_parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(p.read_bytes()).decode()}})
        else:
            out_parts.append({"text": p})
    body = {"contents": [{"role": "user", "parts": out_parts}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}
    for attempt in range(tries):  # the gateway sometimes answers 5xx (524) on a slow request; a retry usually works
        try:
            d = _req("POST", f"{API}/v1beta/models/{model}:generateContent", body, timeout=timeout)
            break
        except RuntimeError as e:
            if attempt == tries - 1 or not str(e).startswith("HTTP 5"):
                raise
            time.sleep(5 * (attempt + 1))
    um = d.get("usageMetadata") or {}
    tin, tout = um.get("promptTokenCount"), um.get("candidatesTokenCount")
    think, cached = um.get("thoughtsTokenCount"), um.get("cachedContentTokenCount")
    ledger({"kind": "gemini", "model": model, "in": tin, "out": tout, "think": think, "cached": cached,
            "cost_cny_est": gemini_cost(model, tin, tout, think, cached)})
    parts = ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise RuntimeError("Gemini returned nothing: " + str((d.get("candidates") or [{}])[0].get("finishReason")))
    return text
