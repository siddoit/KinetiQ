"""
KINETIQ mock telemetry streamer.

Generates a synthetic, schema-valid telemetry stream at 1 Hz so the laptop
pipeline (STT -> LLM -> TTS assist path, context injection, logging) can be
developed and demoed without the ESP32-S3 wearable attached.

Physiology model (believable-synthetic until real parts arrive):
  - Activity state machine cycles rest -> walk -> run -> walk -> recover
    over a ~67 s loop with fixed dwell times (>= 10 s per phase), so the
    activity label never flickers. The internal "recover" phase is emitted
    as "rest" because the locked schema has no "recover" activity value --
    the story is told by the still-elevated, falling heart rate.
  - Heart rate follows a first-order lag toward a phase target
    (rise tau ~7 s, recovery tau ~15 s), so HR visibly LAGS activity
    changes and keeps falling after motion stops, plus +/-2.5 BPM HRV noise.
  - Exertion is the trailing ~30 s mean of motion intensity mapped onto the
    locked rest/light/moderate/high vocabulary, so it stays high briefly
    into recovery while HR falls -- a story the assistant can narrate.

Every generated packet uses the exact locked KINETIQ telemetry schema and is
validated through TelemetryRecord.parse before being printed, so a streamer
regression shows up immediately instead of reaching the LLM prompt builder.

Usage (from the repository root, Windows PowerShell):
    python laptop\\receiver\\mock_streamer.py --count 3
    python laptop\\receiver\\mock_streamer.py --count 0 --interval 1.0

Output is one compact JSON packet per line on stdout, which is byte-for-byte
the same wire format the firmware emits from comm_thread.
"""

from __future__ import annotations

import argparse
import collections
import math
import random
import sys
import time
from typing import Any, Deque, Dict, List, Optional, Sequence

try:  # package import: python -m laptop.receiver.mock_streamer
    from .telemetry_schema import TelemetryError, TelemetryRecord
except ImportError:  # direct script: python laptop\receiver\mock_streamer.py
    from telemetry_schema import TelemetryError, TelemetryRecord

EPOCH_BASE_S = 1750000000
DEFAULT_INTERVAL_S = 1.0

ACTIVITY_CYCLE: Sequence[str] = ("rest", "walk", "run")
DWELL_SAMPLES = 12

# ---- Believable-physiology parameters (tune here, schema stays frozen) ----
HR_BASELINE = 68.0          # resting heart rate (BPM)
HR_OFFSET_WALK = 26.0       # walk target = 94 BPM  (spec: +20-30)
HR_OFFSET_RUN = 84.0        # run target  = 152 BPM (spec: +70-90)
HR_TAU_RISE_S = 7.0         # first-order lag while HR climbs (spec 6-8 s)
HR_TAU_FALL_S = 15.0        # first-order lag while HR recovers (spec 12-18 s)
HR_NOISE_BPM = 2.5          # HRV-ish noise band, +/- BPM (spec 2-3)

# Internal phase schedule: (phase, dwell seconds). Loop = 67 s, min dwell 10 s.
PHASE_SCHEDULE: Sequence[tuple] = (
    ("rest", 15.0),
    ("walk", 12.0),
    ("run", 15.0),
    ("walk", 10.0),
    ("recover", 15.0),
)
LOOP_S = sum(duration for _, duration in PHASE_SCHEDULE)

# Internal phases -> emitted schema activity ("recover" is not a schema value,
# so it is emitted as "rest"; the elevated, falling HR tells the story).
EMIT_ACTIVITY = {"rest": "rest", "walk": "walk", "run": "run", "recover": "rest"}

BASE_HEART_RATE = {"rest": 68, "walk": 94, "run": 152, "recover": 68}
BASE_INTENSITY = {"rest": 0.05, "walk": 0.45, "run": 0.88, "recover": 0.08}
STEP_RATE_PER_S = {"rest": 0.0, "walk": 1.6, "run": 2.8, "recover": 0.0}
SPO2_ESTIMATE = 97

EXERTION_WINDOW_S = 30.0    # trailing window for the smoothed exertion story
SPO2_DIP_S = 4.0            # transient SpO2 dip length after run onset
SPO2_DIP_VALUE = 95


class StreamerState:
    """Evolving synthetic wearer state (activity, heart rate, cadence)."""

    def __init__(
        self,
        interval_s: float,
        start_steps: int = 4218,
        seed: Optional[int] = None,
    ) -> None:
        self.interval_s = interval_s
        self.index = 0
        self.elapsed = 0.0
        self.steps = float(start_steps)
        self.activity = ACTIVITY_CYCLE[0]
        self.phase = "rest"
        self.hr = HR_BASELINE
        self.intensity_window: Deque[float] = collections.deque()
        self.rng = random.Random(seed)

    def _locate_phase(self) -> tuple:
        """Return (phase name, seconds elapsed within the phase)."""
        t_loop = self.elapsed % LOOP_S
        boundary = 0.0
        for name, duration in PHASE_SCHEDULE:
            if t_loop < boundary + duration:
                return name, t_loop - boundary
            boundary += duration
        return PHASE_SCHEDULE[0][0], 0.0

    def advance(self) -> str:
        """Move to the next sample slot and return the activity for it."""
        dt = self.interval_s
        phase, t_in_phase = self._locate_phase()
        self.phase = phase
        self.activity = EMIT_ACTIVITY[phase]

        # Heart rate: first-order lag toward the phase target (HR lags motion).
        target = BASE_HEART_RATE[phase]
        tau = HR_TAU_RISE_S if target > self.hr else HR_TAU_FALL_S
        alpha = 1.0 - math.exp(-dt / tau)
        self.hr += (target - self.hr) * alpha

        # Motion intensity: state-driven level + noise, near-zero at rest.
        level = BASE_INTENSITY[phase]
        sample = level + self.rng.uniform(-0.03, 0.03)
        if phase in ("rest", "recover"):
            sample += 0.015 * math.sin(0.7 * self.elapsed)  # tiny fidget
        sample = min(1.0, max(0.0, sample))
        self.intensity_window.append(sample)
        window_max = max(1, int(round(EXERTION_WINDOW_S / dt)))
        while len(self.intensity_window) > window_max:
            self.intensity_window.popleft()
        self.intensity = sample
        self.phase_time = t_in_phase

        # Steps integrate only while walking/running at realistic cadence.
        self.steps += STEP_RATE_PER_S[phase] * dt

        self.elapsed += dt
        self.index += 1
        return self.activity


def derive_exertion(heart_rate: int, activity: str) -> str:
    """Map heart rate + activity onto the locked exertion vocabulary."""
    if activity == "rest" and heart_rate < 80:
        return "rest"
    if heart_rate < 100:
        return "light"
    if heart_rate < 130:
        return "moderate"
    return "high"


def exertion_from_trailing_intensity(window: Deque[float]) -> str:
    """Map the trailing ~30 s mean intensity onto the exertion vocabulary."""
    if not window:
        return "rest"
    mean_level = sum(window) / len(window)
    if mean_level < 0.12:
        return "rest"
    if mean_level < 0.30:
        return "light"
    if mean_level < 0.55:
        return "moderate"
    return "high"


def build_packet(state: StreamerState) -> Dict[str, Any]:
    """Build one schema-shaped telemetry dict from the current state."""
    activity = state.advance()
    t = state.elapsed

    heart_rate = int(round(state.hr + state.rng.uniform(-HR_NOISE_BPM, HR_NOISE_BPM)))
    if heart_rate < 30:
        heart_rate = 30
    elif heart_rate > 220:
        heart_rate = 220

    intensity = state.intensity

    # SpO2 96-99 with a slight transient dip just after run onset.
    if state.phase == "run" and state.phase_time < SPO2_DIP_S:
        spo2 = SPO2_DIP_VALUE
    elif state.phase == "run":
        spo2 = 97
    elif state.phase == "walk":
        spo2 = 98 if state.rng.random() < 0.7 else 97
    else:
        spo2 = 98 if state.rng.random() < 0.8 else 97

    # Motion artifact degrades PPG quality during high motion.
    ppg_quality = "good"
    if state.phase == "run":
        if state.rng.random() < 0.30:
            ppg_quality = "poor"
    elif state.phase == "walk":
        if state.rng.random() < 0.05:
            ppg_quality = "poor"

    return {
        "timestamp": EPOCH_BASE_S + int(t),
        "heart_rate": heart_rate,
        "spo2": spo2,
        "activity": activity,
        "motion_intensity": round(intensity, 2),
        "exertion": exertion_from_trailing_intensity(state.intensity_window),
        "steps": int(state.steps),
        "ppg_quality": ppg_quality,
    }


def stream(
    count: int, interval_s: float, seed: Optional[int] = None
) -> List[TelemetryRecord]:
    """Emit validated packets; count == 0 means run until interrupted."""
    state = StreamerState(interval_s=interval_s, seed=seed)
    emitted: List[TelemetryRecord] = []
    deadline_base = time.monotonic()

    while True:
        if count > 0 and len(emitted) >= count:
            break

        packet = build_packet(state)
        record = TelemetryRecord.parse(packet)
        print(record.to_json(), flush=True)
        emitted.append(record)

        target = deadline_base + (len(emitted) * interval_s)
        delay = target - time.monotonic()
        if delay > 0.0:
            time.sleep(delay)

    return emitted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mock_streamer",
        description="Generate a synthetic KINETIQ telemetry stream (1 packet per second).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="number of packets to emit; 0 (default) runs until interrupted",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help=f"seconds between packets (default {DEFAULT_INTERVAL_S})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="optional RNG seed for a reproducible demo stream",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the end-of-run summary on stderr",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.count < 0:
        print("error: --count must be >= 0", file=sys.stderr)
        return 2
    if args.interval <= 0.0:
        print("error: --interval must be > 0", file=sys.stderr)
        return 2

    try:
        emitted = stream(args.count, args.interval, seed=args.seed)
    except TelemetryError as exc:
        print(f"schema validation failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted by user", file=sys.stderr)
        return 0

    if not args.quiet and emitted:
        last = emitted[-1]
        print(
            f"emitted {len(emitted)} packet(s); last: "
            f"activity={last.activity} hr={last.heart_rate} "
            f"intensity={last.motion_intensity:.2f} "
            f"exertion={last.exertion} steps={last.steps} "
            f"ppg_quality={last.ppg_quality}",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
