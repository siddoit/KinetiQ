"""KINETIQ browser mirror dashboard (stdlib-only backend).

Serves templates/index.html plus static/css/style.css and static/js/app.js;
no third-party web framework is required.

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

import collections
import io
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_HERE, "templates")
_STATIC_DIR = os.path.join(_HERE, "static")
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

# Track last ingested upstream Band 6 POST so polling does not fabricate 1 Hz arrivals
_band_state = {
    "last_post_key": None,
}


def _reset_telemetry_state():
    """Clear active telemetry & freshness clocks on mode switch to prevent provenance bleed."""
    global _latest
    with _lock:
        _latest = None
        _history.clear()
        _fresh["first_seen_at"] = None
        _fresh["arrival_at"] = None
        _fresh["value_changed_at"] = None
        _fresh["value_changed_witnessed"] = False
        _fresh["fingerprint"] = None
        _fresh["arrivals"] = 0
        _band_state["last_post_key"] = None


# ---- camera-as-sensor state (browser motion -> server hysteresis band)
_cam = {
    "motion": 0.0,
    "ts": 0.0,
    "band": "rest",
    "switch_t": time.monotonic(),
}
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


def _ask_live(question, timeout_s=35.0):
    """Primary: AIAdvisor on the CURRENT latest packet. Returns (answer, grounding)."""
    with _lock:
        pkt = dict(_latest) if _latest else None
    if pkt is None:
        t_deadline = time.monotonic() + 1.2
        while time.monotonic() < t_deadline:
            time.sleep(0.08)
            with _lock:
                if _latest:
                    pkt = dict(_latest)
                    break
    if pkt is None and build_packet is not None and _ACTIVE_MODE == "rtos":
        try:
            st = StreamerState(interval_s=1.0) if StreamerState else None
            pkt = build_packet(st) if st else None
        except Exception:
            pkt = None
    if pkt is None:
        return ("No telemetry yet -- wait a second and ask again.", "FAIL")
    payload = _payload7(pkt)
    advisor_cls = _AI["advisor_cls"]
    verify = _AI["verify"]
    template = _AI["template"]
    advisor = advisor_cls(timeout=min(30.0, timeout_s - 2.0))
    box = {}

    def _run():
        try:
            with _VISION_LOCK:
                ts = _MIRROR_VISION.get("timestamp", 0)
                vis = (
                    dict(_MIRROR_VISION)
                    if (ts > 0 and (time.time() - ts) < 30.0)
                    else None
                )
            ans = advisor.advise(payload, question, visual_context=vis)
            if not str(ans or "").strip():
                ans = template(payload)
            box["reply"] = ans
            box["backend"] = getattr(advisor, "last_backend", "")
        except Exception as exc:  # noqa - LLM down -> template fallback
            box["fallback_err"] = str(exc)
            try:
                box["reply"] = template(payload)
                box["backend"] = "template"
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
    # Self-heal live LLM drifts (when running against real Ollama/API backend)
    if failures and box.get("backend") in ("ollama", "api"):
        try:
            healed = str(template(payload) or "").strip()
            if healed and not verify(healed, payload):
                return (healed, "PASS")
        except Exception:
            pass
    return (reply, "PASS" if not failures else "FAIL")


def _ask_subprocess(question, timeout_s=35.0):
    """Fallback: laptop/.venv/Scripts/python.exe laptop/ai/assistant.py --query."""
    root = os.path.dirname(os.path.dirname(_LAPTOP_DIR))
    venv_py = os.path.join(root, "laptop", ".venv", "Scripts", "python.exe")
    asst = os.path.join(_AI_DIR, "assistant.py")
    py = venv_py if os.path.isfile(venv_py) else sys.executable
    cmd = [py, asst, "--query", question, "--once", "--no-tts"]
    with _VISION_LOCK:
        ts = _MIRROR_VISION.get("timestamp", 0)
        if ts > 0 and (time.time() - ts) < 30.0 and _MIRROR_VISION.get("descriptor"):
            cmd.extend(["--emotion", _MIRROR_VISION["descriptor"]])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
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
        kept = [
            ln
            for ln in (proc.stdout or "").splitlines()
            if ln.strip() and not ln.startswith("[")
        ]
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
            print(
                "[ask] live import failed (%s); subprocess fallback" % exc,
                file=sys.stderr,
                flush=True,
            )
    return _ask_subprocess(question)


# ---- voice: STT singleton (faster-whisper tiny.en) + spoken replies (pyttsx3)
_STT_LOCK = threading.Lock()
_STT = {"transcriber": None, "err": None}
_TTS_LOCK = threading.Lock()
_TTS_CACHE_LOCK = threading.Lock()
_TTS_CACHE: collections.OrderedDict[tuple[str, str], bytes] = collections.OrderedDict()
_TTS_CACHE_MAX = 24
MAX_AUDIO_BYTES = 2_000_000  # ~2 MB cap (webm clips are tiny; wav tests fit)


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
        try:
            container.close()
        except Exception:
            pass
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
    return np.concatenate(chunks).astype(np.float32, copy=False)


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
            try:
                segments, _info = tr._model.transcribe(
                    mono,
                    language="en",
                    beam_size=1,
                    best_of=1,
                    without_timestamps=True,
                    condition_on_previous_text=False,
                )
            except TypeError:
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
            com_init = False
            try:
                try:
                    import pythoncom  # type: ignore

                    pythoncom.CoInitialize()
                    com_init = True
                except Exception:
                    pass
                import pyttsx3

                eng = pyttsx3.init()
                try:
                    for v in eng.getProperty("voices"):
                        if (
                            "zira" in v.name.lower()
                            or "female" in str(getattr(v, "gender", "")).lower()
                        ):
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
                print(
                    "[tts] disabled (%s: %s)" % (type(exc).__name__, exc),
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                if com_init:
                    try:
                        import pythoncom  # type: ignore

                        pythoncom.CoUninitialize()
                    except Exception:
                        pass

    threading.Thread(target=_run, daemon=True).start()


def _patch_windows_socketpair():
    """Windows Hyper-V/NAT dynamic port collisions can break socketpair(host, 0)."""
    import socket

    if getattr(socket, "_kinetiq_patched", False):
        return
    orig = getattr(socket, "socketpair", None)

    def _safe_socketpair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
        for port in range(49200, 49950):
            lsock = socket.socket(family, type, proto)
            csock = None
            try:
                lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                lsock.bind(("127.0.0.1", port))
                lsock.listen(1)
                csock = socket.socket(family, type, proto)
                csock.connect(("127.0.0.1", port))
                ssock, _ = lsock.accept()
                lsock.close()
                return (ssock, csock)
            except Exception:
                for s in (csock, lsock):
                    if s is not None:
                        try:
                            s.close()
                        except Exception:
                            pass
                continue
        if orig:
            return orig(family, type, proto)
        raise OSError("Unable to establish local socketpair")

    socket.socketpair = _safe_socketpair
    socket._kinetiq_patched = True  # type: ignore[attr-defined]


def synthesize_neural_tts(text: str, voice: str = "en-US-JennyNeural") -> bytes | None:
    """Generate high-definition Microsoft Jenny Neural TTS MP3 audio via edge-tts."""
    msg = (text or "").strip()
    if not msg:
        return None
    clean = re.sub(r"[*_#`~]", "", msg).strip()
    if not clean:
        return None
    cache_key = (voice, clean)
    with _TTS_CACHE_LOCK:
        cached = _TTS_CACHE.get(cache_key)
        if cached is not None:
            _TTS_CACHE.move_to_end(cache_key)
            return cached
    try:
        _patch_windows_socketpair()
        import asyncio
        import edge_tts

        async def _produce():
            comm = edge_tts.Communicate(clean, voice)
            chunks = []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        audio = asyncio.run(_produce())
        if audio:
            with _TTS_CACHE_LOCK:
                _TTS_CACHE[cache_key] = audio
                while len(_TTS_CACHE) > _TTS_CACHE_MAX:
                    _TTS_CACHE.popitem(last=False)
        return audio
    except Exception:
        return None


# ---- ambient vision context: emotion and facial expression
_VISION_LOCK = threading.Lock()
_VISION_DETECTOR = {"detector": None}
_MIRROR_VISION = {
    "raw_emotion": "Neutral",
    "descriptor": "calm and balanced",
    "label": "Calm / Balanced",
    "tone": "balanced",
    "confidence": 1.0,
    "face_detected": False,
    "timestamp": 0.0,
}


def _get_emotion_detector():
    with _VISION_LOCK:
        if _VISION_DETECTOR["detector"] is not None:
            return _VISION_DETECTOR["detector"]
        try:
            from emotion_detector import EmotionDetector
        except ImportError:
            from laptop.ai.emotion_detector import EmotionDetector
        det = EmotionDetector.get_instance()
        _VISION_DETECTOR["detector"] = det
        return det


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
                label = (
                    ("changed " if witnessed else "first seen ")
                    + _fmt_age(value_age)
                    + " ago"
                )
            else:
                state, label = "UNCHANGED", "first seen " + _fmt_age(value_age) + " ago"

    return {
        "state": state,
        "label": label,
        "age_s": None if age is None else round(age, 2),
        "value_age_s": (
            None
            if (arrival is None or changed is None)
            else round(max(0.0, now - changed), 2)
        ),
        "witnessed": bool(witnessed),
        "first_seen_s": (
            None if first is None else round(max(0.0, now - first), 2)
        ),
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
        if len(_history) > 90:
            del _history[:-90]
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


def _parse_epoch_seconds(raw_ts) -> int | None:
    """Parse an ISO-8601 or numeric timestamp into Unix epoch seconds, or None."""
    if raw_ts is None:
        return None
    if isinstance(raw_ts, (int, float)):
        val = float(raw_ts)
        if val > 1e12:
            val /= 1000.0
        return int(val) if val > 0 else None
    try:
        s = str(raw_ts).strip()
        if not s:
            return None
        if s.isdigit():
            val = float(s)
            if val > 1e12:
                val /= 1000.0
            return int(val)
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


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
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=1.8) as resp:
                snap = json.loads(resp.read().decode("utf-8"))
            real = snap.get("real")
            if isinstance(real, dict) and real.get("simulated") is False:
                # Reject upstream snapshots explicitly marked offline by receiver
                upstream_fresh = str(real.get("freshness") or "").lower()
                if upstream_fresh == "offline":
                    time.sleep(1.0)
                    continue

                hr = real.get("heart_rate_bpm")
                spo2 = real.get("spo2_percent")
                steps = real.get("steps", 0)
                if hr is not None and spo2 is not None:
                    now_wall = time.time()
                    parsed_ts = _parse_epoch_seconds(
                        real.get("received_at") or real.get("timestamp")
                    )
                    ts_val = parsed_ts if (parsed_ts and parsed_ts > 0) else int(now_wall)

                    # Deduplicate polling: only record a new arrival when the phone
                    # actually sends a new POST (new timestamp/received_at/values).
                    post_key = (
                        real.get("received_at"),
                        real.get("timestamp"),
                        hr,
                        spo2,
                        steps,
                    )
                    with _lock:
                        last_key = _band_state["last_post_key"]
                        has_arrivals = _fresh["arrivals"] > 0

                    if post_key == last_key:
                        # No new POST arrived at kinetiq_receiver since our last poll.
                        # Do not call _store(), so STALE (>60s) and OFFLINE (>300s)
                        # clocks advance honestly when the phone stops transmitting.
                        time.sleep(1.0)
                        continue

                    # On first sight, ignore ancient cached receiver rows (>300s old)
                    if not has_arrivals and parsed_ts is not None:
                        age_wall = now_wall - parsed_ts
                        if age_wall > _OFFLINE_S:
                            with _lock:
                                _band_state["last_post_key"] = post_key
                            time.sleep(1.0)
                            continue

                    hr_int = int(hr)
                    spo2_int = int(round(float(spo2)))
                    steps_int = int(steps or 0)
                    if derive_exertion:
                        exert = derive_exertion(hr_int, "unknown")
                    else:
                        exert = (
                            "rest"
                            if hr_int < 80
                            else (
                                "light"
                                if hr_int < 100
                                else ("moderate" if hr_int < 130 else "high")
                            )
                        )

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
                    if TelemetryRecord and _ACTIVE_MODE == "band":
                        rec = TelemetryRecord.parse(pkt)
                        with _lock:
                            _band_state["last_post_key"] = post_key
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
                state._locate_phase = lambda _p=phase: (_p, 0.0)
            else:
                try:
                    state._locate_phase = _orig_locate
                except Exception:
                    pass
            raw = build_packet(state)
            rec = TelemetryRecord.parse(raw)
            if _ACTIVE_MODE == "rtos":
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


# ---- Static & Template File Resolution --------------------------------------

_ALLOWED_STATIC_EXTS = frozenset(
    {
        ".css",
        ".js",
        ".mjs",
        ".html",
        ".htm",
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".mp3",
        ".wav",
        ".ogg",
        ".webm",
        ".json",
        ".map",
        ".txt",
    }
)

# Explicit MIME overrides so Windows Registry quirks never serve .css/.js as text/plain
_MIME_OVERRIDES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}

_FAVICON_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<rect width="32" height="32" rx="7" fill="#070b12"/>'
    b'<rect x="7" y="7" width="18" height="18" rx="4" fill="#2f81f7"/>'
    b'<circle cx="16" cy="16" r="4.5" fill="#34d399"/>'
    b"</svg>"
)

_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>KINETIQ Mirror</title>
<link rel="stylesheet" href="/static/css/style.css"></head>
<body data-st="OFFLINE">
<header><div class="brand"><span class="mark"></span><h1 class="wordmark">KINETIQ</h1><span class="tag">the mirror that sees and answers</span></div>
<div class="hud"><div class="mode-switch" id="modeSwitch"><button id="btnModeBand" class="mode-btn active" onclick="setMode('band')">Band 6</button><button id="btnModeRtos" class="mode-btn rtos" onclick="setMode('rtos')">FreeRTOS</button></div>
<span class="chip"><span id="lane" style="display:none">LANE</span><span class="sep" id="laneSep" style="display:none">&middot;</span><span id="src">...</span></span>
<span class="stpill" id="stChip">OFFLINE</span>
<span class="linkgrp"><span class="badge" id="link">LINK</span><span id="stAge">waiting for first packet</span><span id="clock">--:--:--</span></span></div></header>
<main><section class="col left"><div class="zone hero" id="heroZone"><div id="vals" class="dim"><div class="hero-top"><span class="eyebrow">Live vitals</span><span class="act" id="activity">REST</span></div><div class="hero-num num"><span id="hr">--</span><span class="unit">BPM</span></div></div><div class="age" id="ageH">no data yet</div></div>
<div class="zone tiles"><div class="head"><span class="eyebrow">Secondary metrics</span><span class="age" id="ageM">no data yet</span></div><div id="vals2" class="dim"><div class="tilegrid"><div class="tile"><span class="k">SpO2</span><span class="big" id="spo2">--</span></div><div class="tile"><span class="k">Exertion</span><span class="big" id="ex">--</span></div><div class="tile"><span class="k">Steps</span><span class="big" id="steps">--</span></div><div class="tile"><span class="k">PPG</span><span class="big" id="ppg">--</span></div></div><div class="motion"><span class="k">Motion index</span><span class="bar"><span id="mbar"></span></span><span class="big num" id="mv">--</span></div></div></div>
<div class="zone sparkcard"><div class="head"><span class="eyebrow">Heart rate history</span><span class="age" id="ageS">no data yet</span></div><canvas id="spark" width="1200" height="140"></canvas></div></section>
<section class="col right"><div class="zone mirror" id="mirror"><div class="head"><span class="eyebrow">The mirror sees you</span><span><span id="presBadge" class="pres-badge idle"><span class="dot"></span>No person detected</span><span id="emoBadge" class="pres-badge idle" style="display:none"><span class="dot"></span>Mood: --</span></span></div><div class="mirror-row"><button id="camBtn" class="btn primary">Activate Camera Mirror</button><video id="camVideo" autoplay playsinline muted style="display:none"></video></div><div class="note" id="camMsg">Click above to start mirror camera presence recognition.</div><canvas id="camCanvas" width="64" height="48" style="display:none"></canvas></div>
<div class="zone chat" id="chat"><div class="head"><span class="eyebrow">Ask the mirror</span><span class="note">checked against live data</span></div><button id="chatToggle" class="tbtn" aria-expanded="true">&#9662;</button><div id="msgs"><div class="m assistant"><span class="m-tag">KINETIQ</span><span class="m-body">I can see your live telemetry &mdash; ask me how you&rsquo;re doing.</span></div></div><div id="lastReply">I can see your live telemetry &mdash; ask me how you&rsquo;re doing.</div><div id="qin"><input id="q" placeholder="How am I doing?" autocomplete="off"><button id="mic" class="btn">Mic</button><button id="spk" class="btn primary">Voice On</button><button id="send" class="btn primary">Send</button></div></div></section></main>
<footer><span class="disc" id="discText">Sensors simulated &middot; camera motion only &mdash; no video stored or uploaded</span><span class="diag"><span id="up">uptime --</span> &middot; <span id="rej">rejected frames --</span></span></footer>
<script src="/static/js/app.js"></script>
</body></html>"""


def _is_safe_subpath(base_dir: str, target_path: str) -> bool:
    """Return True if target_path resides inside base_dir (prevents .. traversal)."""
    base_abs = os.path.abspath(base_dir)
    target_abs = os.path.abspath(target_path)
    return target_abs == base_abs or target_abs.startswith(base_abs + os.sep)


def _find_static_file(req_path: str) -> str | None:
    """Resolve a requested static asset safely across static/, templates/, or web root."""
    clean = urllib.parse.unquote(req_path.split("?", 1)[0]).replace("\\", "/").lstrip("/")
    if not clean or ".." in clean.split("/"):
        return None
    if clean.startswith("static/"):
        clean = clean[len("static/") :].lstrip("/")
    if not clean:
        return None

    _, ext = os.path.splitext(clean)
    ext_lower = ext.lower()
    if ext_lower not in _ALLOWED_STATIC_EXTS:
        return None

    basename = os.path.basename(clean)
    rel_candidates = [clean]
    if ext_lower == ".css" and not clean.startswith("css/"):
        rel_candidates.append(os.path.join("css", clean))
        rel_candidates.append(os.path.join("css", basename))
    elif ext_lower in (".js", ".mjs") and not clean.startswith("js/"):
        rel_candidates.append(os.path.join("js", clean))
        rel_candidates.append(os.path.join("js", basename))
    if basename != clean:
        rel_candidates.append(basename)

    search_roots = (_STATIC_DIR, _TEMPLATES_DIR, _HERE)
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for rel in rel_candidates:
            full = os.path.abspath(os.path.join(root, rel))
            if _is_safe_subpath(root, full) and os.path.isfile(full):
                return full
    return None


def _load_index_html() -> str:
    """Load index.html from templates/, static/, or web root, falling back to _FALLBACK_HTML."""
    candidates = (
        os.path.join(_TEMPLATES_DIR, "index.html"),
        os.path.join(_HERE, "index.html"),
        os.path.join(_STATIC_DIR, "index.html"),
    )
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    content = fh.read()
                if content.strip():
                    return content
            except Exception:
                continue
    return _FALLBACK_HTML


# Export PAGE at module level for backwards compatibility with any test suite
# and for runtime page patching (kinetiq-integration/mirror_band.py appends its
# provenance chip by reassigning app.PAGE before the server starts).
PAGE = _load_index_html()
_PAGE_FROM_DISK = PAGE


def _served_page() -> str:
    """Serve the on-disk template, unless an integration replaced PAGE at runtime.

    mirror_band.py does ``app.PAGE = inject_chip(app.PAGE)``; identity comparison
    detects that patch without breaking live edits to templates/index.html.
    """
    if PAGE is not _PAGE_FROM_DISK:
        return PAGE
    return _load_index_html()


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, code: int, body: str | bytes, ctype: str, head_only: bool = False):
        data = body.encode("utf-8") if isinstance(body, str) else body
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _send_file(self, file_path: str, head_only: bool = False):
        try:
            with open(file_path, "rb") as fh:
                data = fh.read()
            _, ext = os.path.splitext(file_path)
            ext_lower = ext.lower()
            ctype = _MIME_OVERRIDES.get(ext_lower)
            if not ctype:
                guessed, _ = mimetypes.guess_type(file_path)
                ctype = guessed or "application/octet-stream"
                if ctype.startswith("text/") and "charset=" not in ctype:
                    ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            if not head_only:
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            self._send(
                500,
                json.dumps({"error": str(exc)}),
                "application/json",
                head_only=head_only,
            )

    def _handle_get_or_head(self, head_only: bool = False):
        path = self.path.split("?", 1)[0]
        if path == "/api/telemetry":
            with _lock:
                latest = dict(_latest) if _latest else None
                hist = [p["heart_rate"] for p in _history]
                rej = _rejects
            body = {
                "latest": latest,
                "history": hist,
                "source": _current_source(),
                "rejects": rej,
                "uptime_s": int(time.monotonic() - _start_t),
                "freshness": _freshness(),
                "link": _link(),
                "lane": _current_lane_label(),
                "mode": _ACTIVE_MODE,
                "locked": STDIN_MODE,
            }
            self._send(200, json.dumps(body), "application/json", head_only=head_only)
            return

        if path == "/api/mode":
            self._send(
                200,
                json.dumps(
                    {
                        "mode": _ACTIVE_MODE,
                        "lane": _current_lane_label(),
                        "locked": STDIN_MODE,
                    }
                ),
                "application/json",
                head_only=head_only,
            )
            return

        if path in ("/api/health", "/api/status"):
            with _lock:
                arr = _fresh.get("arrivals", 0)
                rej = _rejects
            health = {
                "ok": True,
                "mode": _ACTIVE_MODE,
                "lane": _current_lane_label(),
                "source": _current_source(),
                "locked": STDIN_MODE,
                "arrivals": arr,
                "rejects": rej,
                "uptime_s": int(time.monotonic() - _start_t),
                "freshness": _freshness(),
                "link": _link(),
            }
            self._send(200, json.dumps(health), "application/json", head_only=head_only)
            return

        if path == "/api/tts":
            query_str = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query_str)
            text = params.get("text", [""])[0]
            if not text:
                self._send(
                    400,
                    '{"error":"missing text"}',
                    "application/json",
                    head_only=head_only,
                )
                return
            audio_data = synthesize_neural_tts(text)
            if audio_data:
                self._send(200, audio_data, "audio/mpeg", head_only=head_only)
            else:
                self._send(
                    503,
                    '{"error":"neural tts unavailable"}',
                    "application/json",
                    head_only=head_only,
                )
            return

        if path == "/api/emotion":
            with _VISION_LOCK:
                self._send(
                    200,
                    json.dumps(_MIRROR_VISION),
                    "application/json",
                    head_only=head_only,
                )
            return

        if path in ("/", "/index.html"):
            self._send(
                200,
                _served_page(),
                "text/html; charset=utf-8",
                head_only=head_only,
            )
            return

        static_file = _find_static_file(path)
        if static_file:
            self._send_file(static_file, head_only=head_only)
            return

        if path == "/favicon.ico":
            self._send(200, _FAVICON_SVG, "image/svg+xml", head_only=head_only)
            return

        self._send(
            404,
            '{"error":"not found"}',
            "application/json",
            head_only=head_only,
        )

    def do_GET(self):
        self._handle_get_or_head(head_only=False)

    def do_HEAD(self):
        self._handle_get_or_head(head_only=True)

    def do_POST(self):
        global _rejects
        path = self.path.split("?", 1)[0]

        if path in ("/api/ingest", "/api/telemetry"):
            try:
                nbytes = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(nbytes) if nbytes > 0 else b"{}"
                payload_str = raw.decode("utf-8") or "{}"
                rec = TelemetryRecord.parse(payload_str)
                _store(rec.to_dict())
                self._send(
                    200,
                    json.dumps({"ok": True, "freshness": _freshness()["state"]}),
                    "application/json",
                )
            except Exception as exc:
                with _lock:
                    _rejects += 1
                self._send(
                    400,
                    json.dumps({"ok": False, "error": str(exc)}),
                    "application/json",
                )
            return

        if path == "/api/emotion":
            try:
                nbytes = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(nbytes) if nbytes > 0 else b"{}"
                data = json.loads(raw.decode("utf-8") or "{}")
                has_person = data.get("person", True)
                if not has_person:
                    res = {
                        "raw_emotion": "Neutral",
                        "descriptor": "calm and balanced",
                        "label": "Calm",
                        "tone": "balanced",
                        "confidence": 1.0,
                        "face_detected": False,
                        "timestamp": 0.0,
                    }
                else:
                    img_data = str(data.get("image", "") or "")
                    det = _get_emotion_detector()
                    res = det.detect_from_base64(img_data)
                with _VISION_LOCK:
                    _MIRROR_VISION.update(res)
                self._send(
                    200,
                    json.dumps({"status": "ok", "vision": res}),
                    "application/json",
                )
            except Exception as exc:
                self._send(500, json.dumps({"error": str(exc)}), "application/json")
            return

        if path == "/api/mode":
            global _ACTIVE_MODE, LANE_LABEL
            if STDIN_MODE:
                self._send(
                    409,
                    json.dumps(
                        {
                            "ok": False,
                            "error": "Mode is locked: incoming telemetry is piped directly from stdin (%s)"
                            % (LANE or "stdin"),
                            "mode": _ACTIVE_MODE,
                            "lane": _current_lane_label(),
                            "locked": True,
                        }
                    ),
                    "application/json",
                )
                return
            try:
                nbytes = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(nbytes) if nbytes > 0 else b"{}"
                data = json.loads(raw.decode("utf-8") or "{}")
                m = str(data.get("mode", "")).lower().strip()
                if m in ("band", "rtos"):
                    changed = False
                    with _lock:
                        if _ACTIVE_MODE != m:
                            _ACTIVE_MODE = m
                            LANE_LABEL = _current_lane_label()
                            changed = True
                    if changed:
                        _reset_telemetry_state()
                    self._send(
                        200,
                        json.dumps(
                            {
                                "ok": True,
                                "mode": _ACTIVE_MODE,
                                "lane": _current_lane_label(),
                                "locked": False,
                            }
                        ),
                        "application/json",
                    )
                    return
                self._send(
                    400,
                    json.dumps({"error": "invalid mode (band or rtos)"}),
                    "application/json",
                )
            except Exception as exc:
                self._send(500, json.dumps({"error": str(exc)}), "application/json")
            return

        if path == "/api/camera_ingest":
            if STDIN_MODE:
                self._send(
                    409,
                    json.dumps({"error": "stdin mode ignores camera"}),
                    "application/json",
                )
                return
            try:
                nbytes = int(self.headers.get("Content-Length") or 0)
            except Exception:
                nbytes = 0
            try:
                raw = self.rfile.read(nbytes) if nbytes > 0 else b"{}"
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send(400, json.dumps({"error": "bad JSON"}), "application/json")
                return
            try:
                motion = float(data.get("motion", 0.0))
            except Exception:
                self._send(
                    400, json.dumps({"error": "bad motion"}), "application/json"
                )
                return
            motion = min(2.0, max(0.0, motion))
            now = time.monotonic()
            with _lock:
                _cam["motion"] = motion
                _cam["ts"] = now
            band = _cam_band_update(motion, now)
            self._send(
                200,
                json.dumps(
                    {"ok": True, "band": band, "source": _current_source(now)}
                ),
                "application/json",
            )
            return

        if path == "/api/transcribe":
            try:
                nbytes = int(self.headers.get("Content-Length") or 0)
            except Exception:
                nbytes = 0
            if nbytes <= 0:
                self._send(
                    400,
                    json.dumps({"error": "empty audio upload"}),
                    "application/json",
                )
                return
            if nbytes > MAX_AUDIO_BYTES:
                self._send(
                    413,
                    json.dumps({"error": "audio too large (cap ~2 MB)"}),
                    "application/json",
                )
                return
            try:
                raw = self.rfile.read(nbytes)
            except Exception as exc:  # noqa
                self._send(
                    400,
                    json.dumps({"error": "unreadable upload: %s" % exc}),
                    "application/json",
                )
                return
            try:
                text = transcribe_audio_bytes(raw, timeout_s=15.0)
            except TimeoutError as exc:
                self._send(504, json.dumps({"error": str(exc)}), "application/json")
                return
            except Exception as exc:  # noqa - clean JSON errors, never crash
                self._send(
                    500,
                    json.dumps({"error": "%s: %s" % (type(exc).__name__, exc)}),
                    "application/json",
                )
                return
            self._send(200, json.dumps({"text": text}), "application/json")
            return

        if path != "/api/ask":
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
                self._send(
                    400, json.dumps({"error": "missing 'q'"}), "application/json"
                )
                return
            try:
                answer, grounding = ask_question(q)
            except TimeoutError as exc:
                self._send(504, json.dumps({"error": str(exc)}), "application/json")
                return
            except Exception as exc:  # noqa - never crash the server
                self._send(
                    500,
                    json.dumps({"error": "%s: %s" % (type(exc).__name__, exc)}),
                    "application/json",
                )
                return
            try:
                # Speaker toggle: frontend sends {"tts": false} to mute speech.
                want_tts = data.get("tts", True)
                if want_tts is not False:
                    _speak_async(answer)
            except Exception:  # noqa - TTS failure never breaks the API
                pass
            self._send(
                200,
                json.dumps({"answer": answer, "grounding": grounding}),
                "application/json",
            )
        except Exception as exc:  # noqa
            try:
                self._send(500, json.dumps({"error": str(exc)}), "application/json")
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
            srv = ReusableThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            srv = None
    if srv is None:
        print("error: ports 8777 and 8778 are busy", file=sys.stderr)
        return 1
    url = "http://127.0.0.1:%d/" % port
    print(
        "KINETIQ WEB dashboard: %s  (source=%s%s)"
        % (url, _source, ", lane=%s" % LANE if LANE else ""),
        flush=True,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())