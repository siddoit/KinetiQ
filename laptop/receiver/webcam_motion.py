"""
"The mirror sees you" -- laptop webcam drives OUR physiology stream.

The default webcam watches the presenter, a frame-difference motion score
(EMA-smoothed, tau ~3 s) maps to an activity label (rest/walk/run), and that
label drives ``mock_streamer``'s physiology state via ``StreamerState`` +
``build_packet`` -- so heart rate (first-order lag), steps, exertion, SpO2 and
PPG quality respond EXACTLY like the mock story, only the activity source is
a live human instead of the 67 s phase schedule.

Output is raw newline-delimited JSON, one schema-valid packet per second on
stdout -- byte-for-byte the wire format ``dashboard.py --source stdin``
expects (it just does ``sys.stdin.readline()`` per line; no framing, no
length prefix). Every packet is re-validated through
``TelemetryRecord.parse`` before printing, so a bug here surfaces as a
non-zero exit, never as broken JSON downstream.

Usage (from the repository root, Windows PowerShell -- ZERO new installs):

    # Terminal A (produce): uses cv2 if importable, else PyAV dshow (preinstalled)
    laptop\\.venv\\Scripts\\python.exe laptop\\receiver\\webcam_motion.py --camera 0
    # Terminal B -- OR single-terminal pipe (from laptop\\receiver):
    python webcam_motion.py | ..\\.venv\\Scripts\\python.exe dashboard.py --source stdin --headless

Capture priority: (a) cv2.VideoCapture(index) when opencv is importable,
(b) PyAV dshow ``video=<device name>`` where <device name> is the --camera-th
video device from ``ffmpeg -list_devices true -f dshow -i dummy`` (default 0,
usually "Integrated Camera" / "USB2.0 HD UVC WebCam").

Flags: --camera N (default 0), --interval S (emit period, default 1.0),
--count N (0 = run until Ctrl+C), --tau S (EMA time constant, default 3.0),
--walk-t / --run-t (motion thresholds, defaults 3.0 / 10.0, mean abs-diff
on 0-255 downscaled grayscale), --dwell S (label must persist this long
before switching, default 2.0), --show (preview window, press q to quit).

Camera failure (missing opencv, no device, permission denied) prints a clear
error to STDERR and exits non-zero; stdout then contains zero lines, so the
dashboard reports "no packets" instead of ingesting garbage.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from typing import Any, Optional, Sequence

try:  # package import: python -m laptop.receiver.webcam_motion
    from .mock_streamer import StreamerState, build_packet
except ImportError:  # direct script: python laptop\\receiver\\webcam_motion.py
    from mock_streamer import StreamerState, build_packet  # noqa: E402

try:  # package import: python -m laptop.receiver.webcam_motion
    from .telemetry_schema import TelemetryError, TelemetryRecord
except ImportError:  # direct script: python laptop\\receiver\\webcam_motion.py
    from telemetry_schema import TelemetryError, TelemetryRecord  # noqa: E402

try:
    import cv2 as _cv2
except ImportError:
    _cv2 = None
cv2: Any = _cv2  # Any-typed so static analysis stays quiet when opencv is absent

try:
    import av as _av
except ImportError:
    _av = None
av: Any = _av

try:
    import numpy as _np
except ImportError:
    _np = None
np: Any = _np

import re
import shutil
import subprocess

EXIT_OK = 0
EXIT_SOURCE_ERROR = 3

DEFAULT_CAMERA = 0
DEFAULT_INTERVAL_S = 1.0
DEFAULT_TAU_S = 3.0
DEFAULT_WALK_T = 3.0
DEFAULT_RUN_T = 10.0
DEFAULT_DWELL_S = 2.0
CAPTURE_FPS = 15.0
SMALL_WIDTH = 160

PRIVACY_HINT = "Settings -> Privacy -> Camera -> allow desktop apps"

_DEVICE_LINE_RE = re.compile(r'"([^"]+)"\s*\(video\)')


def list_dshow_video_devices() -> list:
    """Parse video device names from ``ffmpeg -list_devices true -f dshow``."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        return []
    try:
        proc = subprocess.run(
            [exe, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
    except Exception:
        return []
    return _DEVICE_LINE_RE.findall(proc.stdout or "")


def classify(score: float, walk_t: float, run_t: float) -> str:
    """Map a smoothed motion score onto the schema activity vocabulary."""
    if score >= run_t:
        return "run"
    if score >= walk_t:
        return "walk"
    return "rest"


def frame_score(prev_small: Any, gray_small: Any) -> float:
    """Mean absolute pixel difference between two downscaled gray frames."""
    if cv2 is not None:
        diff = cv2.absdiff(prev_small, gray_small)
        return float(diff.mean())
    return float(
        np.abs(prev_small.astype(np.int16) - gray_small.astype(np.int16)).mean()
    )


def bgr_to_gray(frame: Any) -> Any:
    """BGR (cv2) frame -> grayscale; passes 2-D gray frames through."""
    if frame is None:
        raise ValueError("empty frame")
    if getattr(frame, "ndim", 3) == 2:
        return frame
    if cv2 is not None:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    b = frame[:, :, 0].astype(np.float32)
    g = frame[:, :, 1].astype(np.float32)
    r = frame[:, :, 2].astype(np.float32)
    return (0.114 * b + 0.587 * g + 0.299 * r).astype(np.uint8)


def downscale(gray: Any) -> Any:
    """Shrink grayscale to SMALL_WIDTH wide (cheap + noise-robust)."""
    height, width = gray.shape[:2]
    if width <= SMALL_WIDTH:
        return gray
    if cv2 is not None:
        scale = SMALL_WIDTH / float(width)
        return cv2.resize(
            gray, (SMALL_WIDTH, max(1, int(height * scale))), interpolation=cv2.INTER_AREA
        )
    step = max(1, int(math.ceil(width / float(SMALL_WIDTH))))
    return gray[::step, ::step]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webcam_motion",
        description="Drive the KINETIQ physiology stream from webcam motion.",
    )
    parser.add_argument("--camera", type=int, default=DEFAULT_CAMERA)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--tau", type=float, default=DEFAULT_TAU_S)
    parser.add_argument("--walk-t", type=float, default=DEFAULT_WALK_T)
    parser.add_argument("--run-t", type=float, default=DEFAULT_RUN_T)
    parser.add_argument("--dwell", type=float, default=DEFAULT_DWELL_S)
    parser.add_argument("--show", action="store_true")
    return parser


class _Cv2Capture:
    """Thin wrapper so cv2 and PyAV captures share read()/release()."""

    backend = "cv2"

    def __init__(self, cap: Any) -> None:
        self._cap = cap

    def read(self) -> tuple:
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return False, None
        return True, frame

    def release(self) -> None:
        try:
            self._cap.release()
        except Exception:
            pass


class _PyAvCapture:
    """PyAV dshow capture: decode video stream, yield grayscale ndarrays."""

    backend = "pyav-dshow"

    def __init__(self, container: Any, device_name: str) -> None:
        self._container = container
        self.device_name = device_name
        self._iterator = container.decode(video=0)

    def read(self) -> tuple:
        try:
            frame = next(self._iterator)
        except StopIteration:
            return False, None
        except Exception:
            return False, None
        try:
            gray = frame.to_ndarray(format="gray")
        except Exception:
            return False, None
        return True, gray

    def release(self) -> None:
        try:
            self._container.close()
        except Exception:
            pass


def _open_cv2(index: int) -> Any:
    cap = cv2.VideoCapture(index)
    if cap is None or not cap.isOpened():
        raise RuntimeError(
            "cv2.VideoCapture({0}) failed: device missing, index wrong "
            "(try --camera 1), or OS permission denied".format(index)
        )
    return _Cv2Capture(cap)


def _open_pyav(index: int) -> Any:
    if av is None:
        raise RuntimeError("PyAV (av) is not installed")
    devices = list_dshow_video_devices()
    if not devices:
        raise RuntimeError(
            "PyAV dshow found no video devices (ffmpeg -list_devices true "
            "-f dshow -i dummy returned none)"
        )
    if index < 0 or index >= len(devices):
        raise RuntimeError(
            "camera index {0} out of range for dshow video devices {1}".format(
                index, devices
            )
        )
    name = devices[index]
    try:
        container = av.open("video={0}".format(name), format="dshow")
    except Exception as exc:
        raise RuntimeError(
            "cannot open dshow device 'video={0}': {1}".format(name, exc)
        )
    return _PyAvCapture(container, name)


def open_camera(index: int) -> Any:
    """Open the webcam via cv2 if importable, else PyAV dshow.

    Raises RuntimeError listing everything tried + the Windows camera-privacy
    hint, so a headless failure is actionable without new installs.
    """
    tried: list = []
    if cv2 is not None:
        try:
            return _open_cv2(index)
        except RuntimeError as exc:
            tried.append("cv2: {0}".format(exc))
    else:
        tried.append("cv2: not importable (opencv not installed; skipping)")
    try:
        return _open_pyav(index)
    except RuntimeError as exc:
        tried.append("pyav-dshow: {0}".format(exc))
    raise RuntimeError(
        "cannot open camera {0}; tried [{1}]. Check the device is plugged in, "
        "close apps holding the camera, and allow desktop access at {2}".format(
            index, "; ".join(tried), PRIVACY_HINT
        )
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.interval <= 0.0 or args.tau <= 0.0 or args.dwell < 0.0:
        print("error: --interval/--tau must be > 0, --dwell >= 0", file=sys.stderr)
        return 2
    if args.count < 0:
        print("error: --count must be >= 0", file=sys.stderr)
        return 2
    if np is None:
        print("error: numpy is required (preinstalled in the laptop venv)", file=sys.stderr)
        return EXIT_SOURCE_ERROR
    if getattr(args, "show", False) and cv2 is None:
        print("warning: --show needs opencv; ignoring preview", file=sys.stderr)
        args.show = False

    try:
        cap = open_camera(args.camera)
    except RuntimeError as exc:
        print("error: {0}".format(exc), file=sys.stderr)
        return EXIT_SOURCE_ERROR

    # Physiology state borrowed wholesale from the mock: we override ONLY the
    # phase locator so build_packet's HR lag / steps / exertion / SpO2 / PPG
    # story follows the camera instead of the 67 s schedule.
    state = StreamerState(interval_s=args.interval)
    driven = {"phase": "rest", "t_in_phase": 0.0}

    def _locate() -> tuple:
        return driven["phase"], driven["t_in_phase"]

    state._locate_phase = _locate  # type: ignore[method-assign]
    phase_started = time.monotonic()

    ema: Optional[float] = None
    activity = "rest"
    pending = "rest"
    pending_since = time.monotonic()
    prev_small: Optional[Any] = None
    last_emit = time.monotonic()
    emitted = 0
    frame_dt = 1.0 / CAPTURE_FPS
    alpha_frame = 1.0 - math.exp(-frame_dt / args.tau)

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("error: camera feed lost mid-run", file=sys.stderr)
                return EXIT_SOURCE_ERROR

            gray_small = downscale(bgr_to_gray(frame))
            if prev_small is not None and prev_small.shape == gray_small.shape:
                raw = frame_score(prev_small, gray_small)
                ema = raw if ema is None else ema + (raw - ema) * alpha_frame

                target = classify(ema, args.walk_t, args.run_t)
                now = time.monotonic()
                if target != activity:
                    if target != pending:
                        pending, pending_since = target, now
                    if now - pending_since >= args.dwell:
                        activity = target
                        pending_since = now
                else:
                    pending, pending_since = activity, now
            prev_small = gray_small

            if args.show:
                cv2.imshow("kinetiq webcam_motion (q to quit) -- {0}".format(activity), frame)  # type: ignore[union-attr]
                if cv2.waitKey(1) & 0xFF == ord("q"):  # type: ignore[union-attr]
                    break

            now = time.monotonic()
            if now - last_emit >= args.interval and ema is not None:
                last_emit = now
                if driven["phase"] != activity:
                    driven["phase"] = activity
                    phase_started = now
                driven["t_in_phase"] = now - phase_started
                try:
                    packet = build_packet(state)
                    print(TelemetryRecord.parse(packet).to_json(), flush=True)
                except TelemetryError as exc:
                    print("schema validation failed: {0}".format(exc), file=sys.stderr)
                    return 1
                emitted += 1
                if args.count > 0 and emitted >= args.count:
                    break

            time.sleep(frame_dt)
    except KeyboardInterrupt:
        print("\ninterrupted by user", file=sys.stderr)
        return EXIT_OK
    finally:
        try:
            cap.release()
        except Exception:
            pass
        if args.show:
            try:
                cv2.destroyAllWindows()  # type: ignore[union-attr]
            except Exception:
                pass
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
