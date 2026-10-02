"""
KINETIQ sensor-grounded AI assistant.

Pipeline
--------
    telemetry packet -> ContextManager -> PromptBuilder -> LLM -> TTS

1. packet        The ESP32-S3 firmware streams one locked-schema JSON packet per
                 second over BLE (or USB serial). The receiver validates it with
                 ``TelemetryRecord.parse`` before anything downstream sees it.
2. context       ``ContextManager`` keeps the most recent validated record in a
                 thread-safe cache and exposes a 7-key payload view of it.
3. prompt        ``PromptBuilder`` embeds that payload plus the user's question
                 into a fixed system prompt, for either an OpenAI-style chat
                 completion or Ollama's flat ``/api/generate`` endpoint.
4. llm           ``AIAdvisor`` talks to a local Ollama model first and falls back
                 to a configured OpenAI-compatible chat endpoint. If neither is
                 reachable the caller can use a deterministic grounded template.
5. tts           ``TTSOutput`` speaks the reply through pyttsx3 and always echoes
                 it to the console so the pipeline is observable without audio.

Medical safety
--------------
Every metric KINETIQ produces (PPG heart rate, still-only SpO2, motion intensity,
activity class, exertion class) is a **wellness estimate**, not a medical
measurement. These figures come from a consumer wearable, are not clinically
validated, and must never be presented as a diagnosis. This module enforces
that contract twice: the system prompt forbids diagnoses, and
``verify_grounding`` fails any reply that ignores the live sensor state or does
not declare poor signal quality as unverified.

Usage (from the repository root, Windows PowerShell):
    python laptop\\ai\\assistant.py --query "How am I doing?" --no-tts
    python laptop\\ai\\assistant.py --mock-seconds 5

Third-party imports (pyttsx3, requests) are deliberately lazy so this module
always imports and the console-only loopback keeps working when the optional
speech/network dependencies are missing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

# The telemetry contract lives in laptop/receiver; make it importable no matter
# which directory this file was launched from (Windows-safe, no POSIX tricks).
_LAPTOP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RECEIVER_DIR = os.path.join(_LAPTOP_DIR, "receiver")
if os.path.isdir(_RECEIVER_DIR) and _RECEIVER_DIR not in sys.path:
    sys.path.append(_RECEIVER_DIR)

from telemetry_schema import TelemetryError, TelemetryRecord  # noqa: E402

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_OLLAMA_MODEL = "gemma2:2b"
DEFAULT_API_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_MOCK_SECONDS = 3.0
DEFAULT_MOCK_INTERVAL_S = 0.2
MAX_RESPONSE_WORDS = 60
DEFAULT_SPEECH_RATE_WPM = 185

ENV_API_URL = "KINETIQ_API_URL"
ENV_API_KEY = "KINETIQ_API_KEY"

#: The exact 7 keys an LLM payload may carry -- the locked telemetry schema
#: minus ``timestamp``, which is transport metadata rather than physiology.
PAYLOAD_KEYS: Tuple[str, ...] = (
    "heart_rate",
    "spo2",
    "activity",
    "motion_intensity",
    "exertion",
    "steps",
    "ppg_quality",
)


class AIUnavailableError(RuntimeError):
    """Raised when no LLM backend could produce a reply."""


def _validate_payload(payload: Dict[str, Any]) -> None:
    """Enforce the exact 7-key KINETIQ payload contract."""
    if not isinstance(payload, dict):
        raise ValueError(
            f"payload must be a dict, got {type(payload).__name__}"
        )

    present = set(payload.keys())
    expected = set(PAYLOAD_KEYS)
    missing = sorted(expected - present)
    unknown = sorted(present - expected)
    if missing or unknown:
        raise ValueError(
            "payload does not match the KINETIQ contract: "
            f"missing={missing} unknown={unknown} expected={list(PAYLOAD_KEYS)}"
        )


class ContextManager:
    """Thread-safe cache of the latest validated telemetry packet."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._packet: Optional[TelemetryRecord] = None
        self._packet_count: int = 0
        self._last_update: float = 0.0

    @property
    def packet_count(self) -> int:
        with self._lock:
            return self._packet_count

    @property
    def last_update(self) -> float:
        with self._lock:
            return self._last_update

    def update(self, packet: TelemetryRecord) -> None:
        """Store an already-validated record."""
        with self._lock:
            self._packet = packet
            self._packet_count += 1
            self._last_update = time.time()

    def update_from_raw(self, raw: Dict[str, Any] | str) -> TelemetryRecord:
        """Validate a raw packet (dict or JSON text), store it, and return it."""
        record = TelemetryRecord.parse(raw)
        self.update(record)
        return record

    def latest(self) -> Optional[TelemetryRecord]:
        """Return the newest record, or None if no packet has arrived yet.

        TelemetryRecord is a frozen dataclass, so handing out the reference is
        safe without copying.
        """
        with self._lock:
            return self._packet

    def payload(self) -> Optional[Dict[str, Any]]:
        """Return the 7-key LLM payload view of the newest record (no timestamp)."""
        with self._lock:
            packet = self._packet
            if packet is None:
                return None
            return {
                "heart_rate": int(packet.heart_rate),
                "spo2": int(packet.spo2),
                "activity": str(packet.activity),
                "motion_intensity": float(packet.motion_intensity),
                "exertion": str(packet.exertion),
                "steps": int(packet.steps),
                "ppg_quality": str(packet.ppg_quality),
            }

    def wait_for_packet(self, timeout: float = 5.0) -> Optional[TelemetryRecord]:
        """Poll until a packet exists or ``timeout`` seconds elapse."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._lock:
                if self._packet is not None:
                    return self._packet
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)


class PromptBuilder:
    """Turns a telemetry payload + user question into an LLM-ready prompt."""

    SYSTEM_PROMPT = (
        "You are KINETIQ, a context-aware fitness assistant. You give brief, "
        "direct responses (1-2 sentences) grounded strictly in the user's live "
        "physiological and activity metrics. Never give medical diagnoses. If "
        "signal quality is POOR, explicitly state that metrics are unverified."
    )

    def build(self, payload: Dict[str, Any], user_query: str) -> List[Dict[str, str]]:
        """Build an OpenAI-style chat message list."""
        _validate_payload(payload)
        query = (user_query or "").strip()
        body = json.dumps(payload, indent=2)
        content = (
            f"{query}\n\n"
            "Live KINETIQ telemetry (locked schema, wellness estimates only):\n"
            "```json\n"
            f"{body}\n"
            "```\n\n"
            "Answer using only these metrics. These are wellness estimates from a "
            "consumer wearable, not medical measurements, and must not be framed "
            "as a diagnosis."
        )
        return [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

    def build_generate_prompt(self, payload: Dict[str, Any], user_query: str) -> str:
        """Build one flat prompt string for Ollama's /api/generate endpoint."""
        _validate_payload(payload)
        query = (user_query or "").strip()
        body = json.dumps(payload, indent=2)
        return (
            f"{self.SYSTEM_PROMPT}\n\n"
            "--- LIVE KINETIQ TELEMETRY (JSON) ---\n"
            f"{body}\n"
            "--- END TELEMETRY ---\n\n"
            f"QUESTION: {query}\n\n"
            "ANSWER (1-2 sentences, metrics only, no diagnosis):"
        )


class AIAdvisor:
    """LLM facade: local Ollama first, OpenAI-compatible API second."""

    def __init__(
        self,
        ollama_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_OLLAMA_MODEL,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.ollama_url = ollama_url
        self.model = model
        self.api_url = api_url or os.environ.get(ENV_API_URL) or None
        self.api_key = api_key or os.environ.get(ENV_API_KEY) or None
        self.timeout = timeout
        self.last_error: str = ""
        self.last_backend: str = ""

    def advise(self, payload: Dict[str, Any], user_query: str) -> str:
        """Return a grounded reply, or raise AIUnavailableError."""
        builder = PromptBuilder()
        prompt = builder.build_generate_prompt(payload, user_query)
        messages = builder.build(payload, user_query)

        self.last_error = ""
        errors: List[str] = []

        reply = self._try_ollama(prompt)
        if reply:
            self.last_backend = "ollama"
            return reply
        errors.append(f"ollama: {self.last_error or 'no reply'}")

        if self.api_url:
            reply = self._try_api(messages)
            if reply:
                self.last_backend = "api"
                return reply
            errors.append(f"api: {self.last_error or 'no reply'}")
        else:
            errors.append(f"api: not configured (set {ENV_API_URL}/{ENV_API_KEY})")

        self.last_backend = ""
        self.last_error = " | ".join(errors)
        raise AIUnavailableError(
            "no LLM backend produced a reply -- " + self.last_error
        )

    def is_available(self) -> bool:
        """Cheap liveness probe of the local Ollama server; never raises."""
        try:
            import requests  # lazy: console path must work without network deps
        except ImportError:
            return False

        base = self.ollama_url.split("/api/")[0] or self.ollama_url
        try:
            resp = requests.get(base, timeout=1.0)
        except Exception:
            return False
        return resp.status_code < 400

    def _try_ollama(self, prompt: str) -> Optional[str]:
        try:
            import requests  # lazy import
        except ImportError as exc:
            self.last_error = f"requests not installed ({exc})"
            return None

        try:
            resp = requests.post(
                self.ollama_url,
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            text = str(resp.json().get("response", "")).strip()
        except Exception as exc:  # connection refused, HTTP error, bad JSON
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

        if not text:
            self.last_error = "empty response field"
            return None
        return text

    def _try_api(self, messages: List[Dict[str, str]]) -> Optional[str]:
        try:
            import requests  # lazy import
        except ImportError as exc:
            self.last_error = f"requests not installed ({exc})"
            return None

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = {
            "model": self.model or DEFAULT_API_MODEL,
            "messages": messages,
            "stream": False,
        }
        try:
            resp = requests.post(
                self.api_url or "", json=body, headers=headers, timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
            text = str(data["choices"][0]["message"]["content"]).strip()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

        if not text:
            self.last_error = "empty message content"
            return None
        return text


class TTSOutput:
    """pyttsx3 speech with an always-on console echo as the fallback."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._engine: Any = None
        self._rate_configured: bool = False
        self.fallback_reason: Optional[str] = None

        if not self.enabled:
            self.fallback_reason = "disabled by caller"
            return

        try:
            import pyttsx3  # lazy: never required for import or console output

            self._engine = pyttsx3.init()
        except Exception as exc:  # missing package, no audio device, COM error
            self._engine = None
            self.fallback_reason = f"{type(exc).__name__}: {exc}"

    @property
    def has_engine(self) -> bool:
        return self._engine is not None

    def speak(self, text: str) -> None:
        """Speak ``text`` if possible; always echo it to the console."""
        message = (text or "").strip()
        if not message:
            return

        print(f"[TTS] {message}")

        if not self.enabled or self._engine is None:
            return

        try:
            if not self._rate_configured:
                self._engine.setProperty("rate", DEFAULT_SPEECH_RATE_WPM)
                self._rate_configured = True
            self._engine.say(message)
            self._engine.runAndWait()
        except Exception as exc:
            # pyttsx3 can throw on repeated use on Windows; degrade permanently.
            self.fallback_reason = f"{type(exc).__name__}: {exc}"
            self._engine = None
            print(f"[TTS] speech engine disabled ({self.fallback_reason}); console only")

    def stop(self) -> None:
        """Release the speech engine; safe to call repeatedly."""
        engine = self._engine
        self._engine = None
        if engine is None:
            return
        try:
            engine.stop()
        except Exception:
            pass


class MockIngestor:
    """Feeds mock_streamer packets into a ContextManager on a worker thread."""

    def __init__(
        self,
        context: ContextManager,
        duration_s: float = DEFAULT_MOCK_SECONDS,
        interval_s: float = DEFAULT_MOCK_INTERVAL_S,
    ) -> None:
        self.context = context
        self.duration_s = max(0.0, duration_s)
        self.interval_s = max(0.01, interval_s)
        self.errors: List[str] = []
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="kinetiq-mock", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        try:
            from mock_streamer import StreamerState, build_packet
        except ImportError as exc:  # laptop/receiver not importable
            self.errors.append(f"mock_streamer unavailable: {exc}")
            return

        state = StreamerState(interval_s=self.interval_s)
        deadline = time.monotonic() + self.duration_s
        produced = 0

        while not self._stop.is_set():
            try:
                self.context.update_from_raw(build_packet(state))
                produced += 1
            except TelemetryError as exc:
                self.errors.append(f"rejected packet: {exc}")
            except Exception as exc:  # streamer bug should not kill the thread
                self.errors.append(f"{type(exc).__name__}: {exc}")

            if self.duration_s <= 0.0 or time.monotonic() >= deadline:
                break
            if self._stop.wait(self.interval_s):
                break

        if produced:
            print(f"[mock] produced {produced} telemetry packet(s)")


def template_reply(payload: Dict[str, Any]) -> str:
    """Deterministic, payload-grounded reply used when no LLM is reachable."""
    _validate_payload(payload)
    activity = payload["activity"]
    exertion = payload["exertion"]
    heart_rate = payload["heart_rate"]
    steps = payload["steps"]
    intensity = float(payload["motion_intensity"])
    spo2 = payload["spo2"]
    quality = payload["ppg_quality"]

    parts = [
        f"You're currently {activity} at {exertion} exertion with heart rate "
        f"{heart_rate} BPM ({steps} steps).",
        f"Motion intensity is {intensity:.2f} and the still-only SpO2 estimate is "
        f"{spo2}%.",
    ]
    if quality == "poor":
        parts.append(
            "Signal quality is poor, so these metrics are unverified estimates, "
            "not medical measurements."
        )
    return " ".join(parts)


def verify_grounding(response: str, payload: Dict[str, Any]) -> List[str]:
    """Return the list of FAILED grounding checks (empty list means grounded)."""
    text = (response or "").strip()
    lowered = text.lower()
    failures: List[str] = []

    exertion = str(payload.get("exertion", "")).strip().lower()
    if exertion and exertion not in lowered:
        failures.append(
            f"exertion grounding: response does not mention live exertion '{exertion}'"
        )

    activity = str(payload.get("activity", "")).strip().lower()
    if activity and activity not in lowered:
        failures.append(
            f"activity grounding: response does not mention live activity '{activity}'"
        )

    quality = str(payload.get("ppg_quality", "")).strip().lower()
    if quality == "poor" and not any(
        marker in lowered for marker in ("unverified", "poor")
    ):
        failures.append(
            "signal-quality grounding: ppg_quality is 'poor' but the response neither "
            "calls the metrics unverified nor mentions the poor signal"
        )

    words = len(text.split())
    if words > MAX_RESPONSE_WORDS:
        failures.append(
            f"brevity contract: response is {words} words, limit is {MAX_RESPONSE_WORDS}"
        )

    return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kinetiq-assistant",
        description=(
            "KINETIQ loopback: ingest mock telemetry, build a grounded prompt, "
            "query an LLM backend, verify grounding, and speak the reply."
        ),
    )
    parser.add_argument(
        "--query",
        default=None,
        help="ask this instead of prompting (enables non-interactive testing)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="answer a single question and exit (implied when --query is given)",
    )
    parser.add_argument(
        "--no-tts",
        action="store_true",
        help="print the reply but skip speech synthesis",
    )
    parser.add_argument(
        "--mock-seconds",
        type=float,
        default=DEFAULT_MOCK_SECONDS,
        help=f"seconds of mock telemetry to ingest (default {DEFAULT_MOCK_SECONDS:g})",
    )
    parser.add_argument(
        "--mock-interval",
        type=float,
        default=DEFAULT_MOCK_INTERVAL_S,
        help=f"seconds between mock packets (default {DEFAULT_MOCK_INTERVAL_S:g})",
    )
    parser.add_argument(
        "--ollama-url",
        default=DEFAULT_OLLAMA_URL,
        help=f"Ollama /api/generate endpoint (default {DEFAULT_OLLAMA_URL})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_OLLAMA_MODEL,
        help=f"local model name (default {DEFAULT_OLLAMA_MODEL})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"LLM request timeout in seconds (default {DEFAULT_TIMEOUT_S:g})",
    )
    return parser


def _read_query(query: Optional[str], once: bool) -> Optional[str]:
    """Return the question to ask, or None when the caller should quit."""
    if query is not None:
        text = query.strip()
        if not text:
            print("error: --query must not be empty", file=sys.stderr)
            return None
        return text

    if once:
        print("error: --once requires --query", file=sys.stderr)
        return None

    if not sys.stdin.isatty():
        print(
            "error: stdin is not a terminal; pass --query for a non-interactive run",
            file=sys.stderr,
        )
        return None

    try:
        text = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return text or None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the loopback pipeline. Exit 0 = grounded, 1 = grounding failed."""
    args = build_parser().parse_args(argv)

    context = ContextManager()
    advisor = AIAdvisor(
        ollama_url=args.ollama_url, model=args.model, timeout=args.timeout
    )
    tts = TTSOutput(enabled=not args.no_tts)
    ingestor = MockIngestor(
        context, duration_s=args.mock_seconds, interval_s=args.mock_interval
    )

    if tts.fallback_reason:
        print(f"[tts] console fallback: {tts.fallback_reason}")

    print(f"[llm] ollama={args.ollama_url} model={args.model}")
    print(
        f"[mock] ingesting {args.mock_seconds:g}s of telemetry "
        f"every {args.mock_interval:g}s"
    )
    ingestor.start()

    status = 0
    try:
        first = context.wait_for_packet(timeout=10.0)
        if first is None:
            print(
                "error: no telemetry received; check the mock streamer import path",
                file=sys.stderr,
            )
            return 2
        for problem in ingestor.errors:
            print(f"[mock] {problem}", file=sys.stderr)
        print(
            f"[telemetry] activity={first.activity} exertion={first.exertion} "
            f"hr={first.heart_rate} spo2={first.spo2} "
            f"intensity={first.motion_intensity:.2f} steps={first.steps} "
            f"ppg_quality={first.ppg_quality}"
        )

        single_shot = args.once or args.query is not None
        while True:
            query = _read_query(args.query, args.once)
            if query is None:
                break

            packet = context.latest()
            if packet is None:
                print("error: telemetry context is empty", file=sys.stderr)
                status = 2
                break

            payload = context.payload()
            if payload is None:
                print("error: telemetry payload unavailable", file=sys.stderr)
                status = 2
                break

            print(f"[payload] {json.dumps(payload)}")
            try:
                reply = advisor.advise(payload, query)
                print(f"[llm] reply via {advisor.last_backend}: {reply}")
            except AIUnavailableError as exc:
                print(
                    "LLM unavailable (Ollama down, no API key) -- showing grounded "
                    "template reply"
                )
                print(f"[llm] detail: {exc}")
                reply = template_reply(payload)

            failures = verify_grounding(reply, payload)
            if failures:
                print(f"[grounding] FAIL ({len(failures)} check(s)):")
                for failure in failures:
                    print(f"  - {failure}")
                status = 1
            else:
                print("[grounding] PASS")

            tts.speak(reply)

            if single_shot:
                break
    except KeyboardInterrupt:
        print("\ninterrupted by user", file=sys.stderr)
    finally:
        ingestor.stop()
        tts.stop()

    return status


if __name__ == "__main__":
    raise SystemExit(main())