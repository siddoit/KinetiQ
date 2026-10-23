"""KINETIQ browser mirror dashboard (single file, stdlib only).

Engine mode (default): background thread ticks mock_streamer at 1 Hz.
Stdin mode (--stdin): read JSON lines from stdin, validate, ignore non-JSON.

Every projected value carries a freshness label derived inside THIS process from
two clocks: when a line arrived, and when its data-bearing values last changed.
The packet's own ``timestamp`` is the phone's POST time (it advances on every
manual refresh of a cached tile), so it is never treated as freshness evidence.

Usage:
    python laptop\\web\\app.py
    python laptop\\web\\app.py --stdin
    python laptop\\web\\app.py --stdin --lane band
    hardware\\native\\kinetiq_native.exe --run-seconds 300 | python laptop\\web\\app.py --stdin
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_LAPTOP_DIR = os.path.dirname(_HERE)
_RECEIVER_DIR = os.path.join(_LAPTOP_DIR, "receiver")
_AI_DIR = os.path.join(_LAPTOP_DIR, "ai")
if _RECEIVER_DIR not in sys.path:
    sys.path.append(_RECEIVER_DIR)
if _AI_DIR not in sys.path:
    sys.path.append(_AI_DIR)

try:
    from mock_streamer import StreamerState, build_packet, derive_exertion
except ImportError:
    StreamerState = None  # type: ignore
    build_packet = None  # type: ignore
    derive_exertion = None  # type: ignore

try:
    from telemetry_schema import TelemetryRecord
except ImportError:
    TelemetryRecord = None  # type: ignore

STDIN_MODE = "--stdin" in sys.argv[1:]

# Display-only lane chip. Presentation metadata: never written into the
# telemetry JSON, never seen by telemetry_schema.
LANE_LABELS = {"band": "LANE A \u00b7 REAL BAND", "rtos": "LANE B \u00b7 RTOS CONTROL"}


def _parse_lane(argv):
    """Return the --lane key ("band" / "rtos"), "" when absent, "?" if valueless."""
    for index, arg in enumerate(argv):
        if arg == "--lane":
            return argv[index + 1].strip().lower() if index + 1 < len(argv) else "?"
        if arg.startswith("--lane="):
            return arg.split("=", 1)[1].strip().lower()
    return ""


LANE = _parse_lane(sys.argv[1:])
_ACTIVE_MODE = "rtos" if (LANE == "rtos") else "band"

def _current_lane_label():
    return LANE_LABELS.get(_ACTIVE_MODE, "LANE A \u00b7 REAL BAND")

LANE_LABEL = _current_lane_label()

# ---- freshness clocks (display-side only; never added to the JSON stream)
_LIVE_S = 60.0      # value first seen/changed less than this ago -> LIVE
_STALE_S = 60.0     # no arrival for longer than this -> STALE
_OFFLINE_S = 300.0  # no arrival for longer than this -> OFFLINE

# Data-bearing fields only. `timestamp` is excluded on purpose: it changes on
# every POST even when the band re-sends an identical cached tile.
_FINGERPRINT_FIELDS = (
    "heart_rate",
    "spo2",
    "activity",
    "motion_intensity",
    "exertion",
    "steps",
    "ppg_quality",
)

_lock = threading.Lock()
_latest = None  # type: ignore
_history = []  # type: ignore  # list of full packet dicts, max 90
_rejects = 0
_start_t = time.monotonic()
_source = "NATIVE NODE" if STDIN_MODE else "MOCK ENGINE"

# Receipt clocks, monotonic seconds. Ages are always computed from these, never
# from the packet timestamp (the phone's POST time, not the measurement time).
_fresh = {
    "first_seen_at": None,      # first valid line this process accepted
    "arrival_at": None,         # most recent valid line
    "value_changed_at": None,   # when the current fingerprint first appeared
    "value_changed_witnessed": False,  # did we watch that transition happen?
    "fingerprint": None,
    "arrivals": 0,
}

# ---- camera-as-sensor state (browser motion -> server hysteresis band)
_cam = {"motion": 0.0, "ts": 0.0, "band": "rest",
        "switch_t": time.monotonic()}
_CAM_FRESH_S = 3.0
_CAM_REST_MAX = 0.08
_CAM_RUN_MIN = 0.25
_CAM_DWELL_S = 1.5


def _cam_fresh(now=None):
    if STDIN_MODE:
        return False
    now = time.monotonic() if now is None else now
    with _lock:
        ts = _cam.get("ts", 0.0)
    return (now - ts) < _CAM_FRESH_S


def _cam_band_update(motion, now):
    """Hysteresis band with dwell: <0.08 rest, >0.25 run, else walk."""
    try:
        m = float(motion)
    except Exception:
        m = 0.0
    m = min(2.0, max(0.0, m))
    with _lock:
        band = _cam.get("band", "rest")
        switch_t = _cam.get("switch_t", now)
    desired = "rest" if m < _CAM_REST_MAX else ("run" if m > _CAM_RUN_MIN else "walk")
    if desired != band and (now - switch_t) >= _CAM_DWELL_S:
        band = desired
        switch_t = now
        with _lock:
            _cam["band"] = band
            _cam["switch_t"] = switch_t
    return band


def _current_source(now=None):
    if _ACTIVE_MODE == "band":
        if _cam_fresh(now):
            return "CAM + BAND 6"
        with _lock:
            arr = _fresh.get("arrivals", 0)
        return "HUAWEI BAND 6" if arr > 0 else "WAITING FOR BAND"
    if STDIN_MODE:
        return "NATIVE NODE"
    return "CAMERA" if _cam_fresh(now) else "FREERTOS NODE"

# ---- live assistant stack (primary path: grounded on CURRENT screen packet)
_AI = {}


def _load_ai_stack():
    if _AI.get("ok") or _AI.get("tried"):
        return _AI.get("ok", False)
    _AI["tried"] = True
    try:
        import assistant as _ast  # noqa

        _AI["advisor_cls"] = _ast.AIAdvisor
        _AI["verify"] = _ast.verify_grounding
        _AI["template"] = _ast.template_reply
        _AI["ok"] = True
    except Exception as exc:  # noqa
        _AI["ok"] = False
        _AI["err"] = "%s: %s" % (type(exc).__name__, exc)
    return _AI.get("ok", False)


def _payload7(packet):
    return {
        "heart_rate": int(packet["heart_rate"]),
        "spo2": int(packet["spo2"]),
        "activity": str(packet["activity"]),
        "motion_intensity": float(packet["motion_intensity"]),
        "exertion": str(packet["exertion"]),
        "steps": int(packet["steps"]),
        "ppg_quality": str(packet["ppg_quality"]),
    }


def _ask_live(question, timeout_s=25.0):
    """Primary: AIAdvisor on the CURRENT latest packet. Returns (answer, grounding)."""
    with _lock:
        pkt = dict(_latest) if _latest else None
    if pkt is None:
        return ("No telemetry yet -- wait a second and ask again.", "FAIL")
    payload = _payload7(pkt)
    advisor_cls = _AI["advisor_cls"]
    verify = _AI["verify"]
    template = _AI["template"]
    advisor = advisor_cls(timeout=min(20.0, timeout_s - 2.0))
    box = {}

    def _run():
        try:
            box["reply"] = advisor.advise(payload, question)
        except Exception as exc:  # noqa - LLM down -> template fallback
            box["fallback_err"] = str(exc)
            try:
                box["reply"] = template(payload)
            except Exception as exc2:  # noqa
                box["error"] = str(exc2)

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout=timeout_s)
    if th.is_alive() or "reply" not in box:
        raise TimeoutError("assistant timed out after %.0fs" % timeout_s)
    reply = str(box["reply"] or "").strip()
    if not reply:
        raise RuntimeError("assistant returned an empty reply")
    failures = verify(reply, payload)
    return (reply, "PASS" if not failures else "FAIL")


def _ask_subprocess(question, timeout_s=25.0):
    """Fallback: laptop/.venv/Scripts/python.exe laptop/ai/assistant.py --query."""
    root = os.path.dirname(os.path.dirname(_LAPTOP_DIR))
    venv_py = os.path.join(root, "laptop", ".venv", "Scripts", "python.exe")
    asst = os.path.join(_AI_DIR, "assistant.py")
    py = venv_py if os.path.isfile(venv_py) else sys.executable
    try:
        proc = subprocess.run(
            [py, asst, "--query", question, "--once", "--no-tts"],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError("assistant timed out after %.0fs" % timeout_s)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    grounding = "FAIL"
    for line in out.splitlines():
        if "[grounding] PASS" in line:
            grounding = "PASS"
            break
        if "[grounding] FAIL" in line:
            grounding = "FAIL"
            break
    answer = ""
    for line in (proc.stdout or "").splitlines():
        if line.startswith("[llm] reply via"):
            idx = line.find(": ", line.find("reply via"))
            answer = line[idx + 2:] if idx >= 0 else line
            break
    if not answer:
        for line in (proc.stdout or "").splitlines():
            if line.startswith("[TTS] "):
                answer = line[6:]
                break
    if not answer.strip():
        # last resort: whole stdout minus bracketed log lines
        kept = [ln for ln in (proc.stdout or "").splitlines()
                if ln.strip() and not ln.startswith("[")]
        answer = " ".join(kept).strip()
    if not answer.strip():
        raise RuntimeError("assistant subprocess produced no reply")
    return (answer.strip(), grounding)


def ask_question(question):
    question = (question or "").strip()
    if not question:
        raise ValueError("empty question")
    if _load_ai_stack():
        try:
            return _ask_live(question)
        except Exception as exc:  # noqa - fall through to subprocess
            print("[ask] live import failed (%s); subprocess fallback" % exc,
                  file=sys.stderr, flush=True)
    return _ask_subprocess(question)


# ---- voice: STT singleton (faster-whisper tiny.en) + spoken replies (pyttsx3)
_STT_LOCK = threading.Lock()
_STT = {"transcriber": None, "err": None}
_TTS_LOCK = threading.Lock()
MAX_AUDIO_BYTES = 2_000_000  # ~1 MB cap (webm clips are tiny; wav tests fit)


def _get_transcriber():
    """Lazy singleton; reuses voice_pipeline.AudioTranscriber (incl. PyAV shim)."""
    with _STT_LOCK:
        if _STT["transcriber"] is not None:
            return _STT["transcriber"]
        try:
            from voice_pipeline import (  # noqa
                AudioTranscriber,
                _patch_av_open_for_faster_whisper,
            )
        except ImportError:
            from laptop.ai.voice_pipeline import (  # noqa
                AudioTranscriber,
                _patch_av_open_for_faster_whisper,
            )
        _patch_av_open_for_faster_whisper()
        tr = AudioTranscriber(model_size="tiny.en")
        _STT["transcriber"] = tr
        return tr


def _decode_to_mono16k(audio_bytes):
    """Decode any PyAV container -> 16 kHz mono float32 numpy array."""
    try:
        from voice_pipeline import _patch_av_open_for_faster_whisper  # noqa
    except ImportError:
        from laptop.ai.voice_pipeline import (  # noqa
            _patch_av_open_for_faster_whisper,
        )
    _patch_av_open_for_faster_whisper()
    import av
    import numpy as np

    buf = io.BytesIO(bytes(audio_bytes))
    container = av.open(buf, mode="r", metadata_errors="ignore")
    stream = next((s for s in container.streams.audio), None)
    if stream is None:
        raise ValueError("no audio stream found")
    resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
    chunks = []
    for frame in container.decode(stream):
        out = resampler.resample(frame)
        if out is None:
            continue
        if not isinstance(out, list):
            out = [out]
        for f in out:
            arr = f.to_ndarray()
            chunks.append(np.asarray(arr, dtype=np.float32).reshape(-1))
    try:
        container.close()
    except Exception:
        pass
    if not chunks:
        raise ValueError("could not decode any audio frames")
    import numpy as _np

    return _np.concatenate(chunks).astype(_np.float32, copy=False)


def transcribe_audio_bytes(audio_bytes, timeout_s=15.0):
    """Bytes -> transcript text. Decodes with PyAV, runs tiny.en. 15s cap."""
    if not audio_bytes:
        raise ValueError("empty audio upload")
    tr = _get_transcriber()
    tr._ensure_model()  # patched av.open inside; model loads once
    mono = _decode_to_mono16k(audio_bytes)
    if mono.size < 1600:  # <0.1s of audio
        raise ValueError("audio clip too short")
    box = {}

    def _run():
        try:
            segments, _info = tr._model.transcribe(mono, language="en")
            box["text"] = "".join(s.text for s in segments).strip()
        except Exception as exc:  # noqa
            box["error"] = "%s: %s" % (type(exc).__name__, exc)

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout=timeout_s)
    if th.is_alive():
        raise TimeoutError("transcription timed out after %.0fs" % timeout_s)
    if "error" in box:
        raise RuntimeError(box["error"])
    return box.get("text", "")


def _speak_async(text):
    """Speak a reply aloud (Windows SAPI5, offline). Never raises."""
    msg = (text or "").strip()
    if not msg:
        return

    def _run():
        with _TTS_LOCK:
            try:
                import pyttsx3

                eng = pyttsx3.init()
                try:
                    for v in eng.getProperty("voices"):
                        if "zira" in v.name.lower() or "female" in str(getattr(v, "gender", "")).lower():
                            eng.setProperty("voice", v.id)
                            break
                    eng.setProperty("rate", 165)
                except Exception:
                    pass
                eng.say(msg[:600])
                eng.runAndWait()
                try:
                    eng.stop()
                except Exception:
                    pass
            except Exception as exc:  # noqa - headless/TTS failure is non-fatal
                print("[tts] disabled (%s: %s)" % (type(exc).__name__, exc),
                      file=sys.stderr, flush=True)

    threading.Thread(target=_run, daemon=True).start()


# ---- freshness derivation: exactly four states, never guessed
def _fmt_age(seconds):
    """Compact age: '42s' under 5 min, '7m' under an hour, '2h05m' beyond."""
    s = int(max(0.0, float(seconds)))
    if s < 300:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    h, rem = divmod(s, 3600)
    return "%dh%02dm" % (h, rem // 60)


def _fingerprint(packet):
    """Stable identity of the data-bearing fields (see _FINGERPRINT_FIELDS)."""
    out = []
    for key in _FINGERPRINT_FIELDS:
        value = packet.get(key)
        if isinstance(value, float):
            value = round(value, 4)  # kill float formatting noise, keep resolution
        out.append(value)
    return tuple(out)


def _freshness(now=None):
    """Derive LIVE / UNCHANGED / STALE / OFFLINE from the receipt clocks.

    LIVE       value changed < 60 s ago             -> "changed 4s ago"
    UNCHANGED  arrival fresh, value static >= 60 s  -> "first seen 7m ago"
    STALE      no arrival for > 60 s                -> "no data 62s"
    OFFLINE    no arrival for > 300 s               -> "no data 5m"

    "first seen" (not "last changed") is deliberate: for the first value we
    observe we cannot know when it started, only that we have watched it since
    then, so the label states observation, never a claim about the band.
    """
    now = time.monotonic() if now is None else now
    with _lock:
        arrival = _fresh["arrival_at"]
        changed = _fresh["value_changed_at"]
        witnessed = _fresh["value_changed_witnessed"]
        arrivals = _fresh["arrivals"]
        first = _fresh["first_seen_at"]

    if arrival is None:
        state, label, age = "OFFLINE", "waiting for first packet", None
    else:
        age = max(0.0, now - arrival)
        if age > _OFFLINE_S:
            state, label = "OFFLINE", "no data " + _fmt_age(age)
        elif age > _STALE_S:
            state, label = "STALE", "no data " + _fmt_age(age)
        else:
            value_age = max(0.0, now - (changed if changed is not None else arrival))
            if value_age < _LIVE_S:
                state = "LIVE"
                label = ("changed " if witnessed else "first seen ") + _fmt_age(value_age) + " ago"
            else:
                state, label = "UNCHANGED", "first seen " + _fmt_age(value_age) + " ago"

    return {
        "state": state,
        "label": label,
        "age_s": None if age is None else round(age, 2),
        "value_age_s": (None if (arrival is None or changed is None)
                        else round(max(0.0, now - changed), 2)),
        "witnessed": bool(witnessed),
        "first_seen_s": (None if first is None
                         else round(max(0.0, now - first), 2)),
        "arrivals": arrivals,
        "live_s": _LIVE_S,
        "stale_s": _STALE_S,
        "offline_s": _OFFLINE_S,
    }


def _link(now=None):
    """LINK liveness only: has ANY line arrived recently?

    Independent of data recency so the screen can tell "phone alive, band
    static" (LINK green + UNCHANGED) from "pipe dead" (LINK red + STALE/OFFLINE).
    """
    now = time.monotonic() if now is None else now
    with _lock:
        arrival = _fresh["arrival_at"]
    if arrival is None:
        return {"state": "down", "label": "LINK DOWN", "detail": "waiting for first packet"}
    age = max(0.0, now - arrival)
    if age <= _STALE_S:
        return {"state": "up", "label": "LINK", "detail": "arrival " + _fmt_age(age) + " ago"}
    return {"state": "down", "label": "LINK", "detail": "no arrival " + _fmt_age(age)}


# ---- feeds
def _store(packet_dict):
    """Accept one validated packet and advance the receipt clocks."""
    global _latest, _rejects
    now = time.monotonic()
    fingerprint = _fingerprint(packet_dict)
    with _lock:
        _latest = packet_dict
        _history.append(packet_dict)
        while len(_history) > 90:
            _history.pop(0)
        _fresh["arrivals"] += 1
        _fresh["arrival_at"] = now
        if _fresh["first_seen_at"] is None:
            _fresh["first_seen_at"] = now
        if _fresh["fingerprint"] != fingerprint:
            # We only ever saw a change happen from a previous fingerprint we
            # had already observed; the first one is "first seen", not "changed".
            _fresh["value_changed_witnessed"] = _fresh["fingerprint"] is not None
            _fresh["fingerprint"] = fingerprint
            _fresh["value_changed_at"] = now


def _band_collector_loop():
    """Direct background collector for genuine Huawei Band 6 biometrics.

    Runs only when _ACTIVE_MODE == 'band' AND standard input is not active
    (not STDIN_MODE). If STDIN_MODE is True (e.g. band_bridge.py | app.py --stdin),
    _stdin_loop is the sole collector to prevent double-stamping and fingerprint thrash.
    """
    while True:
        if _ACTIVE_MODE != "band" or STDIN_MODE:
            time.sleep(0.5)
            continue
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8000/api/wearable/latest",
                headers={"Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=1.8) as resp:
                snap = json.loads(resp.read().decode("utf-8"))
            real = snap.get("real")
            if isinstance(real, dict) and real.get("simulated") is False:
                hr = real.get("heart_rate_bpm")
                spo2 = real.get("spo2_percent")
                steps = real.get("steps", 0)
                if hr is not None and spo2 is not None:
                    ts_val = int(time.time())
                    raw_ts = real.get("timestamp")
                    if raw_ts:
                        try:
                            val = str(raw_ts).strip()
                            if val.endswith(("Z", "z")):
                                val = val[:-1] + "+00:00"
                            dt = datetime.fromisoformat(val)
                            ts_val = int(dt.timestamp())
                        except Exception:
                            pass
                    if ts_val <= 0:
                        ts_val = int(time.time())

                    hr_int = int(hr)
                    spo2_int = int(round(float(spo2)))
                    steps_int = int(steps or 0)
                    if derive_exertion:
                        exert = derive_exertion(hr_int, "unknown")
                    else:
                        exert = "rest" if hr_int < 80 else ("light" if hr_int < 100 else ("moderate" if hr_int < 130 else "high"))

                    # Honest Band 6 contract (matches band_bridge.py):
                    # Activity and motion are not relayed by hardware -> marked honest placeholders.
                    # This prevents camera jitter from thrashing the freshness fingerprint.
                    pkt = {
                        "timestamp": ts_val,
                        "heart_rate": hr_int,
                        "spo2": spo2_int,
                        "activity": "unknown",
                        "motion_intensity": 0.0,
                        "exertion": exert,
                        "steps": steps_int,
                        "ppg_quality": "good",
                    }
                    if TelemetryRecord:
                        rec = TelemetryRecord.parse(pkt)
                        _store(rec.to_dict())
        except urllib.error.URLError:
            pass  # quiet polling when receiver is offline
        except Exception as exc:
            print("[band-collector] error: %s" % exc, file=sys.stderr, flush=True)
        time.sleep(1.0)


def _engine_loop():
    state = StreamerState(interval_s=1.0)
    _orig_locate = state._locate_phase
    while True:
        t0 = time.monotonic()
        if _ACTIVE_MODE != "rtos" or STDIN_MODE:
            time.sleep(0.5)
            continue
        try:
            now = time.monotonic()
            if _cam_fresh(now):
                with _lock:
                    m = float(_cam.get("motion", 0.0))
                band = _cam_band_update(m, now)
                phase = band if band in ("rest", "walk", "run") else "rest"
                state._locate_phase = (lambda _p=phase: (_p, 0.0))
            else:
                try:
                    state._locate_phase = _orig_locate
                except Exception:
                    pass
            raw = build_packet(state)
            rec = TelemetryRecord.parse(raw)
            _store(rec.to_dict())
        except Exception as exc:  # noqa - never kill the feed
            global _rejects
            with _lock:
                _rejects += 1
            print("[engine] reject: %s" % exc, file=sys.stderr, flush=True)
        dt = time.monotonic() - t0
        time.sleep(max(0.05, 1.0 - dt))


def _stdin_loop():
    global _rejects
    for line in sys.stdin:
        s = line.strip()
        if not s:
            continue
        if not s.startswith("{"):
            continue  # RTOS/banner lines on stdout -> ignore
        try:
            rec = TelemetryRecord.parse(s)
            _store(rec.to_dict())
        except Exception as exc:  # noqa - bad frame counted, never rendered
            with _lock:
                _rejects += 1
            print("[stdin] reject frame: %s" % exc, file=sys.stderr, flush=True)
    print("[stdin] pipe closed (EOF received)", file=sys.stderr, flush=True)


PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>KINETIQ Mirror</title>
<style>
/* ------------------------------------------------------------------------
   KINETIQ stage mirror. Flat instrument panel: near-black bays separated by
   1px hairlines, one blue accent (#2f81f7), semantic colours only for state.
   Designed at 1280x720 first, scaled up to 1080p: every font-size clamp
   carries a px floor so projection never shrinks below legibility.
   One viewport, no page scroll, system fonts only.
   ------------------------------------------------------------------------ */
:root{
  --bg:#05070a;
  --line:rgba(255,255,255,.09);
  --line-2:rgba(255,255,255,.16);
  --fg:#f2f5fa;
  --fg-2:#a9b4c3;
  --fg-3:#7b8798;
  --accent:#2f81f7;
  --ok:#39e56b;
  --warn:#ffb020;
  --bad:#ff5347;
  --t:190ms;
}
*{box-sizing:border-box}
html{height:100%}
body{
  margin:0;padding:0;
  height:100vh;width:100vw;overflow:hidden;
  background:var(--bg);
  color:var(--fg);
  font-family:"Segoe UI Variable Text","Segoe UI Variable","Segoe UI",Inter,system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif;
  font-size:16px;
  -webkit-font-smoothing:antialiased;
  display:grid;
  grid-template-rows:auto minmax(0,1fr) auto;
}
.num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1,"lnum" 1}

/* ---- header: wordmark left, one compact status cluster right --------- */
header{
  display:flex;align-items:center;justify-content:space-between;
  gap:24px;padding:10px 24px;
  border-bottom:1px solid var(--line);
  min-width:0;
}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.mark{width:9px;height:9px;background:var(--accent);flex:0 0 auto}
.wordmark{
  margin:0;font-size:clamp(16px,1.9vh,19px);font-weight:800;
  letter-spacing:.12em;line-height:1.25;white-space:nowrap;
}
.tag{
  font-size:clamp(16px,1.6vh,17px);color:var(--fg-3);letter-spacing:.04em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;
}
.hud{display:flex;align-items:center;gap:12px;flex:0 0 auto}

/* source mode switch: band vs rtos */
.mode-switch{
  display:inline-flex;align-items:center;gap:3px;
  background:rgba(255,255,255,.06);padding:3px;border-radius:4px;
  border:1px solid var(--line-2);
}
.mode-btn{
  font:inherit;font-size:clamp(12px,1.4vh,14px);font-weight:700;
  padding:.32em .75em;background:transparent;color:var(--fg-3);
  border:none;border-radius:3px;cursor:pointer;transition:all .15s ease;
  white-space:nowrap;
}
.mode-btn:hover{color:var(--fg);background:rgba(255,255,255,.08)}
.mode-btn.active{background:var(--accent);color:#fff}
.mode-btn.active.rtos{background:#238636;color:#fff}

/* object 1: source/lane chip - deliberately muted, the state owns attention */
.chip{
  display:inline-flex;align-items:center;gap:8px;
  font-size:clamp(16px,1.6vh,16px);font-weight:600;letter-spacing:.1em;
  color:var(--fg-3);border:1px solid var(--line);border-radius:3px;
  padding:.42em .8em;white-space:nowrap;
}
.chip .sep{opacity:.6}
#src.cam{color:#c9962f}

/* object 2: the single primary state indicator */
.stpill{
  font-size:clamp(18px,2vh,21px);font-weight:800;letter-spacing:.14em;
  padding:.42em .9em;border-radius:3px;white-space:nowrap;
  border:1px solid var(--line-2);color:var(--fg-3);min-width:11ch;text-align:center;
  transition:color var(--t) ease,background var(--t) ease,border-color var(--t) ease;
}
.stpill::before{
  content:"";display:inline-block;width:.5em;height:.5em;border-radius:1px;
  background:currentColor;margin-right:.6em;vertical-align:middle;
}
#stChip.LIVE{color:var(--ok);border-color:rgba(57,229,107,.5);background:rgba(57,229,107,.10)}
#stChip.UNCHANGED{color:var(--warn);border-color:rgba(255,176,32,.5);background:rgba(255,176,32,.10)}
#stChip.STALE{color:var(--bad);border-color:rgba(255,83,71,.5);background:rgba(255,83,71,.10)}
#stChip.OFFLINE{color:var(--fg-3);border-color:var(--line-2);background:transparent}

/* object 3: LINK dot + age + clock, one borderless muted text group */
.linkgrp{display:flex;align-items:center;gap:12px;font-size:clamp(18px,1.8vh,20px);white-space:nowrap}
.badge{font-weight:700;letter-spacing:.1em;color:var(--bad)}
.badge::before{
  content:"";display:inline-block;width:.55em;height:.55em;border-radius:50%;
  background:currentColor;margin-right:.55em;vertical-align:middle;
}
.badge.up{color:var(--ok)}
#stAge{color:var(--fg-2);min-width:20ch;font-variant-numeric:tabular-nums}
#clock{color:var(--fg-3);min-width:8ch;font-variant-numeric:tabular-nums}

/* ---- layout: vitals own the clear majority of width ------------------ */
main{
  display:grid;grid-template-columns:minmax(0,1fr) minmax(0,32%);
  gap:20px;padding:20px 24px;min-height:0;overflow:hidden;
}
.col{display:grid;gap:16px;min-height:0;min-width:0}
.col.left{grid-template-rows:1.35fr 1fr 0.55fr}
.col.right{grid-template-rows:auto minmax(56px,1fr)}
/* flat instrument bays: no card fill, no radius, no blur - hairlines only */
.zone{
  position:relative;min-width:0;min-height:0;overflow:hidden;
  border:1px solid var(--line);border-radius:4px;background:transparent;
  padding:16px 20px;display:flex;flex-direction:column;
}
.head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;min-width:0;flex:0 0 auto}
.eyebrow{
  font-size:clamp(17px,1.8vh,18px);font-weight:700;letter-spacing:.16em;
  text-transform:uppercase;color:var(--fg-3);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;min-width:0;
}
.age{
  font-size:clamp(16px,1.6vh,17px);color:var(--fg-3);
  white-space:nowrap;flex:0 0 auto;font-variant-numeric:tabular-nums;
}
.note{font-size:clamp(16px,1.6vh,17px);color:var(--fg-3);white-space:nowrap}
/* OFFLINE only: there is genuinely nothing to show, so values blank + dim */
.dim{opacity:.3;filter:grayscale(1);transition:opacity var(--t) ease,filter var(--t) ease}

/* ---- hero: heart rate ---------------------------------------------- */
.hero{justify-content:center;gap:8px}
#vals{display:flex;flex-direction:column;justify-content:center;gap:6px;min-height:0}
.hero-top{display:flex;align-items:center;justify-content:space-between;gap:12px;min-width:0}
.act{
  font-size:clamp(18px,1.9vh,19px);font-weight:800;letter-spacing:.14em;
  padding:.35em .8em;border-radius:3px;white-space:nowrap;min-width:5ch;text-align:center;
  border:1px solid var(--line-2);color:var(--fg-2);
}
.act.rest{color:var(--ok);border-color:rgba(57,229,107,.45)}
.act.walk{color:var(--warn);border-color:rgba(255,176,32,.45)}
.act.run{color:var(--bad);border-color:rgba(255,83,71,.45)}
/* BPM is baseline-anchored to the numeral at a fixed offset: min-width 3ch
   plus tabular figures means digit-count changes never move the unit. */
.hero-num{
  display:flex;align-items:baseline;justify-content:center;gap:.06em;
  font-size:clamp(96px,24vh,240px);line-height:.92;
}
#hr{font-weight:800;letter-spacing:-.03em;min-width:3ch;text-align:right}
.unit{font-size:.16em;font-weight:700;letter-spacing:.14em;color:var(--fg-2)}
#ageH{text-align:center}

/* Freshness must be readable at 5 m without reading text, and no state may
   look like a rendering failure:
     LIVE       full white
     UNCHANGED  numeral stays full white; age + state pill go amber
     STALE      numeral stays full white (dimming reads as "broken" at the
                back of a room) + subtle amber ring on the hero bay; age +
                state pill red
     OFFLINE    the only state allowed to blank/dim (.dim on #vals/#vals2) */
body[data-st="UNCHANGED"] #ageH,
body[data-st="UNCHANGED"] #ageM,
body[data-st="UNCHANGED"] #ageS,
body[data-st="UNCHANGED"] #stAge{color:var(--warn)}
body[data-st="STALE"] #ageH,
body[data-st="STALE"] #ageM,
body[data-st="STALE"] #ageS,
body[data-st="STALE"] #stAge{color:var(--bad)}
body[data-st="STALE"] .hero{box-shadow:inset 0 0 0 2px rgba(255,176,32,.38)}

/* ---- secondary metrics --------------------------------------------- */
.tiles{gap:10px}
.tiles .head,#ageM{flex:0 0 auto}
#vals2{display:flex;flex-direction:column;gap:10px;flex:1 1 auto;min-height:0}
.tilegrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;flex:1 1 auto;min-height:0}
/* hairline-separated bays, not floating mini-cards */
.tile{
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;
  border-left:1px solid var(--line);padding:4px 8px;min-width:0;
}
.tile:first-child{border-left:0}
.k{
  font-size:clamp(17px,1.8vh,18px);font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--fg-3);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;max-width:100%;flex:0 0 auto;
}
.big{
  font-size:clamp(30px,4.3vh,46px);font-weight:700;line-height:1.05;white-space:nowrap;
  font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1,"lnum" 1;
}
/* label / bar / value read as one unit: the bar is capped instead of
   running the full row, so the value no longer sits orphaned at the far
   right. #mbar's % width semantics are unchanged (it fills .bar). */
.motion{display:flex;align-items:center;gap:16px;min-width:0;flex:0 0 auto}
.motion .k{flex:0 0 auto}
.bar{
  flex:0 1 min(46%,380px);min-width:120px;height:clamp(8px,1.3vh,14px);
  background:rgba(255,255,255,.07);overflow:hidden;
}
#mbar{
  display:block;height:100%;width:0%;
  background:var(--accent);
  transition:width var(--t) ease;
}
/* motion value shares the tile-value typography and right alignment */
.motion .big{flex:0 0 auto;min-width:4ch;text-align:right}

/* ---- sparkline ------------------------------------------------------ */
.sparkcard{gap:8px}
#spark{display:block;width:100%;flex:1 1 auto;min-height:0;height:auto}

/* ---- camera + assistant -------------------------------------------- */
.mirror{gap:10px;flex:0 0 auto}
.mirror-row{display:flex;align-items:center;gap:12px;min-width:0;flex-wrap:wrap}
.btn{
  font:inherit;font-size:clamp(16px,1.6vh,16px);font-weight:700;letter-spacing:.06em;
  padding:.55em 1em;border-radius:3px;cursor:pointer;white-space:nowrap;
  border:1px solid var(--line-2);background:transparent;color:var(--fg);
  transition:background var(--t) ease,border-color var(--t) ease,color var(--t) ease;
}
.btn:hover{background:rgba(255,255,255,.07)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.primary:hover{background:#4a92ff;border-color:#4a92ff}
#camVideo{width:min(40%,200px);display:block;background:#000;border-radius:4px;border:1px solid var(--line-2)}
#camCanvas{display:none}
#camMsg{min-height:1.4em;transition:color .2s ease}
.pres-badge{display:inline-flex;align-items:center;padding:2px 8px;font-size:11px;font-weight:700;letter-spacing:.04em;border-radius:4px;text-transform:uppercase}
.pres-badge.idle{background:rgba(110,118,129,.15);color:#8b949e;border:1px solid #30363d}
.pres-badge.detecting{background:rgba(210,153,34,.2);color:#e3b341;border:1px solid #d29922}
.pres-badge.active{background:rgba(46,160,67,.2);color:#3fb950;border:1px solid #2ea043}
.chat{gap:10px}
.tbtn{
  font:inherit;font-size:clamp(16px,1.6vh,16px);font-weight:700;line-height:1;
  padding:.4em .7em;cursor:pointer;border:1px solid var(--line-2);border-radius:3px;
  background:transparent;color:var(--fg-2);
}
.tbtn:hover{background:rgba(255,255,255,.07);color:var(--fg)}
.chat .head{padding-right:44px}
.chat .note{min-width:0;overflow:hidden;text-overflow:ellipsis}
#chatToggle{position:absolute;top:10px;right:12px;z-index:2}
#msgs{
  flex:1 1 auto;min-height:0;overflow-y:auto;overscroll-behavior:contain;
  padding:2px 2px 2px 0;font-size:clamp(16px,1.8vh,17px);line-height:1.5;
  scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.2) transparent;
}
.m{
  margin:0 0 10px;padding:.6em .8em;
  border:1px solid var(--line);border-radius:3px;background:rgba(255,255,255,.028);
  overflow-wrap:anywhere;word-break:break-word;
}
.g{
  font-size:clamp(16px,1.6vh,16px);font-weight:800;letter-spacing:.08em;
  padding:.14em .6em;border-radius:2px;margin-left:.6em;white-space:nowrap;
}
.pass{background:rgba(57,229,107,.14);color:var(--ok)}
.fail{background:rgba(255,83,71,.14);color:var(--bad)}
#qin{display:flex;gap:8px;flex:0 0 auto;min-width:0}
#q{
  flex:1 1 auto;min-width:0;font:inherit;font-size:clamp(16px,1.7vh,17px);
  padding:.55em .7em;border-radius:3px;border:1px solid var(--line-2);
  background:rgba(0,0,0,.35);color:var(--fg);
  transition:border-color var(--t) ease,box-shadow var(--t) ease;
}
#q:focus{outline:none;border-color:rgba(47,129,247,.6);box-shadow:0 0 0 3px rgba(47,129,247,.15)}
#mic.rec{border-color:rgba(255,83,71,.55);color:var(--bad);background:rgba(255,83,71,.10)}
/* collapsed assistant = one slim ~56px bar: last reply + input + expand.
   The input never shrinks below its min-width (130px) and the placeholder
   is swapped to a short "Ask…" by setChat(), so nothing truncates; the
   right padding (64px) clears the absolutely-positioned expand chevron. */
.chat.collapsed{flex-direction:row;align-items:center;justify-content:flex-end;
  align-self:end;gap:10px;padding:8px 64px 8px 12px}
.chat.collapsed .head,.chat.collapsed #msgs{display:none}
.chat.collapsed #lastReply{display:block;flex:1 1 150px}
.chat.collapsed #qin{flex:0 0 auto}
.chat.collapsed #q{flex:0 0 130px;min-width:130px}
.chat.collapsed #spk,.chat.collapsed #mic{display:none}
#lastReply{
  display:none;font-size:clamp(16px,1.6vh,16px);color:var(--fg-2);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;
}

/* ---- status strip: diagnostics only, 13px floor ---------------------- */
footer{
  display:flex;align-items:center;justify-content:space-between;gap:16px;
  padding:7px 24px;border-top:1px solid var(--line);
  font-size:clamp(16px,1.6vh,17px);color:var(--fg-3);min-width:0;
}
.disc{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.diag{flex:0 0 auto;white-space:nowrap;font-variant-numeric:tabular-nums;text-align:right}

@media (max-width:1180px){.tag{display:none}}
@media (max-width:1440px){main{grid-template-columns:minmax(0,1fr) minmax(0,34%)}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body data-st="OFFLINE">
<!-- freshness labels state observation only: "changed Ns ago" / "first seen Ns ago" / "no data Ns" -->
<header>
<div class="brand"><span class="mark"></span><h1 class="wordmark">KINETIQ</h1><span class="tag">the mirror that sees and answers</span></div>
<div class="hud">
<div class="mode-switch" id="modeSwitch" title="Switch active source: Band 6 vs FreeRTOS">
  <button id="btnModeBand" class="mode-btn active" onclick="setMode('band')">&#9200; Band 6</button>
  <button id="btnModeRtos" class="mode-btn rtos" onclick="setMode('rtos')">&#9889; FreeRTOS</button>
</div>
<span class="chip"><span id="lane" style="display:none">LANE</span><span class="sep" id="laneSep" style="display:none">&middot;</span><span id="src">...</span></span>
<span class="stpill" id="stChip">OFFLINE</span>
<span class="linkgrp"><span class="badge" id="link">LINK</span><span id="stAge">waiting for first packet</span><span id="clock">--:--:--</span></span>
</div>
</header>
<main>
<section class="col left">
<div class="zone hero" id="heroZone">
<div id="vals" class="dim">
<div class="hero-top">
<span class="eyebrow">Live vitals</span>
<span class="act" id="activity">REST</span>
</div>
<div class="hero-num num"><span id="hr">--</span><span class="unit">BPM</span></div>
</div>
<div class="age" id="ageH">no data yet</div>
</div>
<div class="zone tiles">
<div class="head"><span class="eyebrow">Secondary metrics</span><span class="age" id="ageM">no data yet</span></div>
<div id="vals2" class="dim">
<div class="tilegrid">
<div class="tile"><span class="k">SpO2</span><span class="big" id="spo2">--</span></div>
<div class="tile"><span class="k">Exertion</span><span class="big" id="ex">--</span></div>
<div class="tile"><span class="k">Steps</span><span class="big" id="steps">--</span></div>
<div class="tile"><span class="k">PPG</span><span class="big" id="ppg">--</span></div>
</div>
<div class="motion">
<span class="k">Motion index</span>
<span class="bar"><span id="mbar"></span></span>
<span class="big num" id="mv">--</span>
</div>
</div>
</div>
<div class="zone sparkcard">
<div class="head"><span class="eyebrow">Heart rate history</span><span class="age" id="ageS">no data yet</span></div>
<canvas id="spark" width="1200" height="140"></canvas>
</div>
</section>
<section class="col right">
<div class="zone mirror" id="mirror">
<div class="head"><span class="eyebrow">The mirror sees you</span><span id="presBadge" class="pres-badge idle">&#9898; No person detected</span></div>
<div class="mirror-row">
<button id="camBtn" class="btn primary">&#128247; Activate Camera Mirror</button>
<video id="camVideo" autoplay playsinline muted style="display:none"></video>
</div>
<div class="note" id="camMsg">Click above to start mirror camera presence recognition.</div>
<canvas id="camCanvas" width="64" height="48" style="display:none"></canvas>
</div>
<div class="zone chat" id="chat">
<div class="head"><span class="eyebrow">Ask the mirror</span><span class="note">checked against live data</span></div>
<button id="chatToggle" class="tbtn" title="Collapse assistant" aria-expanded="true" aria-label="Collapse or expand the assistant">&#9662;</button>
<div id="msgs"><div class="m">&#129726; I can see your live telemetry &mdash; ask me how you&rsquo;re doing.</div></div>
<div id="lastReply" title="I can see your live telemetry &mdash; ask me how you're doing.">&#129726; I can see your live telemetry &mdash; ask me how you&rsquo;re doing.</div>
<div id="qin">
<input id="q" placeholder="How am I doing?" autocomplete="off">
<button id="mic" class="btn" title="Voice input (mic)">&#127908;</button>
<button id="spk" class="btn" title="Spoken replies on/off">&#128266;</button>
<button id="send" class="btn primary">Send</button>
</div>
</div>
</section>
</main>
<footer>
<span class="disc">Sensors simulated &middot; camera motion only &mdash; no video stored or uploaded</span>
<span class="diag"><span id="up">uptime --</span> &middot; <span id="rej">rejected frames --</span></span>
</footer>
<script>
var hrs = [], freshLabel = 'no data yet', sw = 1, sh = 1;
function $(id) { return document.getElementById(id); }

/* ---- sparkline: heart-rate history drawn at device resolution ------- */
function sizeSpark() {
  var c = $('spark'), r = c.getBoundingClientRect();
  var dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  sw = Math.max(1, Math.round(r.width));
  sh = Math.max(1, Math.round(r.height));
  c.width = Math.round(sw * dpr);
  c.height = Math.round(sh * dpr);
  c.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
}
function draw() {
  var c = $('spark'), x = c.getContext('2d');
  x.clearRect(0, 0, sw, sh);
  /* padding on every side keeps the series clear of the card boundary at
     720p and 1080p alike; the left gutter carries the dimmed min/max value
     labels so the trace reads as data, not decoration. */
  var padY = Math.max(12, sh * 0.14), padR = Math.max(12, sw * 0.012),
      gutter = 36, i;
  var top = padY, base = sh - padY,
      w = Math.max(1, sw - gutter - padR),
      h = Math.max(1, base - top);
  if (hrs.length < 2) {
    x.strokeStyle = 'rgba(255,255,255,.10)';
    x.lineWidth = 1;
    x.setLineDash([4, 6]);
    x.beginPath();
    x.moveTo(gutter, sh / 2);
    x.lineTo(sw - padR, sh / 2);
    x.stroke();
    x.setLineDash([]);
    return;
  }
  var rawMn = Math.min.apply(null, hrs), rawMx = Math.max.apply(null, hrs);
  var mn = rawMn, mx = rawMx;
  if (mx - mn < 5) { mx += 2.5; mn -= 2.5; }
  var pts = [];
  for (i = 0; i < hrs.length; i++) {
    pts.push([gutter + i / (hrs.length - 1) * w, top + h - (hrs[i] - mn) / (mx - mn) * h]);
  }
  x.strokeStyle = 'rgba(255,255,255,.10)';
  x.lineWidth = 1;
  x.beginPath();
  x.moveTo(gutter, base + 0.5);
  x.lineTo(sw - padR, base + 0.5);
  x.stroke();
  x.lineWidth = Math.max(1.4, h / 22);
  x.lineJoin = 'round';
  x.lineCap = 'round';
  x.beginPath();
  x.moveTo(pts[0][0], pts[0][1]);
  for (i = 1; i < pts.length; i++) { x.lineTo(pts[i][0], pts[i][1]); }
  var grad = x.createLinearGradient(0, top, 0, base);
  grad.addColorStop(0, 'rgba(47,129,247,.30)');
  grad.addColorStop(1, 'rgba(47,129,247,0)');
  x.lineTo(gutter + w, base);
  x.lineTo(gutter, base);
  x.closePath();
  x.fillStyle = grad;
  x.fill();
  x.beginPath();
  x.moveTo(pts[0][0], pts[0][1]);
  for (i = 1; i < pts.length; i++) { x.lineTo(pts[i][0], pts[i][1]); }
  x.strokeStyle = '#2f81f7';
  x.stroke();
  /* min/max labels in the gutter: 16px, dimmed, values only - no
     time-window claim. Anchored to the top/bottom gridlines. */
  x.font = '400 16px "Segoe UI",system-ui,sans-serif';
  x.fillStyle = 'rgba(169,180,195,.8)';
  x.textAlign = 'right';
  x.textBaseline = 'middle';
  x.fillText(String(Math.round(rawMx)), gutter - 6, top);
  x.fillText(String(Math.round(rawMn)), gutter - 6, base);
}

/* ---- nothing is projected without an age: OFFLINE blanks every number
       instead of re-showing the last cached value as if it were current. */
function blank() {
  $('hr').textContent = '--';
  $('activity').textContent = '--';
  $('activity').className = 'act';
  $('spo2').textContent = '--';
  $('ex').textContent = '--';
  $('steps').textContent = '--';
  $('ppg').textContent = '--';
  $('mv').textContent = '--';
  $('mbar').style.width = '0%';
  hrs = [];
  draw();
}
function fmtUptime(s) {
  s = Number(s || 0);
  if (s < 60) { return s + 's'; }
  if (s < 3600) { return Math.floor(s / 60) + 'm'; }
  var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h + 'h' + (m < 10 ? '0' : '') + m + 'm';
}
function tick() {
  fetch('/api/telemetry').then(function (r) { return r.json(); }).then(function (d) {
    var f = d.freshness || {}, lk = d.link || {}, L = d.latest;
    var st = f.state || 'OFFLINE';
    freshLabel = f.label || 'no data yet';
    /* body[data-st] drives the state styling: LIVE full white, UNCHANGED
       amber age/pill with a full-white numeral, STALE a full-white numeral
       plus an amber ring on the hero bay, OFFLINE the only blank/dim state. */
    document.body.setAttribute('data-st', st);
    var chip = $('stChip');
    chip.textContent = st;
    chip.className = 'stpill ' + st;
    $('stAge').textContent = freshLabel;
    $('ageH').textContent = freshLabel;
    $('ageM').textContent = freshLabel;
    $('ageS').textContent = freshLabel;
    var lb = $('link');
    lb.textContent = lk.label || 'LINK';
    lb.className = 'badge ' + (lk.state === 'up' ? 'up' : 'down');
    lb.title = lk.detail || '';
    var lane = $('lane'), lsep = $('laneSep');
    if (d.lane) { lane.style.display = ''; lane.textContent = d.lane; lsep.style.display = ''; }
    else { lane.style.display = 'none'; lsep.style.display = 'none'; }
    var s = $('src');
    s.textContent = d.source;
    s.className = (d.source === 'CAMERA' ? 'cam' : '');
    $('up').textContent = 'uptime ' + fmtUptime(d.uptime_s);
    $('rej').textContent = 'rejected frames ' + (d.rejects === undefined ? '--' : d.rejects);
    /* STALE is still data: only OFFLINE dims the numbers. */
    $('vals').classList.toggle('dim', st === 'OFFLINE');
    $('vals2').classList.toggle('dim', st === 'OFFLINE');
    if (!L) { blank(); return; }
    var a = String(L.activity || '--').toUpperCase(), el = $('activity');
    el.textContent = a;
    el.className = 'act ' + (a === 'REST' ? 'rest' : (a === 'WALK' ? 'walk' : (a === 'RUN' ? 'run' : '')));
    $('hr').textContent = L.heart_rate;
    $('spo2').textContent = L.spo2;
    $('ex').textContent = L.exertion;
    $('steps').textContent = L.steps;
    $('ppg').textContent = L.ppg_quality;
    $('mv').textContent = Number(L.motion_intensity).toFixed(2);
    $('mbar').style.width = (Number(L.motion_intensity) * 100) + '%';
    hrs = d.history.slice(-60);
    draw();
    if (d.mode) { updateModeUI(d.mode, d.locked); }
  });
}

function setMode(m) {
  fetch('/api/mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode: m })
  }).then(function (r) { return r.json(); }).then(function (d) {
    if (d.error) { alert(d.error); }
    updateModeUI(d.mode, d.locked);
  }).catch(function () {});
}
function updateModeUI(m, locked) {
  var bb = $('btnModeBand'), br = $('btnModeRtos');
  if (bb && br) {
    bb.classList.toggle('active', m === 'band');
    br.classList.toggle('active', m === 'rtos');
    if (locked) {
      bb.disabled = true;
      br.disabled = true;
      bb.style.cursor = 'not-allowed';
      br.style.cursor = 'not-allowed';
      bb.style.opacity = (m === 'band' ? '1.0' : '0.4');
      br.style.opacity = (m === 'rtos' ? '1.0' : '0.4');
      bb.title = 'Locked: live telemetry piped from stdin';
      br.title = 'Locked: live telemetry piped from stdin';
    } else {
      bb.disabled = false;
      br.disabled = false;
      bb.style.cursor = 'pointer';
      br.style.cursor = 'pointer';
      bb.style.opacity = '';
      br.style.opacity = '';
      bb.title = 'Switch to Huawei Band 6 mode';
      br.title = 'Switch to FreeRTOS control mode';
    }
  }
}

/* ---- natural female speech synthesis (Jenny / Aria / Zira) ---- */
function speakNaturalFemale(text) {
  if (!window.speechSynthesis || !ttsOn) return;
  try {
    window.speechSynthesis.cancel();
    var clean = text.replace(/[*_#`~🪞🧑]/g, '').trim();
    var u = new SpeechSynthesisUtterance(clean);
    var voices = window.speechSynthesis.getVoices();
    var v = voices.find(function(x) {
      var n = x.name.toLowerCase();
      return (n.includes('natural') || n.includes('jenny') || n.includes('aria') || n.includes('zira') || n.includes('female')) && x.lang.startsWith('en');
    }) || voices.find(function(x) {
      return x.lang.startsWith('en') && !x.name.toLowerCase().includes('david') && !x.name.toLowerCase().includes('george') && !x.name.toLowerCase().includes('male');
    });
    if (v) { u.voice = v; }
    u.rate = 1.0;
    u.pitch = 1.05;
    window.speechSynthesis.speak(u);
  } catch (e) {
    console.error('Speech synthesis error:', e);
  }
}
if (window.speechSynthesis) {
  window.speechSynthesis.onvoiceschanged = function() {
    window.speechSynthesis.getVoices();
  };
}

/* ---- smart camera presence detection & greeting ---- */
var camStream = null, camPrev = null, camEMA = 0, camSenseTimer = null, camPostTimer = null;
var presenceSeconds = 0, autoGreetDone = false, lastGreetTimestamp = 0;
var faceDetector = null, lastHasFace = false, personInFrame = false;

if ('FaceDetector' in window) {
  try { faceDetector = new FaceDetector({ fastMode: true, maxDetectedFaces: 1 }); } catch (e) {}
}

function updatePresenceUI(state, text, msg) {
  var b = $('presBadge');
  if (b) {
    b.className = 'pres-badge ' + state;
    b.innerHTML = text;
  }
  if (msg) {
    $('camMsg').textContent = msg;
  }
}

function checkPresenceAndGreet() {
  var v = $('camVideo');
  if (!v || !v.videoWidth || !camStream) {
    presenceSeconds = 0;
    updatePresenceUI('idle', '&#9898; No person detected', 'Click above to activate camera mirror.');
    return;
  }

  if (personInFrame) {
    presenceSeconds++;
    if (presenceSeconds < 5) {
      updatePresenceUI('detecting', '&#128993; Person in range: ' + presenceSeconds + 's / 5s',
                       'Person detected! Hold position in front of mirror (' + presenceSeconds + '/5s to greeting)...');
    } else {
      updatePresenceUI('active', '&#128994; User Present (' + presenceSeconds + 's)',
                       'User recognized in mirror range! Health assistant is listening.');
      if (!autoGreetDone && (Date.now() - lastGreetTimestamp > 45000)) {
        autoGreetDone = true;
        lastGreetTimestamp = Date.now();
        var m = $('msgs');
        m.innerHTML += '<div class="m" style="border-color:var(--accent);color:#79c0ff">&#129726; <em>User detected for 5s &mdash; Spoken greeting activated!</em></div>';
        m.scrollTop = m.scrollHeight;
        sendText("Hello mirror, I just stepped in front of you. Greet me, tell me my live stats, and give me a quick health suggestion.", true);
      }
    }
  } else {
    if (presenceSeconds > 0) {
      presenceSeconds = Math.max(0, presenceSeconds - 2);
    }
    if (presenceSeconds === 0) {
      updatePresenceUI('idle', '&#9898; No person detected', 'Mirror idle &mdash; Step into camera range to activate.');
      if (autoGreetDone && (Date.now() - lastGreetTimestamp > 10000)) {
        autoGreetDone = false; // Reset so next visitor gets greeted
      }
    }
  }
}

function camStop(silent) {
  if (camSenseTimer) { clearInterval(camSenseTimer); camSenseTimer = null; }
  if (camPostTimer) { clearInterval(camPostTimer); camPostTimer = null; }
  if (camStream) { camStream.getTracks().forEach(function (t) { t.stop(); }); camStream = null; }
  camPrev = null;
  presenceSeconds = 0;
  autoGreetDone = false;
  personInFrame = false;
  var v = $('camVideo');
  v.removeAttribute('srcObject');
  v.srcObject = null;
  v.style.display = 'none';
  $('camBtn').textContent = '📷 Activate Camera Mirror';
  updatePresenceUI('idle', '&#9898; No person detected', 'Click above to activate camera mirror.');
}

function camFrame() {
  var v = $('camVideo');
  if (!v || !v.videoWidth) { return; }

  // 1. Check native face detection if supported by browser
  if (faceDetector) {
    faceDetector.detect(v).then(function (faces) {
      lastHasFace = faces.length > 0;
    }).catch(function () {});
  }

  // 2. Downsampled 64x48 canvas for central mirror-zone presence & motion
  var c = $('camCanvas'), x = c.getContext('2d');
  x.drawImage(v, 0, 0, 64, 48);
  var d = x.getImageData(0, 0, 64, 48).data, g = [], i;
  for (i = 0; i < d.length; i += 4) { g.push((d[i] + d[i + 1] + d[i + 2]) / 3); }

  if (camPrev) {
    var totalDiff = 0, centerDiff = 0, centerCount = 0;
    for (var y = 0; y < 48; y++) {
      for (var col = 0; col < 64; col++) {
        var idx = y * 64 + col;
        var diff = Math.abs(g[idx] - camPrev[idx]);
        totalDiff += diff;
        // Central 50% region (mirror presence zone: y: 10..38, col: 14..50)
        if (y >= 10 && y <= 38 && col >= 14 && col <= 50) {
          centerDiff += diff;
          centerCount++;
        }
      }
    }
    var raw = (totalDiff / g.length) / 255.0;
    var centerRaw = (centerDiff / (centerCount || 1)) / 255.0;
    camEMA = camEMA + 0.3 * (raw - camEMA);

    // Person is present if:
    // a) Face detected by Chromium API, OR
    // b) Center motion is above the noise floor (human breathing/micro-motion > 0.010), OR
    // c) Overall motion is active (> 0.014)
    personInFrame = lastHasFace || (centerRaw >= 0.010) || (raw >= 0.014);
  }
  camPrev = g;
}

function camPost() {
  fetch('/api/camera_ingest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ motion: camEMA, activity: 'auto' })
  }).catch(function () {});
  checkPresenceAndGreet();
}

$('camBtn').onclick = function () {
  if (camStream) { camStop(); return; }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    $('camMsg').textContent = 'No camera in this browser — staying on mock engine.';
    return;
  }
  var btn = this;
  navigator.mediaDevices.getUserMedia({ video: true }).then(function (stream) {
    camStream = stream;
    camEMA = 0;
    camPrev = null;
    presenceSeconds = 0;
    var v = $('camVideo');
    v.srcObject = stream;
    v.style.display = 'block';
    btn.textContent = '⏹ Stop Camera';
    updatePresenceUI('idle', '&#9898; Scanning...', 'Camera live — stand in front of mirror.');
    camSenseTimer = setInterval(camFrame, 250);
    camPostTimer = setInterval(camPost, 1000);
  }).catch(function () {
    $('camMsg').textContent = 'Camera blocked — allow camera permission in browser.';
  });
};

setInterval(function () { $('clock').textContent = new Date().toLocaleTimeString(); }, 1000);

/* ---- assistant ------------------------------------------------------ */
var ttsOn = true, recorder = null, chunks = [], recTimer = null, chatOpen = true;
function send() { var i = $('q'); sendText(i.value.trim()); i.value = ''; }
function sendText(q, isAuto) {
  if (!q) { return; }
  var m = $('msgs');
  if (!isAuto) {
    m.innerHTML += '<div class="m">&#129489; ' + q.replace(/</g, '&lt;') + '</div>';
  }
  var useBackendTTS = !('speechSynthesis' in window) && ttsOn;
  fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ q: q, tts: useBackendTTS })
  }).then(function (r) { return r.json(); }).then(function (d) {
    var g = d.grounding || 'FAIL', cls = g === 'PASS' ? 'pass' : 'fail';
    var reply = (d.answer || d.error || '?');
    m.innerHTML += '<div class="m">&#129726; ' + (reply.replace(/</g, '&lt;'))
      + '<span class="g ' + cls + '">' + g + '</span>'
      + '<span class="age" style="display:inline;margin-left:8px">' + freshLabel + '</span></div>';
    m.scrollTop = m.scrollHeight;
    var lr = $('lastReply');
    lr.textContent = '🪞 ' + reply;
    lr.title = reply;
    if (ttsOn && d.answer && ('speechSynthesis' in window)) {
      speakNaturalFemale(d.answer);
    }
  });
}
/* collapse toggle: collapsed = slim ~56px bar (last reply + input),
   expanded = full transcript. Page never scrolls either way. */
function setChat(open) {
  chatOpen = open;
  $('chat').classList.toggle('collapsed', !open);
  /* collapsed bar is narrow: a short placeholder keeps it un-truncated */
  $('q').placeholder = open ? 'How am I doing?' : 'Ask\u2026';
  var b = $('chatToggle');
  b.innerHTML = open ? '&#9662;' : '&#9652;';
  b.title = open ? 'Collapse assistant' : 'Expand assistant';
  b.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) { var m = $('msgs'); m.scrollTop = m.scrollHeight; }
}
$('chatToggle').onclick = function () { setChat(!chatOpen); };
$('spk').onclick = function () { ttsOn = !ttsOn; this.textContent = ttsOn ? '🔊' : '🔇'; };
function micHint(t) {
  var m = $('msgs');
  m.innerHTML += '<div class="m">🎤 ' + t + ' — type instead, text chat keeps working.</div>';
  m.scrollTop = m.scrollHeight;
}
function stopMic() {
  if (recTimer) { clearTimeout(recTimer); recTimer = null; }
  if (recorder && recorder.state !== 'inactive') { try { recorder.stop(); } catch (e) {} }
  var b = $('mic');
  b.classList.remove('rec');
  b.textContent = '🎤';
}
$('mic').onclick = function () {
  if (recorder && recorder.state !== 'inactive') { stopMic(); return; }
  if (!window.MediaRecorder || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    micHint('no microphone in this browser');
    return;
  }
  var btn = this;
  navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
    chunks = [];
    try { recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' }); }
    catch (e) { recorder = new MediaRecorder(stream); }
    recorder.ondataavailable = function (e) { if (e.data && e.data.size) { chunks.push(e.data); } };
    recorder.onstop = function () {
      stream.getTracks().forEach(function (t) { t.stop(); });
      if (!chunks.length) { micHint('heard nothing, try again'); return; }
      var blob = new Blob(chunks, { type: 'audio/webm' }), m = $('msgs');
      m.innerHTML += '<div class="m">🎤 transcribing…</div>';
      m.scrollTop = m.scrollHeight;
      fetch('/api/transcribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: blob
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.text) {
          m.innerHTML += '<div class="m">🧑 🎤 ' + d.text.replace(/</g, '&lt;') + '</div>';
          sendText(d.text);
        } else {
          micHint('could not hear that (' + (d.error || '?') + ')');
        }
        m.scrollTop = m.scrollHeight;
      }).catch(function () { micHint('transcription failed'); });
    };
    recorder.start();
    btn.classList.add('rec');
    btn.textContent = '⏹';
    recTimer = setTimeout(stopMic, 10000);
  }).catch(function () { micHint('mic blocked — allow microphone permission, or type instead'); });
};
$('send').onclick = send;
$('q').onkeydown = function (e) { if (e.key === 'Enter') { send(); } };

sizeSpark();
setInterval(tick, 1000);
tick();
window.addEventListener('resize', function () { sizeSpark(); draw(); });
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/telemetry":
            with _lock:
                latest = dict(_latest) if _latest else None
                hist = [p["heart_rate"] for p in _history]
                rej = _rejects
            body = {"latest": latest, "history": hist, "source": _current_source(),
                    "rejects": rej, "uptime_s": int(time.monotonic() - _start_t),
                    "freshness": _freshness(), "link": _link(),
                    "lane": _current_lane_label(), "mode": _ACTIVE_MODE,
                    "locked": STDIN_MODE}
            self._send(200, json.dumps(body), "application/json")
        elif self.path.split("?")[0] == "/api/mode":
            self._send(200, json.dumps({"mode": _ACTIVE_MODE, "lane": _current_lane_label(), "locked": STDIN_MODE}), "application/json")
        elif self.path.split("?")[0] in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        else:
            self._send(404, '{"error":"not found"}', "application/json")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/mode":
            global _ACTIVE_MODE
            if STDIN_MODE:
                self._send(409, json.dumps({
                    "ok": False,
                    "error": "Mode is locked: incoming telemetry is piped directly from stdin (%s)" % (LANE or "stdin"),
                    "mode": _ACTIVE_MODE,
                    "lane": _current_lane_label(),
                    "locked": True
                }), "application/json")
                return
            try:
                nbytes = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(nbytes) if nbytes > 0 else b"{}"
                data = json.loads(raw.decode("utf-8") or "{}")
                m = str(data.get("mode", "")).lower().strip()
                if m in ("band", "rtos"):
                    with _lock:
                        _ACTIVE_MODE = m
                    self._send(200, json.dumps({"ok": True, "mode": _ACTIVE_MODE, "lane": _current_lane_label(), "locked": False}), "application/json")
                    return
                self._send(400, json.dumps({"error": "invalid mode (band or rtos)"}), "application/json")
            except Exception as exc:
                self._send(500, json.dumps({"error": str(exc)}), "application/json")
            return
        if path == "/api/camera_ingest":
            if STDIN_MODE:
                self._send(409, json.dumps({"error": "stdin mode ignores camera"}),
                           "application/json")
                return
            try:
                nbytes = int(self.headers.get("Content-Length") or 0)
            except Exception:
                nbytes = 0
            try:
                raw = self.rfile.read(nbytes) if nbytes > 0 else b"{}"
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send(400, json.dumps({"error": "bad JSON"}),
                           "application/json")
                return
            try:
                motion = float(data.get("motion", 0.0))
            except Exception:
                self._send(400, json.dumps({"error": "bad motion"}),
                           "application/json")
                return
            motion = min(2.0, max(0.0, motion))
            now = time.monotonic()
            with _lock:
                _cam["motion"] = motion
                _cam["ts"] = now
            band = _cam_band_update(motion, now)
            self._send(200, json.dumps({"ok": True, "band": band,
                                        "source": _current_source(now)}),
                       "application/json")
            return
        if path == "/api/transcribe":
            try:
                nbytes = int(self.headers.get("Content-Length") or 0)
            except Exception:
                nbytes = 0
            if nbytes <= 0:
                self._send(400, json.dumps({"error": "empty audio upload"}),
                           "application/json")
                return
            if nbytes > MAX_AUDIO_BYTES:
                self._send(413, json.dumps({"error": "audio too large (cap ~1 MB)"}),
                           "application/json")
                return
            try:
                raw = self.rfile.read(nbytes)
            except Exception as exc:  # noqa
                self._send(400, json.dumps({"error": "unreadable upload: %s" % exc}),
                           "application/json")
                return
            try:
                text = transcribe_audio_bytes(raw, timeout_s=15.0)
            except TimeoutError as exc:
                self._send(504, json.dumps({"error": str(exc)}),
                           "application/json")
                return
            except Exception as exc:  # noqa - clean JSON errors, never crash
                self._send(500, json.dumps(
                    {"error": "%s: %s" % (type(exc).__name__, exc)}),
                    "application/json")
                return
            self._send(200, json.dumps({"text": text}), "application/json")
            return
        if self.path.split("?")[0] != "/api/ask":
            self._send(404, '{"error":"not found"}', "application/json")
            return
        try:
            nbytes = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(nbytes) if nbytes > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                data = {}
            q = str(data.get("q", "") or "")
            if not q.strip():
                self._send(400, json.dumps({"error": "missing 'q'"}),
                            "application/json")
                return
            try:
                answer, grounding = ask_question(q)
            except TimeoutError as exc:
                self._send(504, json.dumps({"error": str(exc)}),
                            "application/json")
                return
            except Exception as exc:  # noqa - never crash the server
                self._send(500, json.dumps(
                    {"error": "%s: %s" % (type(exc).__name__, exc)}),
                    "application/json")
                return
            try:
                # Speaker toggle: frontend sends {"tts": false} to mute speech.
                want_tts = data.get("tts", True)
                if want_tts is not False:
                    _speak_async(answer)
            except Exception:  # noqa - TTS failure never breaks the API
                pass
            self._send(200, json.dumps({"answer": answer,
                                        "grounding": grounding}),
                       "application/json")
        except Exception as exc:  # noqa
            try:
                self._send(500, json.dumps({"error": str(exc)}),
                            "application/json")
            except Exception:  # noqa
                pass


def main():
    if StreamerState is None or TelemetryRecord is None:
        print("error: receiver modules not importable", file=sys.stderr)
        return 2
    if LANE and LANE not in LANE_LABELS:
        print("error: --lane takes band or rtos (got %r)" % LANE, file=sys.stderr)
        return 2
    if STDIN_MODE:
        threading.Thread(target=_stdin_loop, daemon=True).start()
    else:
        threading.Thread(target=_band_collector_loop, daemon=True).start()
        threading.Thread(target=_engine_loop, daemon=True).start()
    port = 8777
    srv = None
    for port in (8777, 8778):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            srv = None
    if srv is None:
        print("error: ports 8777 and 8778 are busy", file=sys.stderr)
        return 1
    url = "http://127.0.0.1:%d/" % port
    print("KINETIQ WEB dashboard: %s  (source=%s%s)"
          % (url, _source, ", lane=%s" % LANE if LANE_LABEL else ""), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
