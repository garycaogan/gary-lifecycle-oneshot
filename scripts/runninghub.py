"""RunningHub: MiniMax H3 first + last frame (FL2VA) through YZ金鱼's public ComfyUI workflow 「MiniMax H3 终极篇-文武双全」
(workflowId 2109907990638850050) with its reference-to-video node swapped for ComfyUI's native first/last-frame node.
The graph is ../workflows/h3-ultimate-fl.json (see the README there); the whole graph goes with each task, with the
prompt, the two keyframes, the length and the seed filled in.

H3 is billed by machine time on the plus machine (~0.4 RH coins a second). Measured 2026-11-06 on 17 segments of
5–13 s: 3.5–7 minutes each, ¥0.40–0.93 a segment. Some plus machines lack a model file and fail within seconds;
`machine_fault` spots that so the task can be resubmitted."""
import json
import mimetypes
import os
import time
from pathlib import Path

import httpx

import config

BASE = os.environ.get("RUNNINGHUB_BASE", "https://www.runninghub.cn")
WORKFLOW_ID = "2109907990638850050"
GRAPH = config.SKILL / "workflows/h3-ultimate-fl.json"
INSTANCE = "plus"
COIN_CNY = float(os.environ.get("RUNNINGHUB_COIN_CNY", str(399 / 180000)))

# nodes of the graph: 265 is MiniMaxH3ImageToVideo (first_frame ← 51, last_frame ← 49)
PROMPT, LENGTH, SIZE, SEED, FIRST, LAST, FL_NODE = "263", "259", "252", "397", "51", "49", "265"
ASPECTS = {"9:16": "9:16 (Portrait Widescreen)", "16:9": "16:9 (Widescreen)", "1:1": "1:1 (Square)"}
MEGAPIXELS = 1.5   # 928×1664 for 9:16
CANVAS = {"9:16": (928, 1664), "16:9": (1664, 928), "1:1": (1216, 1216)}
MACHINE_RETRIES = 3


class ProviderError(RuntimeError):
    pass


class QueueFull(ProviderError):
    """The key's concurrent-task limit is reached (code 421); nothing is billed, try again later."""


def key():
    return config.key("RUNNINGHUB_API_KEY")


def _redact(text, k):
    return text.replace(k, "[key]")


def call(path, body, timeout=60, tries=8):
    """POST with retries on network errors (a blip must not lose a running task)."""
    k = key()
    for attempt in range(tries):
        try:
            r = httpx.post(BASE + path, json={"apiKey": k, **body}, timeout=timeout,
                           headers={"Authorization": "Bearer " + k, "Host": "www.runninghub.cn"})
            break
        except httpx.HTTPError as exc:
            if attempt == tries - 1:
                raise ProviderError(f"连不上 RunningHub：{type(exc).__name__}") from None
            time.sleep(10 * (attempt + 1))
    if r.status_code >= 300:
        raise ProviderError(f"RunningHub {r.status_code}：{_redact(r.text, k)[:300]}")
    out = r.json()
    if isinstance(out.get("data"), dict):  # connection urls carry temporary credentials
        out["data"].pop("netWssUrl", None)
        out["data"].pop("clientId", None)
    return json.loads(_redact(json.dumps(out, ensure_ascii=False), k))


def account():
    """Balance and current task count of the key (free call)."""
    return call("/uc/openapi/accountStatus", {"apikey": key()}, tries=2).get("data") or {}


def upload(path):
    """The file name RunningHub gives an uploaded input; it goes into a LoadImage node as it is."""
    k = key()
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    last = ""
    for attempt in range(4):
        try:
            with path.open("rb") as fh:
                r = httpx.post(BASE + "/task/openapi/upload", data={"apiKey": k, "fileType": "input"},
                               files={"file": (path.name, fh, mime)}, headers={"Authorization": "Bearer " + k}, timeout=180)
            out = json.loads(_redact(r.text, k))
        except (httpx.HTTPError, ValueError) as exc:
            last = type(exc).__name__
            time.sleep(5 * (attempt + 1))
            continue
        if out.get("code") == 0 and (out.get("data") or {}).get("fileName"):
            return out["data"]["fileName"]
        last = json.dumps(out, ensure_ascii=False)[:200]
    raise ProviderError(f"上传关键帧到 RunningHub 失败：{last}")


def frames_for(seconds):
    """What the workflow renders for a length in seconds: frames snapped up to 17k+5 at 24 fps (5 s minimum)."""
    n = max(5, round(max(5.0, min(15.0, float(seconds))) * 24))
    n += (5 - n % 17) % 17
    return n, n / 24


def fl_prompt(body, seconds):
    """MiniMax's FL2VA prompt: a fixed first line that pins Picture 1 (first frame) to 0 s and Picture 2 (last frame)
    to the end of the clip, a blank line, then integrated_multimodal_description / overall_soundscape /
    non_diegetic_music. Writing it any other way makes H3 treat the pictures as loose references."""
    n, eff = frames_for(seconds)
    head = ("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second "
            f"mark of the target video; Picture 2 (from Shot 1) aligns with the {eff:.2f}-second mark of the target video.")
    return head + "\n\n" + body.strip()


def graph(prompt, first, last, seconds, aspect, seed):
    g = json.loads(GRAPH.read_text(encoding="utf-8"))
    missing = [n for n in [PROMPT, LENGTH, SIZE, SEED, FIRST, LAST, FL_NODE] if n not in g]
    if missing or g[FL_NODE]["class_type"] != "MiniMaxH3ImageToVideo":
        raise ProviderError(f"节点图不对（缺 {missing} 或 265 不是 MiniMaxH3ImageToVideo）：workflows/h3-ultimate-fl.json 被换过？")
    g[PROMPT]["inputs"]["text"] = prompt
    g[LENGTH]["inputs"]["value"] = max(5.0, min(15.0, float(seconds)))
    g[SIZE]["inputs"]["aspect_ratio"] = ASPECTS.get(aspect, ASPECTS["9:16"])
    g[SIZE]["inputs"]["megapixels"] = MEGAPIXELS
    g[SEED]["inputs"]["seed"] = int(seed)
    g[FIRST]["inputs"]["image"] = first
    g[LAST]["inputs"]["image"] = last
    return g


def submit(g, instance=INSTANCE):
    body = {"workflowId": WORKFLOW_ID, "workflow": json.dumps(g, ensure_ascii=False), "addMetadata": False}
    if instance and instance != "default":
        body["instanceType"] = instance
    out = call("/task/openapi/create", body, tries=3)
    task = (out.get("data") or {}).get("taskId")
    if out.get("code") == 421 or "QUEUE_MAXED" in str(out.get("msg") or ""):
        raise QueueFull("RunningHub 队列满：" + json.dumps(out, ensure_ascii=False)[:200])
    if out.get("code") != 0 or not task:
        raise ProviderError("RunningHub 没有接下任务：" + json.dumps(out, ensure_ascii=False)[:300])
    return "rh:" + str(task)


def poll(remote_id):
    """{state: running|done|failed, url, cost (CNY), coins, run_seconds, error}"""
    task = remote_id.removeprefix("rh:")
    out = call("/task/openapi/outputs", {"taskId": task})
    code = out.get("code")
    if code in (804, 813):          # running / queued
        return {"state": "running", "progress": "排队中" if code == 813 else "", "cost": None}
    if code == 805:
        data = out.get("data") if isinstance(out.get("data"), dict) else {}
        reason = data.get("failedReason") or out.get("msg")
        if isinstance(reason, str) and reason.startswith("{"):
            try:
                reason = json.loads(reason)
            except ValueError:
                pass
        if isinstance(reason, dict):
            reason = f"{reason.get('node_name', '')} {reason.get('exception_type', '')}: {str(reason.get('exception_message', ''))[:200]}"
        return {"state": "failed", "error": str(reason)[:300], "cost": None}
    if code != 0 or not out.get("data"):
        return {"state": "running", "progress": f"RunningHub {code} {out.get('msg', '')}"[:60], "cost": None}
    items = out["data"]
    item = next((d for d in items if str(d.get("fileType", "")).lower() in ("mp4", "mov", "webm")), items[0])
    coins = float(item["consumeCoins"]) if item.get("consumeCoins") not in (None, "") else None
    money = float(item["consumeMoney"]) if item.get("consumeMoney") not in (None, "") else None
    cny = money if money is not None else (round(coins * COIN_CNY, 4) if coins is not None else None)
    return {"state": "done", "url": item.get("fileUrl"), "cost": cny, "coins": coins,
            "run_seconds": float(item["taskCostTime"]) if item.get("taskCostTime") else None}


def machine_fault(error):
    """The task landed on a plus machine without one of the workflow's model files; failed tasks carry no billing."""
    e = str(error or "")
    return "FileNotFoundError" in e and ("Loader" in e or "Model in folder" in e)


def download(url, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
        if r.status_code >= 300:
            raise ProviderError(f"下载生成结果失败：{r.status_code}")
        tmp = out.with_suffix(out.suffix + ".part")
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes():
                fh.write(chunk)
    tmp.replace(out)
    return out
