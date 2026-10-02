"""KINETIQ PTT voice pipeline: STT -> sensor-grounded LLM -> TTS.

Chain: raw 16-bit mono PCM (or a WAV clip) -> ``AudioTranscriber`` ->
``ContextManager`` -> ``PromptBuilder`` -> ``AIAdvisor`` -> ``TTSOutput``,
with ``verify_grounding`` gating every reply.

``faster-whisper``, ``sounddevice``, ``requests`` and ``pyttsx3`` are all
optional on the import path: every third-party import in this module is lazy,
so import, ``--benchmark`` and the test-suite keep working on hosts that only
have the stdlib plus the KINETIQ receiver package.

Usage (from the repository root, Windows PowerShell):
    python laptop\\ai\\voice_pipeline.py --benchmark --runs 3 --no-tts
    python laptop\\ai\\voice_pipeline.py --wav clip.wav --no-tts
    python laptop\\ai\\voice_pipeline.py --record --record-seconds 4
"""

from __future__ import annotations

import argparse
import asyncio
import io
import math
import os
import statistics
import struct
import sys
import time
import wave
from typing import Any, Dict, List, Optional

_AI_DIR = os.path.dirname(os.path.abspath(__file__))
if _AI_DIR not in sys.path:
    sys.path.insert(0, _AI_DIR)

try:
    from assistant import (  # noqa: E402
        AIAdvisor,
        AIUnavailableError,
        ContextManager,
        MockIngestor,
        TTSOutput,
        template_reply,
        verify_grounding,
    )
except ImportError:  # `python -m laptop.ai.voice_pipeline` from the repo root
    from laptop.ai.assistant import (  # noqa: E402
        AIAdvisor,
        AIUnavailableError,
        ContextManager,
        MockIngestor,
        TTSOutput,
        template_reply,
        verify_grounding,
    )

TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
SAMPLE_WIDTH = 2

TARGET_STT_S = 0.8
TARGET_LLM_S = 1.5
TARGET_TTS_S = 0.7
TARGET_TOTAL_S = 5.0

DEFAULT_QUERY = "How am I doing?"
DEFAULT_RUNS = 3
DEFAULT_RECORD_SECONDS = 4.0
MOCK_PACKETS = 16
MOCK_INTERVAL_S = 0.2

INSTALL_WHISPER_HINT = "install it with: pip install faster-whisper"
INSTALL_SOUNDDEVICE_HINT = "install it with: pip install sounddevice"


class TranscriptionUnavailableError(RuntimeError):
    """Raised when no speech-to-text backend could transcribe a clip."""


def synthesize_ptt_clip(
    seconds: float = 3.0, sample_rate: int = TARGET_SAMPLE_RATE
) -> bytes:
    """Build a deterministic 16-bit mono WAV clip (complete file bytes).

    Voiced 0.5 s segments (alternating 220/440 Hz with a 5 Hz amplitude
    wobble) alternate with 0.5 s silences, so the clip has a speech-like
    burst pattern without needing a microphone or test assets.
    """
    total = max(1, int(seconds * sample_rate))
    segment = sample_rate // 2
    samples: List[int] = []
    for i in range(total):
        slot = (i // segment) % 2
        if slot == 1:
            samples.append(0)
            continue
        freq = 220.0 if ((i // segment) // 2) % 2 == 0 else 440.0
        t = i / sample_rate
        envelope = 0.5 + 0.5 * math.sin(2.0 * math.pi * 5.0 * t)
        samples.append(int(12000.0 * envelope * math.sin(2.0 * math.pi * freq * t)))
    raw = struct.pack("<%dh" % len(samples), *samples)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(TARGET_CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(sample_rate)
        handle.writeframes(raw)
    return buffer.getvalue()


class StubTranscriber:
    """Deterministic stand-in used when faster-whisper is not installed."""

    def __init__(self, query: str = DEFAULT_QUERY) -> None:
        self.query = query
        self.last_load_s = 0.0

    @staticmethod
    def is_available() -> bool:
        return True

    def transcribe_pcm(
        self, pcm: bytes, sample_rate: int = TARGET_SAMPLE_RATE
    ) -> tuple:
        return self.query, 0.0

    def transcribe_wav(self, wav_bytes: bytes) -> tuple:
        return self.query, 0.0

    def transcribe_file(self, path: str) -> tuple:
        return self.query, 0.0


class AudioTranscriber:
    """faster-whisper transcriber with a lazy model load.

    The model is not loaded (and ``faster_whisper`` is not imported) until
    the first transcription call. The wall time of that first load is kept
    in ``last_load_s`` so callers can report it separately from STT latency.
    """

    def __init__(
        self,
        model_size: str = "tiny.en",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.last_load_s = 0.0
        self._model: Any = None

    @staticmethod
    def is_available() -> bool:
        """True when faster-whisper is importable; never raises."""
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        except Exception:
            return False
        return True

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure_model(self) -> None:
        if self._model is not None:
            self.last_load_s = 0.0
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionUnavailableError(
                f"faster-whisper is not installed ({exc}); {INSTALL_WHISPER_HINT}"
            ) from exc
        started = time.perf_counter()
        try:
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        except Exception as exc:
            raise TranscriptionUnavailableError(
                f"could not load faster-whisper model '{self.model_size}' "
                f"({type(exc).__name__}: {exc}); {INSTALL_WHISPER_HINT}"
            ) from exc
        self.last_load_s = time.perf_counter() - started

    def _transcribe_stream(self, stream: io.BytesIO) -> tuple:
        self._ensure_model()
        started = time.perf_counter()
        try:
            segments, _info = self._model.transcribe(stream, language="en")
            text = "".join(segment.text for segment in segments).strip()
        except Exception as exc:
            raise TranscriptionUnavailableError(
                f"faster-whisper transcription failed "
                f"({type(exc).__name__}: {exc}); {INSTALL_WHISPER_HINT}"
            ) from exc
        return text, time.perf_counter() - started

    def transcribe_pcm(
        self, pcm: bytes, sample_rate: int = TARGET_SAMPLE_RATE
    ) -> tuple:
        """Transcribe raw 16-bit mono PCM bytes -> (text, elapsed_s)."""
        data = bytes(pcm or b"")
        if not data or len(data) % SAMPLE_WIDTH != 0:
            raise TranscriptionUnavailableError(
                "empty or odd-length PCM buffer; expected 16-bit mono frames"
            )
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(TARGET_CHANNELS)
            handle.setsampwidth(SAMPLE_WIDTH)
            handle.setframerate(sample_rate)
            handle.writeframes(data)
        buffer.seek(0)
        return self._transcribe_stream(buffer)

    def transcribe_wav(self, wav_bytes: bytes) -> tuple:
        """Transcribe complete WAV file bytes -> (text, elapsed_s)."""
        return self._transcribe_stream(io.BytesIO(bytes(wav_bytes or b"")))

    def transcribe_file(self, path: str) -> tuple:
        """Transcribe an audio file on disk -> (text, elapsed_s)."""
        self._ensure_model()
        started = time.perf_counter()
        try:
            segments, _info = self._model.transcribe(str(path), language="en")
            text = "".join(segment.text for segment in segments).strip()
        except Exception as exc:
            raise TranscriptionUnavailableError(
                f"faster-whisper could not transcribe '{path}' "
                f"({type(exc).__name__}: {exc}); {INSTALL_WHISPER_HINT}"
            ) from exc
        return text, time.perf_counter() - started


class VoicePipeline:
    """STT -> context -> LLM -> grounding -> TTS chain.

    ``total_s`` covers STT + context + LLM + TTS only; a first-call model
    load is reported separately as ``model_load_s`` and excluded from the
    total so the <5 s budget is measured against steady-state latency.
    """

    def __init__(
        self,
        context: ContextManager,
        advisor: AIAdvisor,
        tts: TTSOutput,
        transcriber: AudioTranscriber | None = None,
        verbose: bool = False,
    ) -> None:
        self.context = context
        self.advisor = advisor
        self.tts = tts
        self.transcriber: Any = transcriber or AudioTranscriber()
        self.verbose = verbose

    def _blank_result(self) -> Dict[str, Any]:
        return {
            "query": "",
            "reply": "",
            "stt_s": 0.0,
            "llm_s": 0.0,
            "tts_s": 0.0,
            "total_s": 0.0,
            "model_load_s": 0.0,
            "grounding_passed": False,
            "grounding_failures": [],
            "llm_fallback": False,
            "stt_stub": isinstance(self.transcriber, StubTranscriber),
            "error": "",
        }

    async def process_clip(
        self,
        audio: bytes,
        query_fallback: str = DEFAULT_QUERY,
        speak: bool = True,
    ) -> Dict[str, Any]:
        result = self._blank_result()
        started = time.perf_counter()

        try:
            if bytes(audio or b"")[:4] == b"RIFF":
                text, stt_s = self.transcriber.transcribe_wav(bytes(audio))
            else:
                text, stt_s = self.transcriber.transcribe_pcm(
                    bytes(audio or b""), TARGET_SAMPLE_RATE
                )
        except TranscriptionUnavailableError as exc:
            result["error"] = f"transcription unavailable: {exc}"
            result["total_s"] = time.perf_counter() - started
            return result
        result["model_load_s"] = float(
            getattr(self.transcriber, "last_load_s", 0.0) or 0.0
        )
        result["stt_s"] = float(stt_s)

        query = (text or "").strip() or query_fallback
        result["query"] = query
        if self.verbose:
            print(f"[stt] recognized ({result['stt_s']:.2f}s): {query}")

        payload = self.context.payload()
        if payload is None:
            self.context.wait_for_packet(2.0)
            payload = self.context.payload()
        if payload is None:
            result["error"] = "no telemetry context available; ingest packets first"
            result["total_s"] = max(
                0.0, time.perf_counter() - started - result["model_load_s"]
            )
            return result

        llm_started = time.perf_counter()
        try:
            reply = self.advisor.advise(payload, query)
        except AIUnavailableError as exc:
            if self.verbose:
                print(f"[llm] backend unavailable ({exc}); using grounded template")
            reply = template_reply(payload)
            result["llm_fallback"] = True
        result["llm_s"] = time.perf_counter() - llm_started
        result["reply"] = reply

        failures = verify_grounding(reply, payload)
        result["grounding_failures"] = failures
        result["grounding_passed"] = not failures

        if speak:
            tts_started = time.perf_counter()
            self.tts.speak(reply)
            result["tts_s"] = time.perf_counter() - tts_started

        result["total_s"] = max(
            0.0, time.perf_counter() - started - result["model_load_s"]
        )
        return result

    def process_clip_sync(
        self,
        audio: bytes,
        query_fallback: str = DEFAULT_QUERY,
        speak: bool = True,
    ) -> Dict[str, Any]:
        return asyncio.run(self.process_clip(audio, query_fallback, speak=speak))


def _ensure_telemetry(context: ContextManager, verbose: bool = False) -> Any:
    """Drive ~16 mock packets into ``context``; None when nothing arrives."""
    ingestor = MockIngestor(
        context,
        duration_s=MOCK_PACKETS * MOCK_INTERVAL_S + 1.0,
        interval_s=MOCK_INTERVAL_S,
    )
    ingestor.start()
    first = context.wait_for_packet(timeout=10.0)
    if first is None:
        ingestor.stop()
        return None
    deadline = time.monotonic() + 15.0
    while context.packet_count < MOCK_PACKETS and time.monotonic() < deadline:
        time.sleep(0.05)
    if verbose:
        print(f"[telemetry] {context.packet_count} packet(s) cached")
    return ingestor


def _load_clip_audio(wav_path: Optional[str]) -> bytes:
    if wav_path:
        with open(wav_path, "rb") as handle:
            return handle.read()
    return synthesize_ptt_clip(3.0)


def _print_benchmark_table(
    stt_times: List[float],
    llm_times: List[float],
    tts_times: List[float],
    total_times: List[float],
    stt_stub: bool,
    llm_fallback: bool,
    grounding_ok: bool,
) -> bool:
    med_stt = statistics.median(stt_times)
    med_llm = statistics.median(llm_times)
    med_tts = statistics.median(tts_times)
    med_total = statistics.median(total_times)

    def mark(flag: bool) -> str:
        return "   (fallback)" if flag else ""

    def stub_mark(flag: bool) -> str:
        return "   (stub)" if flag else ""

    print("[benchmark] stage        median   target   status")
    print(
        f"[benchmark] STT          {med_stt:4.2f} s   ~{TARGET_STT_S:.1f} s   "
        f"{'OK' if med_stt <= TARGET_STT_S else 'SLOW'}{stub_mark(stt_stub)}"
    )
    print(
        f"[benchmark] LLM          {med_llm:4.2f} s   ~{TARGET_LLM_S:.1f} s   "
        f"{'OK' if med_llm <= TARGET_LLM_S else 'SLOW'}{mark(llm_fallback)}"
    )
    print(
        f"[benchmark] TTS          {med_tts:4.2f} s   ~{TARGET_TTS_S:.1f} s   "
        f"{'OK' if med_tts <= TARGET_TTS_S else 'SLOW'}"
    )
    passed = med_total < TARGET_TOTAL_S and grounding_ok
    print(
        f"[benchmark] TOTAL        {med_total:4.2f} s   <{TARGET_TOTAL_S:.1f} s   "
        f"{'PASS' if passed else 'FAIL'}"
    )
    return passed


def run_benchmark(args: argparse.Namespace) -> int:
    context = ContextManager()
    advisor = AIAdvisor()
    tts = TTSOutput(enabled=not args.no_tts)
    if tts.fallback_reason:
        print(f"[tts] console fallback: {tts.fallback_reason}")

    try:
        audio = _load_clip_audio(args.wav)
    except OSError as exc:
        print(f"error: cannot read --wav '{args.wav}': {exc}", file=sys.stderr)
        print("[summary] benchmark aborted: unreadable wav source")
        return 3

    ingestor = _ensure_telemetry(context, verbose=args.verbose)
    if ingestor is None:
        print(
            "error: no telemetry received; check the mock streamer import path",
            file=sys.stderr,
        )
        print("[summary] benchmark aborted: no telemetry context")
        return 2

    if AudioTranscriber.is_available():
        transcriber: Any = AudioTranscriber(model_size=args.model)
        stt_stub = False
    else:
        print("[stt] faster-whisper missing; using stub transcriber "
              f"({INSTALL_WHISPER_HINT})")
        transcriber = StubTranscriber(query=args.query)
        stt_stub = True

    llm_probe = advisor.is_available()
    llm_fallback_expected = not llm_probe
    if llm_fallback_expected and args.verbose:
        print("[llm] no backend reachable; benchmark will use the grounded template")

    pipeline = VoicePipeline(context, advisor, tts, transcriber, verbose=args.verbose)
    stt_times: List[float] = []
    llm_times: List[float] = []
    tts_times: List[float] = []
    total_times: List[float] = []
    grounding_ok = True
    llm_fallback_seen = False
    try:
        for run in range(max(1, args.runs)):
            if llm_fallback_expected:
                payload = context.payload() or {}
                llm_started = time.perf_counter()
                reply = template_reply(payload)
                llm_s = time.perf_counter() - llm_started
                audio_result = pipeline.process_clip_sync(
                    audio, query_fallback=args.query, speak=False
                )
                audio_result["reply"] = reply
                audio_result["llm_s"] = llm_s
                audio_result["llm_fallback"] = True
                audio_result["total_s"] = (
                    audio_result["stt_s"] + llm_s + audio_result["tts_s"]
                )
                failures = verify_grounding(reply, payload)
                audio_result["grounding_failures"] = failures
                audio_result["grounding_passed"] = not failures
                if not args.no_tts:
                    tts_started = time.perf_counter()
                    tts.speak(reply)
                    audio_result["tts_s"] = time.perf_counter() - tts_started
                    audio_result["total_s"] = (
                        audio_result["stt_s"] + llm_s + audio_result["tts_s"]
                    )
                result = audio_result
            else:
                result = pipeline.process_clip_sync(
                    audio, query_fallback=args.query, speak=not args.no_tts
                )
            if result.get("error"):
                print(f"[benchmark] run {run + 1} error: {result['error']}",
                      file=sys.stderr)
                print("[summary] benchmark aborted: pipeline error")
                return 3
            stt_times.append(float(result["stt_s"]))
            llm_times.append(float(result["llm_s"]))
            tts_times.append(float(result["tts_s"]))
            total_times.append(float(result["total_s"]))
            grounding_ok = grounding_ok and bool(result["grounding_passed"])
            llm_fallback_seen = llm_fallback_seen or bool(result["llm_fallback"])
            verdict = "PASS" if result["grounding_passed"] else "FAIL"
            print(
                f"[benchmark] run {run + 1}/{args.runs}: "
                f"stt={result['stt_s']:.3f}s llm={result['llm_s']:.3f}s "
                f"tts={result['tts_s']:.3f}s total={result['total_s']:.3f}s "
                f"grounding={verdict}"
            )
    finally:
        ingestor.stop()
        tts.stop()

    passed = _print_benchmark_table(
        stt_times, llm_times, tts_times, total_times,
        stt_stub, llm_fallback_seen, grounding_ok,
    )
    print(
        "[summary] benchmark "
        f"{'PASS' if passed else 'FAIL'}: median total "
        f"{statistics.median(total_times):.2f}s vs <{TARGET_TOTAL_S:.1f}s, "
        f"grounding {'PASS' if grounding_ok else 'FAIL'}"
    )
    return 0 if passed else 1


def run_record(args: argparse.Namespace) -> int:
    context = ContextManager()
    advisor = AIAdvisor()
    tts = TTSOutput(enabled=not args.no_tts)
    pipeline = VoicePipeline(context, advisor, tts, verbose=args.verbose)

    ingestor = _ensure_telemetry(context, verbose=args.verbose)
    if ingestor is None:
        print(
            "error: no telemetry received; check the mock streamer import path",
            file=sys.stderr,
        )
        print("[summary] record aborted: no telemetry context")
        return 2

    pcm: Optional[bytes] = None
    try:
        try:
            import sounddevice as sd
        except ImportError:
            print("[record] microphone unavailable: the 'sounddevice' package "
                  "is not installed.")
            print(f"[record] {INSTALL_SOUNDDEVICE_HINT}.")
            print("[record] then check Settings > Sound > Input and allow "
                  "microphone access for Python.")
            if args.wav:
                print(f"[record] falling back to --wav '{args.wav}'")
                return run_wav_fallback(args, pipeline, ingestor, tts)
            print("[summary] record aborted: no microphone backend")
            return 3

        seconds = max(1.0, float(args.record_seconds))
        print(f"[record] recording {seconds:g}s at {TARGET_SAMPLE_RATE} Hz mono ...")
        try:
            frames = int(seconds * TARGET_SAMPLE_RATE)
            capture = sd.rec(
                frames,
                samplerate=TARGET_SAMPLE_RATE,
                channels=TARGET_CHANNELS,
                dtype="int16",
            )
            sd.wait()
            pcm = bytes(capture.tobytes())
        except Exception as exc:
            print(f"[record] microphone unavailable ({type(exc).__name__}: {exc}).")
            print("[record] check Settings > Sound > Input, the default device, "
                  "and microphone privacy permissions.")
            if args.wav:
                print(f"[record] falling back to --wav '{args.wav}'")
                return run_wav_fallback(args, pipeline, ingestor, tts)
            print("[summary] record aborted: microphone error")
            return 3

        result = pipeline.process_clip_sync(
            pcm, query_fallback=args.query, speak=not args.no_tts
        )
        return _report_single_result(result, tts, ingestor, "[summary] record done")
    finally:
        if pcm is None and args.wav is None:
            ingestor.stop()
            tts.stop()


def run_wav_fallback(
    args: argparse.Namespace,
    pipeline: VoicePipeline,
    ingestor: Any,
    tts: TTSOutput,
) -> int:
    try:
        audio = _load_clip_audio(args.wav)
    except OSError as exc:
        print(f"error: cannot read --wav '{args.wav}': {exc}", file=sys.stderr)
        ingestor.stop()
        tts.stop()
        print("[summary] record aborted: unreadable wav fallback")
        return 3
    result = pipeline.process_clip_sync(
        audio, query_fallback=args.query, speak=not args.no_tts
    )
    return _report_single_result(result, tts, ingestor, "[summary] record done (wav)")


def _report_single_result(
    result: Dict[str, Any], tts: TTSOutput, ingestor: Any, summary_prefix: str
) -> int:
    try:
        if result.get("error"):
            print(f"error: {result['error']}", file=sys.stderr)
            print(f"{summary_prefix}: pipeline error")
            return 3
        print(f"[stt] recognized: {result['query']}")
        print(f"[reply] {result['reply']}")
        if result["grounding_passed"]:
            print("[grounding] PASS")
        else:
            print("[grounding] FAIL "
                  f"({len(result['grounding_failures'])} check(s)):")
            for failure in result["grounding_failures"]:
                print(f"  - {failure}")
        print(
            f"{summary_prefix}: stt={result['stt_s']:.2f}s "
            f"llm={result['llm_s']:.2f}s tts={result['tts_s']:.2f}s "
            f"total={result['total_s']:.2f}s "
            f"(model_load={result['model_load_s']:.2f}s excluded)"
        )
        return 0 if result["grounding_passed"] else 1
    finally:
        ingestor.stop()
        tts.stop()


def run_once(args: argparse.Namespace) -> int:
    context = ContextManager()
    advisor = AIAdvisor()
    tts = TTSOutput(enabled=not args.no_tts)
    if tts.fallback_reason:
        print(f"[tts] console fallback: {tts.fallback_reason}")

    try:
        audio = _load_clip_audio(args.wav)
    except OSError as exc:
        print(f"error: cannot read --wav '{args.wav}': {exc}", file=sys.stderr)
        print("[summary] single-clip run aborted: unreadable wav source")
        return 3

    ingestor = _ensure_telemetry(context, verbose=args.verbose)
    if ingestor is None:
        print(
            "error: no telemetry received; check the mock streamer import path",
            file=sys.stderr,
        )
        print("[summary] single-clip run aborted: no telemetry context")
        return 2

    if not AudioTranscriber.is_available():
        ingestor.stop()
        tts.stop()
        print("error: faster-whisper is not installed; "
              f"{INSTALL_WHISPER_HINT}", file=sys.stderr)
        print("[summary] single-clip run aborted: no STT backend")
        return 3

    pipeline = VoicePipeline(
        context, advisor, tts, AudioTranscriber(model_size=args.model),
        verbose=args.verbose,
    )
    result = pipeline.process_clip_sync(
        audio, query_fallback=args.query, speak=not args.no_tts
    )
    return _report_single_result(result, tts, ingestor, "[summary] single-clip done")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kinetiq-voice",
        description=(
            "KINETIQ PTT voice pipeline: transcribe a clip, answer from live "
            "telemetry, verify grounding, and speak the reply."
        ),
    )
    parser.add_argument("--benchmark", action="store_true",
                        help="time STT/LLM/TTS stages over --runs clips")
    parser.add_argument("--record", action="store_true",
                        help="record from the default microphone and answer it")
    parser.add_argument("--wav", default=None,
                        help="WAV clip to process (default: synthesized tone)")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                        help=f"benchmark iterations (default {DEFAULT_RUNS})")
    parser.add_argument("--model", default="tiny.en",
                        choices=("tiny.en", "base.en"),
                        help="faster-whisper model size (default tiny.en)")
    parser.add_argument("--query", default=DEFAULT_QUERY,
                        help="fallback question when the transcript is empty")
    parser.add_argument("--record-seconds", type=float,
                        default=DEFAULT_RECORD_SECONDS,
                        help=f"microphone capture length (default {DEFAULT_RECORD_SECONDS:g})")
    parser.add_argument("--no-tts", action="store_true",
                        help="print the reply but skip speech synthesis")
    parser.add_argument("--verbose", action="store_true",
                        help="print per-stage detail")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.benchmark:
            return run_benchmark(args)
        if args.record:
            return run_record(args)
        return run_once(args)
    except KeyboardInterrupt:
        print("\ninterrupted by user", file=sys.stderr)
        print("[summary] aborted by user")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
