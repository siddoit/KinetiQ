"""KINETIQ browser mirror dashboard (single file, stdlib only).

Engine mode (default): background thread ticks mock_streamer at 1 Hz.
Stdin mode (--stdin): read JSON lines from stdin, validate, ignore non-JSON.

Usage:
    python laptop\\web\\app.py
    python laptop\\web\\app.py --stdin
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
    from mock_streamer import StreamerState, build_packet
except ImportError:
    StreamerState = None  # type: ignore
    build_packet = None  # type: ignore

try:
    from telemetry_schema import TelemetryRecord
except ImportError:
    TelemetryRecord = None  # type: ignore

STDIN_MODE = "--stdin" in sys.argv[1:]

_lock = threading.Lock()
_latest = None  # type: ignore
_history = []  # type: ignore  # list of full packet dicts, max 90
_rejects = 0
_start_t = time.monotonic()
_source = "NATIVE NODE" if STDIN_MODE else "MOCK ENGINE"

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
                    eng.setProperty("rate", 175)
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


# ---- feeds
def _store(packet_dict):
    global _latest, _rejects
    with _lock:
        _latest = packet_dict
        _history.append(packet_dict)
        while len(_history) > 90:
            _history.pop(0)


def _engine_loop():
    state = StreamerState(interval_s=1.0)
    while True:
        t0 = time.monotonic()
        try:
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
        except Exception:  # noqa - bad frame counted, never rendered
            with _lock:
                _rejects += 1


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>KINETIQ Mirror</title>
<style>
body{background:#0a0e14;color:#e6edf3;font-family:Segoe UI,Arial,sans-serif;margin:0}
header{display:flex;justify-content:space-between;align-items:center;padding:12px 24px;background:#11161f;border-bottom:2px solid #1f6feb}
h1{font-size:28px;margin:0}
.badge{font-size:20px;font-weight:bold;padding:6px 18px;border-radius:20px}
#src{background:#123}
#clock{font-size:22px}
main{display:grid;grid-template-columns:2fr 1fr;gap:16px;padding:16px 24px}
.card{background:#11161f;border-radius:12px;padding:16px}
#activity{font-size:84px;font-weight:900;text-align:center;margin:8px 0}
.rest{color:#3fb950}.walk{color:#d29922}.run{color:#f85149}
#hr{font-size:120px;font-weight:900;text-align:center;line-height:1}
.row{display:flex;gap:16px;justify-content:space-around;text-align:center;font-size:24px}
.big{font-size:44px;font-weight:bold}
.bar{height:26px;background:#222;border-radius:6px;overflow:hidden}
#mbar{height:100%;background:#1f6feb;width:0%}
#chat{display:flex;flex-direction:column;height:520px}
#msgs{flex:1;overflow-y:auto;background:#0d1117;border-radius:8px;padding:10px;font-size:19px}
.m{margin:8px 0;padding:8px 12px;border-radius:8px;background:#161b22}
.g{font-size:13px;font-weight:bold;padding:2px 10px;border-radius:10px;margin-left:8px}
.pass{background:#0f3d1f;color:#3fb950}.fail{background:#4d1113;color:#f85149}
#qin{display:flex;gap:8px;margin-top:8px}
#q{flex:1;font-size:19px;padding:10px;border-radius:8px;border:1px solid #333;background:#0d1117;color:#fff}
#send{font-size:19px;padding:10px 22px;border-radius:8px;background:#1f6feb;color:#fff;border:none}
#mic,#spk{font-size:19px;padding:10px 14px;border-radius:8px;background:#161b22;color:#fff;border:1px solid #333}
#mic.rec{background:#4d1113;border-color:#f85149}
footer{padding:10px 24px;font-size:14px;color:#8b949e}
</style>
</head>
<body>
<header>
<h1>KINETIQ &mdash; the mirror that sees and answers</h1>
<div><span class="badge" id="src">...</span> <span id="clock">--:--:--</span></div>
</header>
<main>
<div>
<div class="card"><div id="activity" class="rest">REST</div>
<div id="hr">--</div><div style="text-align:center;font-size:22px">HEART RATE (BPM)</div>
<canvas id="spark" width="640" height="120" style="width:100%;height:120px"></canvas></div>
<div class="card" style="margin-top:16px"><div class="row">
<div>SpO2<div class="big" id="spo2">--</div></div>
<div>Exertion<div class="big" id="ex">--</div></div>
<div>Steps<div class="big" id="steps">--</div></div>
<div>PPG<div class="big" id="ppg">--</div></div>
</div>
<div style="font-size:22px;margin-top:8px">Motion <span id="mv">0.00</span></div>
<div class="bar"><div id="mbar"></div></div></div>
</div>
 <div class="card" id="chat"><h2 style="margin:0 0 8px">Ask the mirror</h2>
 <div id="msgs"><div class="m">🪞 I can see your live telemetry &mdash; ask me how you&rsquo;re doing.</div></div>
 <div id="qin"><input id="q" placeholder="How am I doing?"><button id="mic" title="Voice input (mic)">🎤</button><button id="spk" title="Spoken replies on/off">🔊</button><button id="send">Send</button></div>
 </div>
</main>
<footer>Sensors simulated (physiology engine / camera-motion / virtual IMU) &middot; answers verified against live telemetry</footer>
<script>
var hrs=[];
function draw(){var c=document.getElementById('spark'),x=c.getContext('2d');x.clearRect(0,0,c.width,c.height);if(hrs.length<2)return;var mn=Math.min.apply(0,hrs),mx=Math.max.apply(0,hrs);if(mx-mn<5){mx+=2.5;mn-=2.5;}x.strokeStyle='#1f6feb';x.lineWidth=3;x.beginPath();for(var i=0;i<hrs.length;i++){var px=i/(59)*c.width,py=c.height-((hrs[i]-mn)/(mx-mn))*(c.height-10)-5;if(i==0)x.moveTo(px,py);else x.lineTo(px,py);}x.stroke();}
function tick(){fetch('/api/telemetry').then(function(r){return r.json()}).then(function(d){var L=d.latest;if(!L)return;document.getElementById('src').textContent=d.source;var a=(L.activity||'--').toUpperCase(),el=document.getElementById('activity');el.textContent=a;el.className=a==='REST'?'rest':(a==='WALK'?'walk':'run');document.getElementById('hr').textContent=L.heart_rate;document.getElementById('spo2').textContent=L.spo2;document.getElementById('ex').textContent=L.exertion;document.getElementById('steps').textContent=L.steps;document.getElementById('ppg').textContent=L.ppg_quality;document.getElementById('mv').textContent=Number(L.motion_intensity).toFixed(2);document.getElementById('mbar').style.width=(L.motion_intensity*100)+'%';hrs=d.history.slice(-60);draw();});}
setInterval(function(){document.getElementById('clock').textContent=new Date().toLocaleTimeString();},1000);
setInterval(tick,1000);tick();
function send(){var i=document.getElementById('q');sendText(i.value.trim());i.value='';}
function sendText(q){if(!q)return;var m=document.getElementById('msgs');m.innerHTML+='<div class="m">🧑 '+q.replace(/</g,'&lt;')+'</div>';fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:q,tts:ttsOn})}).then(function(r){return r.json()}).then(function(d){var g=d.grounding||'FAIL',cls=g==='PASS'?'pass':'fail';m.innerHTML+='<div class="m">🪞 '+((d.answer||d.error||'?').replace(/</g,'&lt;'))+'<span class="g '+cls+'">'+g+'</span></div>';m.scrollTop=m.scrollHeight;});}
var ttsOn=true,recorder=null,chunks=[],recTimer=null;
document.getElementById('spk').onclick=function(){ttsOn=!ttsOn;this.textContent=ttsOn?'🔊':'🔇';};
function micHint(t){var m=document.getElementById('msgs');m.innerHTML+='<div class="m">🎤 '+t+' — type instead, text chat keeps working.</div>';m.scrollTop=m.scrollHeight;}
function stopMic(){if(recTimer){clearTimeout(recTimer);recTimer=null;}if(recorder&&recorder.state!=='inactive'){try{recorder.stop();}catch(e){}}var b=document.getElementById('mic');b.classList.remove('rec');b.textContent='🎤';}
document.getElementById('mic').onclick=function(){
 if(recorder&&recorder.state!=='inactive'){stopMic();return;}
 if(!window.MediaRecorder||!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){micHint('no microphone in this browser');return;}
 var btn=this;
 navigator.mediaDevices.getUserMedia({audio:true}).then(function(stream){
  chunks=[];try{recorder=new MediaRecorder(stream,{mimeType:'audio/webm'});}catch(e){recorder=new MediaRecorder(stream);}
  recorder.ondataavailable=function(e){if(e.data&&e.data.size)chunks.push(e.data);};
  recorder.onstop=function(){stream.getTracks().forEach(function(t){t.stop();});if(!chunks.length){micHint('heard nothing, try again');return;}var blob=new Blob(chunks,{type:'audio/webm'});var m=document.getElementById('msgs');m.innerHTML+='<div class="m">🎤 transcribing…</div>';m.scrollTop=m.scrollHeight;fetch('/api/transcribe',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:blob}).then(function(r){return r.json()}).then(function(d){if(d.text){m.innerHTML+='<div class="m">🧑 🎤 '+(d.text.replace(/</g,'&lt;'))+'</div>';sendText(d.text);}else{micHint('could not hear that ('+(d.error||'?')+')');}m.scrollTop=m.scrollHeight;}).catch(function(){micHint('transcription failed');});};
  recorder.start();btn.classList.add('rec');btn.textContent='⏹';
  recTimer=setTimeout(stopMic,10000);
 }).catch(function(){micHint('mic blocked — allow microphone permission, or type instead');});
};
document.getElementById('send').onclick=send;
document.getElementById('q').onkeydown=function(e){if(e.key==='Enter')send();};
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
            body = {"latest": latest, "history": hist, "source": _source,
                    "rejects": rej, "uptime_s": int(time.monotonic() - _start_t)}
            self._send(200, json.dumps(body), "application/json")
        elif self.path.split("?")[0] in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        else:
            self._send(404, '{"error":"not found"}', "application/json")

    def do_POST(self):
        path = self.path.split("?")[0]
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
    if STDIN_MODE:
        threading.Thread(target=_stdin_loop, daemon=True).start()
    else:
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
    print("KINETIQ WEB dashboard: %s  (source=%s)" % (url, _source), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
