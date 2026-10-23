#!/usr/bin/env python3
"""KINETIQ recorded band replay tool.

Replays genuine recorded Huawei Band 6 biometrics from a CSV or JSONL capture
onto stdout in the locked KINETIQ telemetry schema (1 Hz).

Usage:
    python laptop/receiver/replay_band.py --input "Tharun work/Huawei-kinetiq-band6/backend/data/kinetiq_wearable.csv"
    python laptop/receiver/replay_band.py --input "Tharun work/Huawei-kinetiq-band6/backend/data/kinetiq_wearable.csv" | python laptop/web/app.py --stdin --lane band
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from telemetry_schema import TelemetryRecord
    from mock_streamer import derive_exertion
except ImportError:
    TelemetryRecord = None
    derive_exertion = None


def parse_args():
    parser = argparse.ArgumentParser(description="Replay recorded band biometrics to stdout")
    parser.add_argument("--input", "-i", type=str, default="", help="Path to input CSV or JSONL capture")
    parser.add_argument("--rate", "-r", type=float, default=1.0, help="Playback interval in seconds (default 1.0)")
    parser.add_argument("--loop", action="store_true", help="Loop playback indefinitely")
    return parser.parse_args()


def find_default_csv():
    # 1. Teammate data log
    root = os.path.abspath(os.path.join(_HERE, "..", ".."))
    teammate_csv = os.path.join(root, "Tharun work", "Huawei-kinetiq-band6", "backend", "data", "kinetiq_wearable.csv")
    if os.path.isfile(teammate_csv):
        return teammate_csv
    # 2. Local laptop logs
    logs_dir = os.path.join(root, "laptop", "logs")
    if os.path.isdir(logs_dir):
        for f in os.listdir(logs_dir):
            if f.endswith(".csv"):
                return os.path.join(logs_dir, f)
    return None


def replay_csv(filepath: str, rate: float, loop: bool):
    print(f"[replay] Replaying recorded biometrics from {filepath} (rate: {rate}s, loop: {loop})", file=sys.stderr, flush=True)
    while True:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                hr_raw = row.get("heart_rate_bpm") or row.get("heart_rate")
                spo2_raw = row.get("spo2_percent") or row.get("spo2")
                steps_raw = row.get("steps") or 0
                if not hr_raw or not spo2_raw:
                    continue
                try:
                    hr = int(float(hr_raw))
                    spo2 = int(round(float(spo2_raw)))
                    steps = int(float(steps_raw or 0))
                except (ValueError, TypeError):
                    continue

                if hr < 30 or hr > 220 or spo2 < 70 or spo2 > 100:
                    continue

                exert = derive_exertion(hr, "unknown") if derive_exertion else "light"
                pkt = {
                    "timestamp": int(time.time()),
                    "heart_rate": hr,
                    "spo2": spo2,
                    "activity": "unknown",
                    "motion_intensity": 0.0,
                    "exertion": exert,
                    "steps": steps,
                    "ppg_quality": "good",
                }
                if TelemetryRecord:
                    rec = TelemetryRecord.parse(pkt)
                    print(rec.to_json(), flush=True)
                else:
                    print(json.dumps(pkt), flush=True)
                count += 1
                time.sleep(rate)

        if not loop or count == 0:
            break
    print(f"[replay] Replay finished ({count} frames emitted)", file=sys.stderr, flush=True)


def main():
    args = parse_args()
    input_path = args.input or find_default_csv()
    if not input_path or not os.path.isfile(input_path):
        print(f"[replay] Error: No valid capture file found at {input_path}", file=sys.stderr)
        return 1

    replay_csv(input_path, args.rate, args.loop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
