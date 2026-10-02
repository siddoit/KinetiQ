"""
KINETIQ telemetry schema (locked v1.0).

Every telemetry packet emitted by the ESP32-S3 firmware (comm_thread, 1 Hz) and
consumed by the laptop pipeline must match this exact JSON schema -- no missing
keys, no unknown keys:

    {
      "timestamp": 1727544000,
      "heart_rate": 72,
      "spo2": 97,
      "activity": "walking",
      "motion_intensity": 0.42,
      "exertion": "moderate",
      "steps": 4218,
      "ppg_quality": "good"
    }

Field contract
--------------
timestamp          int, unix seconds, > 0
heart_rate         int, bpm, 30-220
spo2               int, still-only estimate, 70-100
activity           str, one of rest | walk | run | active | unknown (case-insensitive)
motion_intensity   float, normalised 0.00-1.00
exertion           str, one of rest | light | moderate | high (case-insensitive)
steps              int, >= 0
ppg_quality        str, one of good | poor (case-insensitive)

Values are normalised to lowercase on parse so downstream string comparisons
never need to case-fold. Any violation raises ValueError with an explicit
message -- the pipeline drops malformed packets rather than guessing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Tuple, Union

SCHEMA_VERSION = "1.0"

ACTIVITY_VALUES: Tuple[str, ...] = ("rest", "walk", "run", "active", "unknown")
EXERTION_VALUES: Tuple[str, ...] = ("rest", "light", "moderate", "high")
PPG_QUALITY_VALUES: Tuple[str, ...] = ("good", "poor")

REQUIRED_FIELDS: Tuple[str, ...] = (
    "timestamp",
    "heart_rate",
    "spo2",
    "activity",
    "motion_intensity",
    "exertion",
    "steps",
    "ppg_quality",
)

HEART_RATE_RANGE = (30, 220)
SPO2_RANGE = (70, 100)


class TelemetryError(ValueError):
    """Raised when a packet does not satisfy the locked telemetry schema."""


def _coerce_int(name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise TelemetryError(f"field '{name}': expected an integer, got boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != int(value):
            raise TelemetryError(f"field '{name}': expected an integer, got {value!r}")
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 10)
        except ValueError:
            raise TelemetryError(
                f"field '{name}': expected an integer, got {value!r}"
            ) from None
    raise TelemetryError(f"field '{name}': expected an integer, got {value!r}")


def _coerce_float(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise TelemetryError(f"field '{name}': expected a number, got boolean")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            raise TelemetryError(f"field '{name}': expected a number, got {value!r}") from None
    raise TelemetryError(f"field '{name}': expected a number, got {value!r}")


def _coerce_choice(name: str, value: Any, allowed: Tuple[str, ...]) -> str:
    if not isinstance(value, str):
        raise TelemetryError(
            f"field '{name}': expected one of {list(allowed)}, got {value!r}"
        )
    text = value.strip().lower()
    if text not in allowed:
        raise TelemetryError(
            f"field '{name}': {value!r} is not one of {list(allowed)}"
        )
    return text


def _check_range(name: str, value: int, bounds: Tuple[int, int]) -> int:
    low, high = bounds
    if value < low or value > high:
        raise TelemetryError(
            f"field '{name}': {value} is out of range [{low}, {high}]"
        )
    return value


@dataclass(frozen=True)
class TelemetryRecord:
    """One validated KINETIQ telemetry packet."""

    timestamp: int
    heart_rate: int
    spo2: int
    activity: str
    motion_intensity: float
    exertion: str
    steps: int
    ppg_quality: str

    @classmethod
    def parse(cls, raw: Union[Dict[str, Any], str, bytes]) -> "TelemetryRecord":
        """
        Validate and coerce a raw telemetry packet into a TelemetryRecord.

        Accepts either a mapping or a JSON string/bytes object.
        Raises ValueError (TelemetryError) on any schema violation.
        """
        if isinstance(raw, (str, bytes, bytearray)):
            try:
                decoded = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise TelemetryError(f"packet is not valid JSON: {exc}") from None
            if not isinstance(decoded, dict):
                raise TelemetryError(
                    f"packet must decode to a JSON object, got {type(decoded).__name__}"
                )
            payload: Dict[str, Any] = decoded
        elif isinstance(raw, dict):
            payload = raw
        else:
            raise TelemetryError(
                f"packet must be a dict or JSON string, got {type(raw).__name__}"
            )

        present = tuple(payload.keys())
        missing = [key for key in REQUIRED_FIELDS if key not in payload]
        unknown = [key for key in present if key not in REQUIRED_FIELDS]

        if missing:
            raise TelemetryError(f"missing required field(s): {missing}")
        if unknown:
            raise TelemetryError(f"unknown field(s) not allowed by schema: {unknown}")

        timestamp = _coerce_int("timestamp", payload["timestamp"])
        if timestamp <= 0:
            raise TelemetryError(f"field 'timestamp': {timestamp} must be a positive unix second value")

        heart_rate = _check_range(
            "heart_rate",
            _coerce_int("heart_rate", payload["heart_rate"]),
            HEART_RATE_RANGE,
        )
        spo2 = _check_range(
            "spo2",
            _coerce_int("spo2", payload["spo2"]),
            SPO2_RANGE,
        )
        activity = _coerce_choice("activity", payload["activity"], ACTIVITY_VALUES)
        motion_intensity = _coerce_float("motion_intensity", payload["motion_intensity"])
        if motion_intensity < 0.0 or motion_intensity > 1.0:
            raise TelemetryError(
                f"field 'motion_intensity': {motion_intensity} is out of range [0.0, 1.0]"
            )
        exertion = _coerce_choice("exertion", payload["exertion"], EXERTION_VALUES)
        steps = _coerce_int("steps", payload["steps"])
        if steps < 0:
            raise TelemetryError(f"field 'steps': {steps} must be >= 0")
        ppg_quality = _coerce_choice("ppg_quality", payload["ppg_quality"], PPG_QUALITY_VALUES)

        return cls(
            timestamp=timestamp,
            heart_rate=heart_rate,
            spo2=spo2,
            activity=activity,
            motion_intensity=motion_intensity,
            exertion=exertion,
            steps=steps,
            ppg_quality=ppg_quality,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return the packet as a plain dict in locked key order."""
        data = asdict(self)
        return {key: data[key] for key in REQUIRED_FIELDS}

    def to_json(self) -> str:
        """Serialise the packet to the exact locked one-line JSON form."""
        return json.dumps(self.to_dict(), separators=(",", ":"))


def validate_packet(raw: Union[Dict[str, Any], str, bytes]) -> TelemetryRecord:
    """Module-level convenience wrapper around TelemetryRecord.parse."""
    return TelemetryRecord.parse(raw)
