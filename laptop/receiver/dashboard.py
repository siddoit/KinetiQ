"""
KINETIQ live telemetry dashboard + session CSV logger.

Closes the gap described in docs/FINAL_BREAKDOWN.md 11.3 / 11.5:

    BLE/serial RX -> JSON validator (drop + count bad frames) -> live dashboard + CSV

Every incoming line is validated through ``TelemetryRecord.parse`` before it can
reach the view or the CSV, so a malformed frame from the firmware is *counted
and logged*, never rendered and never trusted. Standard library only: the
terminal view is raw ANSI (no curses, no colorama) and the CSV/reject writers
use plain ``open``.

Three ingestion sources
-----------------------
mock    in-process loopback over ``mock_streamer``'s ``StreamerState``/``build_packet``
        helpers -- the same pattern ``laptop/ai/assistant.py::MockIngestor`` uses,
        so the dashboard needs no extra process and no extra dependency.
stdin   newline-delimited JSON from a pipe or ``Get-Content file.txt |``.
serial  newline-delimited JSON from a pyserial port (pyserial is imported lazily,
        only inside this branch, so the mock/stdin paths work on a bare Python).

Display contract
----------------
* TTY stdout -> ~5 Hz redraw, cursor-home + clear, current activity/exertion cell
  reversed, ``[POOR QUALITY]`` in red, SpO2 shown as ``--`` whenever the PPG
  quality gate is not ``good`` (SpO2 is a still-only estimate).
* Non-TTY stdout or ``--headless`` -> no escape byte is ever written; one plain
  summary line every 2 s instead. The end-of-run summary always prints.

Usage (from the repository root, Windows PowerShell):
    python laptop\\receiver\\dashboard.py --source mock --duration 10 --headless
    python laptop\\receiver\\dashboard.py --source stdin --duration 5 --headless
    python laptop\\receiver\\dashboard.py --source serial --port COM5
    python -m laptop.receiver.dashboard --source mock --interval 0.2

Exit codes: 0 clean shutdown, 2 no packets ingested at all (or bad arguments),
3 source setup failure.
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
from typing import Any, Callable, List, Optional, Sequence, Tuple

# The telemetry contract lives next to this file; make it importable no matter
# which directory this file was launched from (Windows-safe, no POSIX tricks).
_RECEIVER_DIR = os.path.dirname(os.path.abspath(__file__))
_LAPTOP_DIR = os.path.dirname(_RECEIVER_DIR)
if _RECEIVER_DIR not in sys.path:
    sys.path.append(_RECEIVER_DIR)

try:  # package import: python -m laptop.receiver.dashboard
    from .telemetry_schema import TelemetryError, TelemetryRecord
except ImportError:  # direct script: python laptop\receiver\dashboard.py
    from telemetry_schema import TelemetryError, TelemetryRecord  # noqa: E402

DASHBOARD_TITLE = "KINETIQ LIVE TELEMETRY DASHBOARD"

#: Locked CSV header, derived from the schema's own key order (timestamp first,
#: then the 7 physiology fields) rather than hardcoded a second time.
CSV_HEADER: Tuple[str, ...] = (
    "timestamp",
    "heart_rate",
    "spo2",
    "activity",
    "motion_intensity",
    "exertion",
    "steps",
    "ppg_quality",
)
CSV_HEADER_LINE = ",".join(CSV_HEADER)

#: Displayed label prefixes are fixed strings so the frame columns never drift.
LABEL_SESSION = "Session     :"
LABEL_STATUS = "Status      :"
LABEL_ACTIVITY = "Activity State :"
LABEL_HEART = "Heart Rate     :"
LABEL_SPO2 = "SpO2           :"
LABEL_MOTION = "Motion Intensity:"
LABEL_EXERTION = "Exertion       :"
LABEL_STEPS = "Steps          :"
LABEL_PACKETS = "Packets        :"

ACTIVITY_CELLS: Tuple[str, ...] = ("REST", "WALK", "RUN", "UNKNOWN")
EXERTION_CELLS: Tuple[str, ...] = ("REST", "LIGHT", "MODERATE", "HIGH")
NO_VALUE = "--"

FRAME_INTERVAL_S = 0.2  # ~5 Hz redraw
HEADLESS_INTERVAL_S = 2.0
POLL_INTERVAL_S = 0.02
RATE_WINDOW_S = 2.0
RATE_MIN_SPAN_S = 0.5

DEFAULT_CSV_DIR = os.path.join(_LAPTOP_DIR, "logs")
DEFAULT_INTERVAL_S = 0.2
DEFAULT_PORT = "COM5"
DEFAULT_BAUD = 115200
SERIAL_POLL_S = 0.05

ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_RED = "\x1b[31m"
ANSI_INVERSE = "\x1b[7m"
ANSI_CLEAR = "\x1b[2J\x1b[H"

#: Deliberately malformed lines used by ``--inject-bad`` to exercise the drop
#: path: missing fields, not JSON, and an out-of-vocabulary enum value.
BAD_PACKETS: Sequence[str] = (
    '{"timestamp": 1}',
    "not json at all",
    (
        '{"timestamp":1750000000,"heart_rate":72,"spo2":97,"activity":"teleport",'
        '"motion_intensity":0.4,"exertion":"light","steps":1,"ppg_quality":"good"}'
    ),
)

EXIT_OK = 0
EXIT_NO_PACKETS = 2
EXIT_SOURCE_ERROR = 3

#: Queue sentinel pushed by the reader thread once its source is exhausted.
_EOF = object()


class SourceSetupError(RuntimeError):
    """Raised when a source cannot be opened or imported."""


def enable_virtual_terminal(stream: Any = None) -> bool:
    """
    Report whether ANSI escape sequences can be used on ``stream`` (stdout).

    On Windows the console has to opt in to virtual-terminal processing; without
    it every escape byte would print literally. Any failure degrades silently
    into the plain-text (headless) view rather than raising.
    """
    target = sys.stdout if stream is None else stream
    try:
        if not target.isatty():
            return False
    except (AttributeError, ValueError, OSError):
        return False

    if os.name != "nt":
        return True

    try:
        os.system("")  # belt and braces: asks conhost to enable VT for children
    except Exception:
        pass

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        std_output_handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(std_output_handle, ctypes.byref(mode)):
            return False
        return bool(
            kernel32.SetConsoleMode(
                std_output_handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
        )
    except Exception:
        return False


class SessionLogger:
    """
    Per-run CSV history plus a reject log, both stamped from local start time.

    Used as a context manager so the handles are closed (and the files flushed
    and complete) on a normal exit, on ``--duration`` expiry, on stdin EOF and
    on Ctrl+C.
    """

    def __init__(self, csv_dir: str, stamp: Optional[str] = None) -> None:
        self.stamp = stamp or time.strftime("%Y%m%d_%H%M%S")
        self.csv_dir = csv_dir
        self.csv_path = os.path.join(csv_dir, "session_{0}.csv".format(self.stamp))
        self.rejects_path = os.path.join(csv_dir, "rejects_{0}.log".format(self.stamp))
        self.rows = 0
        self.rejects = 0
        self._csv: Optional[Any] = None
        self._reject: Optional[Any] = None

    def __enter__(self) -> "SessionLogger":
        os.makedirs(self.csv_dir, exist_ok=True)
        self._csv = open(self.csv_path, "w", encoding="utf-8", newline="")
        self._csv.write(CSV_HEADER_LINE + "\n")
        self._csv.flush()
        self._reject = open(self.rejects_path, "w", encoding="utf-8", newline="")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.close()
        return False

    def close(self) -> None:
        for attribute in ("_csv", "_reject"):
            handle = getattr(self, attribute)
            if handle is None:
                continue
            try:
                handle.flush()
                handle.close()
            except (OSError, ValueError):
                pass
            setattr(self, attribute, None)

    def write(self, record: TelemetryRecord) -> None:
        """Append one already-validated record as a CSV row."""
        if self._csv is None:
            return
        data = record.to_dict()
        intensity = "{0:.2f}".format(float(data["motion_intensity"]))
        row = ",".join(
            (
                str(data["timestamp"]),
                str(data["heart_rate"]),
                str(data["spo2"]),
                str(data["activity"]),
                intensity,
                str(data["exertion"]),
                str(data["steps"]),
                str(data["ppg_quality"]),
            )
        )
        self._csv.write(row + "\n")
        self.rows += 1
        self._csv.flush()  # flush every packet: even an aborted run leaves a full CSV

    def reject(self, raw: str, error: str) -> None:
        """Record one dropped frame: the raw line plus the validation error."""
        if self._reject is None:
            return
        payload = " ".join(str(raw).split())[:500]
        detail = " ".join(str(error).split())
        self._reject.write(
            "{0} | raw={1} | error={2}\n".format(
                time.strftime("%Y-%m-%d %H:%M:%S"), payload, detail
            )
        )
        self._reject.flush()
        self.rejects += 1


class RateMeter:
    """Sliding-window packets-per-second over the validated packets only."""

    def __init__(self, window_s: float = RATE_WINDOW_S, min_span_s: float = RATE_MIN_SPAN_S) -> None:
        self.window_s = window_s
        self.min_span_s = min_span_s
        self._stamps: List[float] = []
        self._started = time.monotonic()

    def record(self, now: Optional[float] = None) -> None:
        stamp = time.monotonic() if now is None else now
        self._stamps.append(stamp)
        self._prune(stamp)

    def rate_hz(self, now: Optional[float] = None) -> float:
        stamp = time.monotonic() if now is None else now
        self._prune(stamp)
        if not self._stamps:
            return 0.0
        elapsed = stamp - self._started
        span = min(max(elapsed, self.min_span_s), self.window_s)
        return len(self._stamps) / span

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        if self._stamps and self._stamps[0] < cutoff:
            self._stamps = [stamp for stamp in self._stamps if stamp >= cutoff]


class Dashboard:
    """
    Ingest + view state for one run.

    Every line handed in by a source is validated here (on the main thread, so
    there is no shared mutable state to lock), then routed to the CSV logger and
    to the view state.
    """

    def __init__(self, source_label: str, logger: SessionLogger, ansi: bool, quiet: bool) -> None:
        self.source_label = source_label
        self.logger = logger
        self.ansi = ansi
        self.quiet = quiet
        self.latest: Optional[TelemetryRecord] = None
        self.received = 0
        self.dropped = 0
        self.rate = RateMeter()
        self.started = time.monotonic()

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started

    def ingest(self, raw: str) -> bool:
        """Validate one raw line. Returns True when it became a record."""
        try:
            record = TelemetryRecord.parse(raw)
        except ValueError as exc:  # TelemetryError is a ValueError
            self.dropped += 1
            self.logger.reject(raw, str(exc))
            return False

        self.received += 1
        self.latest = record
        self.rate.record()
        self.logger.write(record)
        return True

    def _paint(self, text: str, code: str) -> str:
        return "{0}{1}{2}".format(code, text, ANSI_RESET) if self.ansi else text

    def _choice_row(self, label: str, cells: Sequence[str], current: str) -> str:
        """Render one locked vocabulary row with the current cell highlighted."""
        rendered: List[str] = []
        for cell in cells:
            if self.ansi and current and cell == current:
                rendered.append(self._paint(cell, ANSI_INVERSE))
            else:
                rendered.append(cell)
        row = "{0} {1}".format(label, " | ".join(rendered))
        if current and current not in cells:
            # 'active' is schema-legal but is not one of the four displayed cells.
            row = "{0}  (current: {1})".format(row, current.upper())
        return row

    def _last_age_s(self, now: float) -> float:
        if self.latest is None:
            return 0.0
        return max(0.0, now - self.started)

    def _frame_lines(self, now: float) -> List[str]:
        record = self.latest
        if record is None:
            heart = NO_VALUE
            spo2 = NO_VALUE
            intensity = NO_VALUE
            steps = NO_VALUE
            activity = ""
            exertion = ""
            quality_tag = ""
            status = "waiting for telemetry..."
        else:
            poor = record.ppg_quality != "good"
            heart = "{0} BPM".format(record.heart_rate)
            spo2 = NO_VALUE if poor else "{0} %".format(record.spo2)
            intensity = "{0:.2f}".format(record.motion_intensity)
            steps = str(record.steps)
            activity = record.activity.upper()
            exertion = record.exertion.upper()
            quality_tag = (
                self._paint(" [POOR QUALITY]", ANSI_RED) if poor else ""
            )
            status = "live (last packet {0:.1f}s ago)".format(self._last_age_s(now))

        lines = [
            self._paint(DASHBOARD_TITLE, ANSI_BOLD),
            "{0} {1}".format(LABEL_SESSION, self.logger.csv_path),
            "{0} {1}".format(LABEL_STATUS, status),
            self._choice_row(LABEL_ACTIVITY, ACTIVITY_CELLS, activity),
            "{0} {1}{2}".format(LABEL_HEART, heart, quality_tag),
            "{0} {1}".format(LABEL_SPO2, spo2),
            "{0} {1}".format(LABEL_MOTION, intensity),
            self._choice_row(LABEL_EXERTION, EXERTION_CELLS, exertion),
            "{0} {1}".format(LABEL_STEPS, steps),
            "{0} recv={1} dropped={2} rate={3:.1f} Hz".format(
                LABEL_PACKETS, self.received, self.dropped, self.rate.rate_hz(now)
            ),
            "Source: {0} | Ctrl+C to exit".format(self.source_label),
        ]
        return lines

    def render_frame(self) -> None:
        """One full-screen redraw (TTY only)."""
        if not self.ansi:
            return
        frame = "\n".join(self._frame_lines(time.monotonic()))
        sys.stdout.write(ANSI_CLEAR + frame + "\n")
        sys.stdout.flush()

    def print_progress(self) -> None:
        """One plain summary line (non-TTY / --headless only)."""
        if self.ansi or self.quiet:
            return
        sys.stdout.write(self.progress_line() + "\n")
        sys.stdout.flush()

    def progress_line(self) -> str:
        record = self.latest
        rate = self.rate.rate_hz()
        if record is None:
            return (
                "[kinetiq] t={0:.1f}s waiting for telemetry... recv={1} "
                "dropped={2} rate={3:.1f} Hz".format(
                    self.elapsed_s, self.received, self.dropped, rate
                )
            )
        poor = record.ppg_quality != "good"
        return (
            "[kinetiq] t={0:.1f}s activity={1} hr={2} spo2={3} intensity={4:.2f} "
            "exertion={5} steps={6} ppg={7} recv={8} dropped={9} rate={10:.1f} Hz".format(
                self.elapsed_s,
                record.activity.upper(),
                record.heart_rate,
                NO_VALUE if poor else record.spo2,
                record.motion_intensity,
                record.exertion.upper(),
                record.steps,
                record.ppg_quality,
                self.received,
                self.dropped,
                rate,
            )
        )

    def final_summary(self) -> None:
        """Always-print end-of-run summary, ANSI-free, TTY or not."""
        if self.ansi:
            sys.stdout.write(ANSI_CLEAR)
            sys.stdout.flush()
        print(
            "[kinetiq] session summary: source={0} elapsed={1:.2f}s "
            "received={2} dropped={3} csv={4} rejects={5}".format(
                self.source_label,
                self.elapsed_s,
                self.received,
                self.dropped,
                self.logger.csv_path,
                self.logger.rejects_path,
            )
        )


def _load_mock_helpers() -> Tuple[Any, Any]:
    """Return ``mock_streamer``'s StreamerState/build_packet for either entry point."""
    try:  # package import: python -m laptop.receiver.dashboard
        from .mock_streamer import StreamerState, build_packet
    except ImportError:  # direct script: python laptop\receiver\dashboard.py
        from mock_streamer import StreamerState, build_packet
    return StreamerState, build_packet


def _bad_packet(index: int) -> str:
    return BAD_PACKETS[index % len(BAD_PACKETS)]


def _mock_producer(
    sink: "queue.Queue[Any]", stop: threading.Event, interval_s: float, inject_bad: int
) -> None:
    """Drive the synthetic streamer in-process at ``interval_s`` (assistant.py pattern)."""
    streamer_state, build_packet = _load_mock_helpers()
    state = streamer_state(interval_s=interval_s)
    first = True

    while not stop.is_set():
        sink.put(build_packet(state))
        if first:
            for index in range(max(0, inject_bad)):
                sink.put(_bad_packet(index))
            first = False
        time.sleep(interval_s)


def _stdin_producer(sink: "queue.Queue[Any]", stop: threading.Event, inject_bad: int) -> None:
    """Read newline-delimited JSON from stdin until EOF."""
    for index in range(max(0, inject_bad)):
        sink.put(_bad_packet(index))
    while not stop.is_set():
        raw = sys.stdin.readline()
        if raw == "":
            break
        line = raw.strip()
        if line:
            sink.put(line)


def _open_serial(port: str, baud: int) -> Any:
    """Open the serial port, or raise SourceSetupError with a usable message."""
    try:
        import serial
    except ImportError:
        raise SourceSetupError(
            "pyserial is not installed; run `pip install pyserial` to use --source serial"
        )

    try:
        return serial.Serial(port=port, baudrate=baud, timeout=0)
    except Exception as exc:
        raise SourceSetupError(
            "cannot open serial port {0} at {1} baud: {2}: {3}".format(
                port, baud, type(exc).__name__, exc
            )
        )


def _serial_producer(sink: "queue.Queue[Any]", stop: threading.Event, handle: Any) -> None:
    """
    Poll an open serial port for newline-delimited JSON.

    ``in_waiting`` + a poll sleep keeps every read non-blocking, so Ctrl+C is
    always responsive and a device unplugged mid-run surfaces as a clean EOF
    instead of a traceback.
    """
    buffered = bytearray()
    while not stop.is_set():
        try:
            waiting = handle.in_waiting
        except Exception:
            return
        if not waiting:
            time.sleep(SERIAL_POLL_S)
            continue
        try:
            buffered.extend(handle.read(waiting))
        except Exception:
            return

        while b"\n" in buffered:
            line, _, rest = buffered.partition(b"\n")
            buffered = bytearray(rest)
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                sink.put(text)

    tail = bytes(buffered).decode("utf-8", errors="replace").strip()
    if tail:
        sink.put(tail)


def _noop_cleanup() -> None:
    return None


def build_source(
    args: argparse.Namespace,
) -> Tuple[Callable[["queue.Queue[Any]", threading.Event], None], Callable[[], None], str]:
    """Return (producer, cleanup, label) for the requested source."""
    if args.source == "mock":
        try:
            _load_mock_helpers()
        except ImportError as exc:
            raise SourceSetupError("mock_streamer is not importable: {0}".format(exc))

        def produce(sink: "queue.Queue[Any]", stop: threading.Event) -> None:
            _mock_producer(sink, stop, args.interval, args.inject_bad)

        return produce, _noop_cleanup, "mock(interval={0:g}s)".format(args.interval)

    if args.source == "stdin":

        def produce(sink: "queue.Queue[Any]", stop: threading.Event) -> None:
            _stdin_producer(sink, stop, args.inject_bad)

        return produce, _noop_cleanup, "stdin"

    handle = _open_serial(args.port, args.baud)

    def cleanup() -> None:
        try:
            handle.close()
        except Exception:
            pass

    def produce(sink: "queue.Queue[Any]", stop: threading.Event) -> None:
        _serial_producer(sink, stop, handle)

    return produce, cleanup, "serial({0}@{1})".format(args.port, args.baud)


def _spawn_reader(
    producer: Callable[["queue.Queue[Any]", threading.Event], None],
    sink: "queue.Queue[Any]",
    stop: threading.Event,
    errors: List[str],
    done: threading.Event,
) -> threading.Thread:
    """Run one source on a daemon thread, pushing raw lines into ``sink``."""

    def _run() -> None:
        try:
            producer(sink, stop)
        except Exception as exc:
            errors.append("source thread: {0}: {1}".format(type(exc).__name__, exc))
        finally:
            sink.put(_EOF)
            done.set()

    thread = threading.Thread(target=_run, name="kinetiq-reader", daemon=True)
    thread.start()
    return thread


def _drain(
    sink: "queue.Queue[Any]", dashboard: Dashboard, done: threading.Event
) -> int:
    """Validate every buffered line; returns how many queue items were consumed."""
    handled = 0
    while True:
        try:
            item = sink.get_nowait()
        except queue.Empty:
            return handled
        handled += 1
        if item is _EOF:
            done.set()
            continue
        dashboard.ingest(item)


def run_loop(
    dashboard: Dashboard,
    producer: Callable[["queue.Queue[Any]", threading.Event], None],
    duration_s: float,
) -> List[str]:
    """Ingest until the duration expires or the source reaches EOF."""
    sink: "queue.Queue[Any]" = queue.Queue()
    stop = threading.Event()
    done = threading.Event()
    errors: List[str] = []
    _spawn_reader(producer, sink, stop, errors, done)

    deadline = time.monotonic() + duration_s if duration_s > 0.0 else None
    next_frame = time.monotonic()
    next_line = time.monotonic()

    while True:
        consumed = _drain(sink, dashboard, done)
        now = time.monotonic()

        if dashboard.ansi and now >= next_frame:
            dashboard.render_frame()
            next_frame = now + FRAME_INTERVAL_S
        elif not dashboard.ansi and not dashboard.quiet and now >= next_line:
            dashboard.print_progress()
            next_line = now + HEADLESS_INTERVAL_S

        if done.is_set() and consumed == 0 and sink.empty():
            break
        if deadline is not None and now >= deadline:
            break

        time.sleep(POLL_INTERVAL_S)

    stop.set()
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kinetiq-dashboard",
        description=(
            "KINETIQ live telemetry dashboard + session CSV logger "
            "(validates every packet against the locked schema)."
        ),
    )
    parser.add_argument(
        "--source",
        choices=("mock", "serial", "stdin"),
        default="mock",
        help="where telemetry packets come from (default mock)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help="seconds between mock packets (default {0:g})".format(DEFAULT_INTERVAL_S),
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="stop after this many seconds; 0 (default) runs until Ctrl+C or EOF",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="never write ANSI; print one plain summary line every 2 s instead",
    )
    parser.add_argument(
        "--port",
        default=DEFAULT_PORT,
        help="serial port for --source serial (default {0})".format(DEFAULT_PORT),
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=DEFAULT_BAUD,
        help="serial baud rate for --source serial (default {0})".format(DEFAULT_BAUD),
    )
    parser.add_argument(
        "--inject-bad",
        type=int,
        default=0,
        help="dev flag: inject N malformed lines at stream start (default 0)",
    )
    parser.add_argument(
        "--csv-dir",
        default=DEFAULT_CSV_DIR,
        help="directory for session CSVs and reject logs (default laptop/logs)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress periodic progress lines (the final summary always prints)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.interval <= 0.0:
        print("error: --interval must be > 0", file=sys.stderr)
        return EXIT_NO_PACKETS
    if args.inject_bad < 0:
        print("error: --inject-bad must be >= 0", file=sys.stderr)
        return EXIT_NO_PACKETS
    if args.duration < 0.0:
        print("error: --duration must be >= 0", file=sys.stderr)
        return EXIT_NO_PACKETS

    try:
        producer, cleanup, label = build_source(args)
    except SourceSetupError as exc:
        print("error: {0}".format(exc), file=sys.stderr)
        return EXIT_SOURCE_ERROR

    ansi = False
    if not args.headless:
        ansi = enable_virtual_terminal(sys.stdout)

    csv_dir = os.path.abspath(os.path.expanduser(args.csv_dir))
    logger = SessionLogger(csv_dir)
    dashboard = Dashboard(label, logger, ansi=ansi, quiet=args.quiet)
    interrupted = False

    try:
        with logger:
            print(
                "[kinetiq] source={0} session={1} view={2}".format(
                    label, logger.csv_path, "ansi" if ansi else "plain"
                )
            )
            try:
                errors = run_loop(dashboard, producer, args.duration)
            except KeyboardInterrupt:
                interrupted = True
                errors = []
            finally:
                cleanup()

            if dashboard.ansi:
                sys.stdout.write(ANSI_CLEAR)
            if interrupted:
                print("[kinetiq] interrupted by user (Ctrl+C)")
            for message in errors:
                print("[kinetiq] {0}".format(message), file=sys.stderr)
    except OSError as exc:
        print("error: cannot write session files in {0}: {1}".format(csv_dir, exc), file=sys.stderr)
        return EXIT_SOURCE_ERROR

    dashboard.final_summary()

    if dashboard.received == 0:
        print(
            "[kinetiq] no telemetry packets were ingested from {0}".format(label),
            file=sys.stderr,
        )
        return EXIT_NO_PACKETS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())