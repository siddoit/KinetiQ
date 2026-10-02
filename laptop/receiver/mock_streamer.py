"""
KINETIQ mock telemetry streamer.

Generates a synthetic, schema-valid telemetry stream at 1 Hz so the laptop
pipeline (STT -> LLM -> TTS assist path, context injection, logging) can be
developed and demoed without the ESP32-S3 wearable attached.

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
import math
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

try:  # package import: python -m laptop.receiver.mock_streamer
    from .telemetry_schema import TelemetryError, TelemetryRecord
except ImportError:  # direct script: python laptop\receiver\mock_streamer.py
    from telemetry_schema import TelemetryError, TelemetryRecord

EPOCH_BASE_S = 1750000000
DEFAULT_INTERVAL_S = 1.0

ACTIVITY_CYCLE: Sequence[str] = ("rest", "walk", "run")
DWELL_SAMPLES = 12

BASE_HEART_RATE = {"rest": 68, "walk": 104, "run": 152}
BASE_INTENSITY = {"rest": 0.08, "walk": 0.46, "run": 0.88}
STEP_RATE_PER_S = {"rest": 0.0, "walk": 2.0, "run": 3.2}
SPO2_ESTIMATE = 97


class StreamerState:
    """Evolving synthetic wearer state (activity, heart rate, cadence)."""

    def __init__(self, interval_s: float, start_steps: int = 4218) -> None:
        self.interval_s = interval_s
        self.index = 0
        self.steps = float(start_steps)
        self.activity = ACTIVITY_CYCLE[0]

    def advance(self) -> str:
        """Move to the next sample slot and return the activity for it."""
        slot = self.index % (DWELL_SAMPLES * len(ACTIVITY_CYCLE))
        self.activity = ACTIVITY_CYCLE[slot // DWELL_SAMPLES]

        self.steps += STEP_RATE_PER_S[self.activity] * self.interval_s

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


def build_packet(state: StreamerState) -> Dict[str, Any]:
    """Build one schema-shaped telemetry dict from the current state."""
    activity = state.advance()
    t = state.index * state.interval_s

    heart_rate = int(BASE_HEART_RATE[activity] + (5.0 * math.sin(0.35 * t)))
    if heart_rate < 30:
        heart_rate = 30
    elif heart_rate > 220:
        heart_rate = 220

    intensity = BASE_INTENSITY[activity] + (0.04 * math.sin(0.9 * t))
    if intensity < 0.0:
        intensity = 0.0
    elif intensity > 1.0:
        intensity = 1.0

    # Motion artifact degrades PPG quality now and then while running.
    ppg_quality = "good"
    if activity == "run" and (state.index % 11) == 0:
        ppg_quality = "poor"

    return {
        "timestamp": EPOCH_BASE_S + int(t),
        "heart_rate": heart_rate,
        "spo2": SPO2_ESTIMATE,
        "activity": activity,
        "motion_intensity": round(intensity, 2),
        "exertion": derive_exertion(heart_rate, activity),
        "steps": int(state.steps),
        "ppg_quality": ppg_quality,
    }


def stream(count: int, interval_s: float) -> List[TelemetryRecord]:
    """Emit validated packets; count == 0 means run until interrupted."""
    state = StreamerState(interval_s=interval_s)
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
        emitted = stream(args.count, args.interval)
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
