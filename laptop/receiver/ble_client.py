"""
KINETIQ BLE NUS telemetry client (laptop side).

Closes the gap described in docs/FINAL_BREAKDOWN.md 3 / 11.3:

    ESP32-S3 NUS notify -> frame reassembly -> JSON validator (drop + count)
    -> CSV logger + assistant context

Wire contract
-------------
The firmware advertises a Nordic-UART-Service-style GATT layout and streams one
locked-schema JSON object per line (``TelemetryRecord.to_json()`` + ``\\n``) over
the TX characteristic, at 1 Hz. BLE notifications are MTU-bounded, so a single
logical frame routinely arrives as 2-4 byte fragments and several frames can
share one notification. This module therefore never assumes one notification
== one frame; it reassembles first (``FrameReassembler``) and only then parses.

    service  6E400001-B5A3-F393-E0A9-E50E24DCCA9E
    tx char  6E400003-B5A3-F393-E0A9-E50E24DCCA9E   (watch -> laptop notify)
    rx char  6E400002-B5A3-F393-E0A9-E50E24DCCA9E   (laptop -> watch write)

Three layers
------------
1. ``FrameReassembler``  pure, synchronous, unit-testable byte-buffer -> frame
   state machine (newline framing, brace-scanned delimiter-less framing, an
   overflow valve so a garbage stream cannot exhaust memory).
2. ``BleNusClient``       async bleak client: scan -> connect -> start_notify ->
   drain queue -> ``on_packet`` per frame, with exponential-backoff reconnect.
   bleak is imported *lazily*, inside the methods that need it.
3. ``MockBleSource`` / ``MockBleClient``  an in-process fake NUS peer that
   pushes whole frames, fragmented frames, multi-frame bursts and malformed
   frames through the **same** ``NusSession`` routing path, so ``--source mock``
   exercises the real reassembler and the real validator with no hardware and
   no bleak installed.

``PacketRouter`` is the single routing function both modes share: raw dict ->
``TelemetryRecord.parse`` -> ``SessionLogger.write`` + ``ContextManager`` update.
A rejected packet is counted, logged to the reject log and skipped; it never
aborts the run.

Usage (from the repository root, Windows PowerShell):
    python laptop\\receiver\\ble_client.py --source mock --mock-count 30 --inject-bad 3 --verbose
    python laptop\\receiver\\ble_client.py --source ble --name-filter KINETIQ --verbose
    python laptop\\receiver\\ble_client.py --source ble --mac AA:BB:CC:DD:EE:FF --duration 60
    python -m laptop.receiver.ble_client --source mock --mock-count 5

Exit codes: 0 clean shutdown, 2 zero packets routed, 3 source setup failure
(bleak missing, no device found, unwritable log directory).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import json
import os
import sys
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence, Tuple

# The telemetry contract and the CSV logger live next to this file, the assistant
# context one level up; make all three importable no matter which directory this
# file was launched from (Windows-safe, no POSIX tricks).
_RECEIVER_DIR = os.path.dirname(os.path.abspath(__file__))
_LAPTOP_DIR = os.path.dirname(_RECEIVER_DIR)
_AI_DIR = os.path.join(_LAPTOP_DIR, "ai")
for _extra_path in (_RECEIVER_DIR, _LAPTOP_DIR, _AI_DIR):
    if os.path.isdir(_extra_path) and _extra_path not in sys.path:
        sys.path.append(_extra_path)

try:  # package import: python -m laptop.receiver.ble_client
    from .telemetry_schema import TelemetryError, TelemetryRecord
except ImportError:  # direct script: python laptop\receiver\ble_client.py
    from telemetry_schema import TelemetryError, TelemetryRecord  # noqa: E402

try:
    from .dashboard import SessionLogger
except ImportError:
    from dashboard import SessionLogger  # noqa: E402

try:
    from .mock_streamer import StreamerState, build_packet
except ImportError:
    from mock_streamer import StreamerState, build_packet  # noqa: E402

try:  # package import: python -m laptop.receiver.ble_client
    from ..ai.assistant import ContextManager
except ImportError:  # direct script: python laptop\receiver\ble_client.py
    try:
        from assistant import ContextManager  # noqa: E402
    except ImportError:  # laptop/ai is optional -- CSV logging still works
        ContextManager = None  # type: ignore[assignment]

#: Nordic UART Service layout used by the ESP32-S3 firmware.
NUS_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # watch -> laptop
NUS_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # laptop -> watch
TARGET_DEVICE_NAME = "KINETIQ"

#: Frame reassembly limits. A locked telemetry frame is ~150 bytes, so 4 KiB of
#: delimiter-less buffer is ~25x a real frame; 8 KiB hard cap bounds memory even
#: if the peer never sends a delimiter.
NO_DELIMITER_LIMIT = 4096
MAX_BUFFER_BYTES = 8192
MAX_JSON_DEPTH = 64

#: Reconnect pacing: 1, 2, 4, 8, ... capped at ``max_backoff_s``.
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0
#: A session that stayed up this long counts as healthy, so the next backoff
#: restarts from the base delay instead of inheriting a long streak of failures.
HEALTHY_SESSION_S = 30.0
RECONNECT_ATTEMPT_LIMIT = 64

SCAN_TIMEOUT_S = 5.0
DRAIN_POLL_S = 0.2
DEFAULT_CSV_DIR = os.path.join(_LAPTOP_DIR, "logs")
DEFAULT_MOCK_COUNT = 20
DEFAULT_MOCK_INTERVAL_S = 0.05

#: Synthetic timeline cadence for mock packets. The firmware streams at 1 Hz;
#: ``--mock-interval`` only paces *delivery*, so the generated timestamps and
#: step count advance at the real rate instead of the mock rate.
MOCK_STREAM_INTERVAL_S = 1.0

EXIT_OK = 0
EXIT_NO_PACKETS = 2
EXIT_SOURCE_ERROR = 3

_LABEL = "[kinetiq]"


class SourceSetupError(RuntimeError):
    """Raised when a source cannot be opened, imported or found."""


def _log(message: str) -> None:
    print("{0} {1}".format(_LABEL, message), flush=True)


def _warn(message: str) -> None:
    print("{0} {1}".format(_LABEL, message), file=sys.stderr, flush=True)


def _as_bytes(chunk: Any) -> bytes:
    """Normalise a notification payload to bytes (str/bytearray tolerated)."""
    if isinstance(chunk, bytes):
        return chunk
    if isinstance(chunk, str):
        return chunk.encode("utf-8", errors="replace")
    return bytes(chunk)


def _import_bleak() -> Any:
    """
    Import bleak on demand.

    Lazy by design: ``--source mock`` must import and run on a host where bleak
    is not installed at all, so nothing in this module imports bleak at module
    scope.
    """
    try:
        import bleak  # lazy: never required for import or for --source mock
    except ImportError as exc:
        raise SourceSetupError(
            "bleak is not installed -- run `pip install bleak` (it is already "
            "listed in laptop/requirements.txt) or use --source mock"
        ) from exc
    return bleak


def next_backoff(
    attempt: int, base: float = BACKOFF_BASE_S, cap: float = BACKOFF_CAP_S
) -> float:
    """
    Pure exponential-backoff step: attempt 1 -> base, doubling, clamped to cap.

    Kept free of I/O and state so the sequence can be asserted directly:
    ``next_backoff(n)`` for n = 1.. is 1, 2, 4, 8, 16, 30, 30, ...
    """
    step = max(1, int(attempt))
    return float(min(cap, base * (2.0 ** (step - 1))))


def _scan_json_object_end(buffer: bytes) -> Optional[int]:
    """
    Return the byte length of the first complete top-level JSON object, else None.

    Byte-level and string-aware (braces inside string literals do not count), so
    a multi-byte UTF-8 character split across notifications is never decoded
    mid-sequence. Used to frame delimiter-less streams without guessing.
    """
    depth = 0
    started = False
    in_string = False
    escaped = False
    for index, byte in enumerate(buffer):
        char = chr(byte)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if not started:
                started = True
                depth = 1
            else:
                depth += 1
                if depth > MAX_JSON_DEPTH:
                    return None
            continue
        if char == "}" and started:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


class FrameReassembler:
    """
    Notification bytes -> complete JSON objects, one frame per telemetry packet.

    Pure and synchronous so it can be unit-tested without asyncio, bleak or a
    radio. Framing rules, in order:

    1. ``\\n`` (with an optional preceding ``\\r``) is the primary delimiter: every
       complete line is a frame.
    2. A frame is also flushed as soon as the buffer holds a complete,
       brace-balanced JSON object, so a peer that never sends a delimiter still
       makes progress.
    3. If the buffer holds data that is neither (junk, or an unterminated object
       that outgrew ``no_delimiter_limit``) it is discarded and
       ``overflow_count`` is incremented. The buffer is hard-capped at
       ``max_buffer_bytes`` (oldest bytes dropped) so a garbage or hostile
       stream cannot grow the process without bound.

    Decoding happens per completed frame, never per chunk, which is what keeps a
    multi-byte character split across two notifications intact.
    """

    def __init__(
        self,
        max_buffer_bytes: int = MAX_BUFFER_BYTES,
        no_delimiter_limit: int = NO_DELIMITER_LIMIT,
    ) -> None:
        self.max_buffer_bytes = max(64, int(max_buffer_bytes))
        self.no_delimiter_limit = max(16, int(no_delimiter_limit))
        self._buffer = bytearray()
        self._spanning = False

        self.frames_emitted = 0
        self.frames_invalid_json = 0
        self.frames_reassembled = 0
        self.overflow_count = 0
        self.bytes_received = 0
        self.payloads_received = 0
        self.last_drop_reason = ""

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, chunk: Any) -> List[Dict[str, Any]]:
        """
        Append one notification payload; return every frame it completed.

        A returned frame is a JSON *object*; anything undecodable, or decodable
        to a non-object, is counted in ``frames_invalid_json`` and dropped here
        so it never reaches the schema validator.
        """
        frames: List[Dict[str, Any]] = []
        data = _as_bytes(chunk)
        if not data:
            return frames

        self.payloads_received += 1
        self.bytes_received += len(data)
        self._buffer.extend(data)

        self._drain_delimited(frames)
        self._drain_delimiterless(frames)
        self._enforce_cap()

        if self._buffer:
            self._spanning = True
        return frames

    def flush(self) -> List[Dict[str, Any]]:
        """Drain whatever is left when the stream ends; returns leftover frames."""
        frames: List[Dict[str, Any]] = []
        if self._buffer:
            self._drain_delimited(frames)
        if self._buffer:
            text = bytes(self._buffer).decode("utf-8", errors="replace")
            self._buffer.clear()
            if text.strip():
                self._consume(text, frames)
        self._spanning = False
        return frames

    def stats(self) -> Dict[str, int]:
        return {
            "payloads_received": self.payloads_received,
            "bytes_received": self.bytes_received,
            "frames_emitted": self.frames_emitted,
            "frames_invalid_json": self.frames_invalid_json,
            "frames_reassembled": self.frames_reassembled,
            "overflow_count": self.overflow_count,
            "buffered_bytes": self.buffered_bytes,
        }

    def _drain_delimited(self, frames: List[Dict[str, Any]]) -> None:
        while True:
            cut = self._buffer.find(b"\n")
            if cut < 0:
                return
            line = bytes(self._buffer[:cut])
            del self._buffer[: cut + 1]
            self._consume(line.decode("utf-8", errors="replace"), frames)

    def _drain_delimiterless(self, frames: List[Dict[str, Any]]) -> None:
        while True:
            head = -1
            for index, byte in enumerate(self._buffer):
                if byte not in (0x20, 0x09, 0x0D, 0x0A):
                    head = index
                    break

            if head < 0:
                if len(self._buffer) > self.no_delimiter_limit:
                    self._drop("whitespace-only buffer exceeded the delimiter-less limit")
                return

            if self._buffer[head] != 0x7B:  # '{'
                self._drop("delimiter-less buffer does not start a JSON object")
                return

            end = _scan_json_object_end(bytes(self._buffer[head:]))
            if end is None:
                if len(self._buffer) > self.no_delimiter_limit:
                    self._drop("unterminated JSON object exceeded the delimiter-less limit")
                return

            raw = bytes(self._buffer[head : head + end])
            del self._buffer[: head + end]
            self._consume(raw.decode("utf-8", errors="replace"), frames)

    def _enforce_cap(self) -> None:
        excess = len(self._buffer) - self.max_buffer_bytes
        if excess > 0:
            del self._buffer[:excess]
            self.overflow_count += 1
            self.last_drop_reason = "buffer exceeded {0} bytes; oldest data dropped".format(
                self.max_buffer_bytes
            )

    def _drop(self, reason: str) -> None:
        self.overflow_count += 1
        self.last_drop_reason = reason
        self._buffer.clear()
        self._spanning = False

    def _consume(self, text: str, frames: List[Dict[str, Any]]) -> None:
        payload = text.strip()
        if not payload:
            return
        if self._spanning:
            self.frames_reassembled += 1
            self._spanning = False
        try:
            decoded = json.loads(payload)
        except (ValueError, RecursionError):
            self.frames_invalid_json += 1
            return
        if not isinstance(decoded, dict):
            self.frames_invalid_json += 1
            return
        self.frames_emitted += 1
        frames.append(decoded)


class NusSession:
    """
    Frame routing core shared by the real and the mock transport.

    Both ``BleNusClient`` and ``MockBleClient`` funnel notification bytes through
    one :class:`FrameReassembler` and call ``on_packet`` once per complete frame,
    so the mock cannot drift from the real behaviour.
    """

    def __init__(
        self,
        on_packet: Callable[[Dict[str, Any]], Any],
        reassembler: Optional[FrameReassembler] = None,
    ) -> None:
        self.on_packet = on_packet
        self.reassembler = reassembler or FrameReassembler()
        self.packets_routed = 0
        self.callback_errors = 0
        self.last_callback_error = ""

    @property
    def frames_emitted(self) -> int:
        return self.reassembler.frames_emitted

    async def dispatch(self, payload: Any) -> int:
        """Feed one notification payload; return how many frames it produced."""
        return await self.dispatch_frames(self.reassembler.feed(payload))

    async def dispatch_frames(self, frames: Sequence[Dict[str, Any]]) -> int:
        """Hand already-framed packets to the consumer callback."""
        for frame in frames:
            await self._invoke(frame)
        return len(frames)

    async def _invoke(self, frame: Dict[str, Any]) -> None:
        self.packets_routed += 1
        try:
            result = self.on_packet(frame)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a bad consumer must not kill the transport
            self.callback_errors += 1
            self.last_callback_error = "{0}: {1}".format(type(exc).__name__, exc)


class BleNusClient:
    """
    bleak-backed NUS client: scan, connect, subscribe, route, reconnect.

    Notification callbacks only push raw bytes onto an ``asyncio.Queue`` which the
    main task drains, so reassembly and ``on_packet`` always run on the session's
    own coroutine -- no re-entrant writes into the pipeline from a bleak
    callback, and no blocking work inside a WinRT/loopback callback. The
    disconnect callback is a plain synchronous callback; it schedules its state
    change onto the loop it was given, so it is safe from any thread.
    """

    requires_scan = True

    def __init__(
        self,
        on_packet: Callable[[Dict[str, Any]], Any],
        mac: Optional[str] = None,
        name_filter: str = TARGET_DEVICE_NAME,
        reconnect: bool = True,
        max_backoff_s: float = BACKOFF_CAP_S,
        hello_on_connect: bool = True,
    ) -> None:
        self.on_packet = on_packet
        self.mac = mac
        self.name_filter = name_filter
        self.reconnect = reconnect
        self.max_backoff_s = max(0.1, float(max_backoff_s))
        self.hello_on_connect = hello_on_connect
        self.reassembler = FrameReassembler()
        self.session = NusSession(on_packet, self.reassembler)

        self.last_devices: List[Any] = []
        self.connected = False
        self.connect_attempts = 0
        self.reconnects = 0
        self.last_session_s = 0.0
        self.rx_writes = 0
        self.last_error = ""

        self._client: Any = None
        self._queue: "asyncio.Queue[bytes]" = asyncio.Queue()
        self._disconnected: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._connected_at = 0.0
        self._hello_sent = False

    # ------------------------------------------------------------------ scan

    async def scan(self, timeout: float = SCAN_TIMEOUT_S) -> List[Any]:
        """
        Discover the watch. A device matches on name substring *or* on the NUS
        service UUID in its advertisement. With ``--mac`` the radio is never
        scanned: the address is targeted directly.
        """
        bleak = _import_bleak()
        if self.mac:
            self.last_devices = [self.mac]
            _log("using --mac {0}, skipping discovery".format(self.mac))
            return list(self.last_devices)

        found = await self._discover(bleak, timeout)
        matches: List[Any] = []
        for address, entry in found.items():
            device, advertisement = _split_discovered(address, entry)
            name = _device_name(device, advertisement)
            uuids = _advertised_uuids(advertisement)
            if self._matches(name, uuids):
                matches.append(device)
                _log(
                    "found {0} (name={1!r}, nus_advertised={2})".format(
                        getattr(device, "address", address) or address,
                        name,
                        NUS_SERVICE_UUID.lower() in uuids,
                    )
                )

        self.last_devices = matches
        return matches

    async def _discover(self, bleak: Any, timeout: float) -> Dict[str, Any]:
        scanner = bleak.BleakScanner
        try:
            found = await scanner.discover(timeout=timeout, return_adv=True)
        except TypeError:  # older bleak without return_adv
            found = await scanner.discover(timeout=timeout)
        return dict(found or {})

    def _matches(self, name: str, uuids: Sequence[str]) -> bool:
        wanted = (self.name_filter or "").strip().lower()
        if wanted and wanted in (name or "").lower():
            return True
        return NUS_SERVICE_UUID.lower() in uuids

    # ---------------------------------------------------------------- connect

    async def connect_and_listen(self) -> int:
        """
        Connect and stream until stopped, reconnecting with exponential backoff.

        Returns the number of frames routed across every connection attempt.
        With ``reconnect=False`` the first unexpected drop ends the run.
        """
        attempt = 0
        while True:
            attempt += 1
            self.connect_attempts = attempt
            address = self._target()
            try:
                await self._listen_once(address)
                self.last_error = ""
            except asyncio.CancelledError:
                await self.close()
                raise
            except SourceSetupError:
                await self.close()
                raise
            except Exception as exc:
                self.last_error = "{0}: {1}".format(type(exc).__name__, exc)
                _warn("connection to {0} failed ({1})".format(address, self.last_error))
                await self.close()

            if not self.reconnect:
                if self.last_error:
                    _warn("reconnect disabled; exiting after first drop")
                break

            if self.last_session_s >= HEALTHY_SESSION_S:
                _log("session held {0:.1f}s, resetting backoff".format(self.last_session_s))
                attempt = 0
            if attempt > RECONNECT_ATTEMPT_LIMIT:
                _warn("giving up after {0} reconnect attempts".format(self.reconnects))
                break

            self.reconnects += 1
            delay = next_backoff(attempt, cap=self.max_backoff_s)
            _log(
                "reconnect attempt {0} in {1:.1f}s (backoff {2:g}s)".format(
                    self.reconnects, delay, delay
                )
            )
            await asyncio.sleep(delay)

        return self.session.packets_routed

    def _target(self) -> Any:
        if self.mac:
            return self.mac
        if self.last_devices:
            return self.last_devices[0]
        raise SourceSetupError(
            "no target device: run scan() first or pass --mac"
        )

    async def _listen_once(self, address: Any) -> None:
        bleak = _import_bleak()
        loop = asyncio.get_event_loop()
        self._loop = loop
        self._disconnected = asyncio.Event()
        self._queue = asyncio.Queue()
        self._hello_sent = False

        client = bleak.BleakClient(address, disconnected_callback=self._on_disconnected)
        self._client = client
        _log("connecting to {0}...".format(getattr(client, "address", address)))
        await client.connect()
        self.connected = True
        self._connected_at = time.monotonic()
        _log(
            "connected to {0}, subscribing to {1}".format(
                getattr(client, "address", address), NUS_TX_CHAR_UUID
            )
        )
        await client.start_notify(NUS_TX_CHAR_UUID, self._on_notify)

        if self.hello_on_connect:
            try:
                await self.send_command("hello")
            except Exception as exc:  # downlink is best-effort, never fatal
                _warn("hello command failed ({0})".format(exc))

        try:
            while not self._disconnected.is_set():
                try:
                    payload = await asyncio.wait_for(self._queue.get(), DRAIN_POLL_S)
                except asyncio.TimeoutError:
                    continue
                await self.session.dispatch(payload)
        finally:
            self.last_session_s = time.monotonic() - self._connected_at
            self.connected = False
            await self.close()
        _log("session ended after {0:.1f}s".format(self.last_session_s))

    # --------------------------------------------------------------- callbacks

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        """bleak notification callback: enqueue raw bytes, do no work here."""
        try:
            self._queue.put_nowait(bytes(data))
        except asyncio.QueueFull:  # pragma: no cover - queue is unbounded
            self.reassembler.overflow_count += 1

    def _on_disconnected(self, _client: Any) -> None:
        """bleak disconnect callback: flag the session end on the running loop."""
        loop = self._loop
        event = self._disconnected
        if event is None:
            return
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(event.set)
                return
            except RuntimeError:  # pragma: no cover - loop already gone
                pass
        event.set()

    # ------------------------------------------------------------------ write

    async def write_rx(self, data: Any) -> bool:
        """Write a command to the watch-side RX characteristic (PTT downlink)."""
        client = self._client
        if client is None or not self.connected:
            _warn("write_rx ignored: not connected")
            return False
        payload = _as_bytes(data)
        await client.write_gatt_char(NUS_RX_CHAR_UUID, payload, response=True)
        self.rx_writes += 1
        _log("tx {0} byte(s) to {1}".format(len(payload), NUS_RX_CHAR_UUID))
        return True

    async def send_command(self, command: str) -> bool:
        """Send one newline-terminated command; used once per connect as a demo."""
        return await self.write_rx("{0}\n".format(command))

    async def close(self) -> None:
        """Stop notifications and disconnect; safe to call repeatedly."""
        client = self._client
        self._client = None
        self.connected = False
        if client is None:
            return
        with contextlib.suppress(Exception):
            if getattr(client, "is_connected", False):
                await client.stop_notify(NUS_TX_CHAR_UUID)
        with contextlib.suppress(Exception):
            if getattr(client, "is_connected", False):
                await client.disconnect()

    # ------------------------------------------------------------------- info

    def describe(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "transport": "ble",
            "connect_attempts": self.connect_attempts,
            "reconnects": self.reconnects,
            "rx_writes": self.rx_writes,
            "last_error": self.last_error,
        }
        info.update(self.reassembler.stats())
        return info


def _split_discovered(address: Any, entry: Any) -> Tuple[Any, Any]:
    """``discover()`` returns a device, or a (device, advertisement) pair."""
    if isinstance(entry, tuple) and len(entry) == 2:
        return entry[0], entry[1]
    return entry, None


def _device_name(device: Any, advertisement: Any) -> str:
    for candidate in (
        getattr(advertisement, "local_name", None),
        getattr(device, "name", None),
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


def _advertised_uuids(advertisement: Any) -> Tuple[str, ...]:
    uuids = getattr(advertisement, "service_uuids", None) or ()
    return tuple(str(item).lower() for item in uuids)


#: Frame shapes the mock peer cycles through, so every delivery mode the real
#: radio can produce is exercised on a laptop with no hardware attached.
MOCK_MODES: Tuple[str, ...] = (
    "whole",  # one frame, one notification, newline terminated
    "fragment",  # one frame spread over many 2-4 byte fragments
    "delimiterless",  # one complete frame with no trailing newline
    "whole",
    "burst",  # two frames packed into one notification
)

#: Deliberately malformed frames for ``--inject-bad``, cycling in this order:
#: the first and last two fail schema validation *after* parsing (so they reach
#: the reject log), the middle two are not decodable JSON at all (so they are
#: dropped by the reassembler and never reach the validator).
BAD_FRAMES: Tuple[bytes, ...] = (
    b'{"timestamp":1750000000}\n',
    b"this is not json at all\n",
    b'{"timestamp":1750000000,"heart_rate":72,"spo2":97,"activity":"walk"\n',
    (
        b'{"timestamp":1750000000,"heart_rate":72,"spo2":97,"activity":"teleport",'
        b'"motion_intensity":0.4,"exertion":"light","steps":1,"ppg_quality":"good"}\n'
    ),
    (
        b'{"timestamp":1750000000,"heart_rate":900,"spo2":97,"activity":"walk",'
        b'"motion_intensity":0.4,"exertion":"light","steps":1,"ppg_quality":"good"}\n'
    ),
)


class MockBleSource:
    """
    In-process fake NUS peer: turns synthetic telemetry into notification bytes.

    ``mock_notifications`` is an async generator, so the mock is paced by the
    same await points a real notification callback would create. Yields are
    byte payloads, never packets: framing, reassembly and validation all happen
    downstream, exactly as on the radio.
    """

    def __init__(
        self,
        count: int = DEFAULT_MOCK_COUNT,
        interval_s: float = DEFAULT_MOCK_INTERVAL_S,
        inject_bad: int = 0,
    ) -> None:
        self.count = max(0, int(count))
        self.interval_s = max(0.001, float(interval_s))
        self.inject_bad = max(0, int(inject_bad))
        # Fragments of one frame arrive back to back on a real link, so they are
        # paced far tighter than one frame per interval.
        self.fragment_interval_s = max(0.0005, min(0.005, self.interval_s / 10.0))

        self.frames_written = 0
        self.payloads_yielded = 0
        self.fragments_yielded = 0
        self.fragmented_frames = 0
        self.burst_payloads = 0
        self.burst_frames = 0
        self.delimiterless_frames = 0
        self.whole_frames = 0
        self.bad_frames_yielded = 0

    def _good_lines(self, count: int) -> List[str]:
        state = StreamerState(interval_s=MOCK_STREAM_INTERVAL_S)
        lines: List[str] = []
        for _ in range(count):
            packet = build_packet(state)
            lines.append(TelemetryRecord.parse(packet).to_json())
        self.frames_written = len(lines)
        return lines

    @staticmethod
    def _fragment(payload: bytes) -> List[bytes]:
        """Split one frame into 2-4 byte fragments, the way an MTU-bounded link does."""
        pieces: List[bytes] = []
        index = 0
        size = 2
        while index < len(payload):
            pieces.append(payload[index : index + size])
            index += size
            size = 2 + (index % 3)
        return pieces

    def _bad_payload(self, index: int) -> bytes:
        return BAD_FRAMES[index % len(BAD_FRAMES)]

    async def mock_notifications(
        self, count: int = 0, interval: Optional[float] = None
    ) -> AsyncIterator[bytes]:
        """
        Yield notification payloads: ``count`` good frames, mixed whole,
        fragmented, delimiter-less and burst, with ``--inject-bad`` malformed
        frames spread through the stream.
        """
        total = self.count if count is None or count <= 0 else int(count)
        pace = self.interval_s if interval is None else max(0.0, float(interval))
        lines = self._good_lines(total)

        stride = max(1, total // (self.inject_bad + 1)) if self.inject_bad else 0
        bad_remaining = self.inject_bad
        produced = 0
        index = 0

        while index < len(lines):
            mode = MOCK_MODES[index % len(MOCK_MODES)]
            produced += 1
            payload: Optional[bytes] = None

            if mode == "burst" and index + 1 < len(lines):
                payload = "{0}\n{1}\n".format(lines[index], lines[index + 1]).encode("utf-8")
                self.burst_payloads += 1
                self.burst_frames += 2
                index += 2
                produced += 1
            elif mode == "fragment":
                # One frame, many notifications: this is the reassembly proof.
                self.fragmented_frames += 1
                index += 1
                for piece in self._fragment("{0}\n".format(lines[index - 1]).encode("utf-8")):
                    self.payloads_yielded += 1
                    self.fragments_yielded += 1
                    yield piece
                    await asyncio.sleep(self.fragment_interval_s)
            elif mode == "delimiterless":
                # Complete JSON with no trailing newline: framed by brace balance.
                payload = lines[index].encode("utf-8")
                self.delimiterless_frames += 1
                index += 1
            elif mode == "burst":
                payload = "{0}\n".format(lines[index]).encode("utf-8")
                self.whole_frames += 1
                index += 1
            else:
                payload = "{0}\n".format(lines[index]).encode("utf-8")
                self.whole_frames += 1
                index += 1

            if payload is not None:
                self.payloads_yielded += 1
                yield payload
                await asyncio.sleep(pace)

            if bad_remaining > 0 and stride and produced % stride == 0:
                self.bad_frames_yielded += 1
                bad_remaining -= 1
                self.payloads_yielded += 1
                yield self._bad_payload(self.bad_frames_yielded - 1)
                await asyncio.sleep(pace)

        while bad_remaining > 0:  # more bad frames than the stream had slots for
            self.bad_frames_yielded += 1
            bad_remaining -= 1
            self.payloads_yielded += 1
            yield self._bad_payload(self.bad_frames_yielded - 1)
            await asyncio.sleep(pace)


class MockBleClient:
    """
    Minimal fake transport that drives :class:`MockBleSource` through the same
    ``NusSession`` routing path as :class:`BleNusClient`.

    It deliberately mirrors ``connect_and_listen``/``write_rx``/``scan`` so the
    mock cannot diverge from the real client, and it never imports bleak.
    """

    requires_scan = False

    def __init__(
        self,
        on_packet: Callable[[Dict[str, Any]], Any],
        count: int = DEFAULT_MOCK_COUNT,
        interval_s: float = DEFAULT_MOCK_INTERVAL_S,
        inject_bad: int = 0,
        mac: Optional[str] = "00:00:00:00:00:00",
        name_filter: str = TARGET_DEVICE_NAME,
        reconnect: bool = True,
        hello_on_connect: bool = True,
    ) -> None:
        self.on_packet = on_packet
        self.mac = mac
        self.name_filter = name_filter
        self.reconnect = reconnect
        self.hello_on_connect = hello_on_connect
        self.source = MockBleSource(
            count=count, interval_s=interval_s, inject_bad=inject_bad
        )
        self.reassembler = FrameReassembler()
        self.session = NusSession(on_packet, self.reassembler)
        self.connected = False
        self.rx_writes = 0
        self.last_session_s = 0.0
        self.last_error = ""

    async def scan(self, timeout: float = SCAN_TIMEOUT_S) -> List[Any]:
        """Pretend the watch is already discovered; no radio involved."""
        self.last_devices = [self.mac or "00:00:00:00:00:00"]
        return list(self.last_devices)

    async def connect_and_listen(self) -> int:
        """Mirror of BleNusClient.connect_and_listen over the mock source."""
        self.connected = True
        started = time.monotonic()
        _log(
            "mock source: {0} frame(s), interval {1:g}s, inject-bad {2}".format(
                self.source.count, self.source.interval_s, self.source.inject_bad
            )
        )
        if self.hello_on_connect:
            with contextlib.suppress(Exception):
                await self.send_command("hello")
        try:
            async for payload in self.source.mock_notifications():
                await self.session.dispatch(payload)
        except asyncio.CancelledError:
            await self.close()
            raise
        finally:
            self.connected = False
            self.last_session_s = time.monotonic() - started
        leftover = self.reassembler.flush()
        if leftover:
            await self.session.dispatch_frames(leftover)
        await self.close()
        _log("mock source exhausted after {0:.1f}s".format(self.last_session_s))
        return self.session.packets_routed

    async def write_rx(self, data: Any) -> bool:
        """Record a downlink write instead of touching a radio."""
        payload = _as_bytes(data)
        self.rx_writes += 1
        _log("mock tx {0} byte(s) to {1}".format(len(payload), NUS_RX_CHAR_UUID))
        return True

    async def send_command(self, command: str) -> bool:
        return await self.write_rx("{0}\n".format(command))

    async def close(self) -> None:
        self.connected = False

    def describe(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "transport": "mock",
            "rx_writes": self.rx_writes,
            "mock_frames_written": self.source.frames_written,
            "mock_payloads_yielded": self.source.payloads_yielded,
            "mock_fragments_yielded": self.source.fragments_yielded,
            "mock_fragmented_frames": self.source.fragmented_frames,
            "mock_burst_payloads": self.source.burst_payloads,
            "mock_burst_frames": self.source.burst_frames,
            "mock_delimiterless_frames": self.source.delimiterless_frames,
            "mock_whole_frames": self.source.whole_frames,
            "mock_bad_frames": self.source.bad_frames_yielded,
            "last_error": self.last_error,
        }
        info.update(self.reassembler.stats())
        return info


class PacketRouter:
    """
    The one routing function both transports share.

    raw dict -> ``TelemetryRecord.parse`` -> ``SessionLogger.write`` +
    ``ContextManager`` update (+ a console line when verbose). A
    ``TelemetryError`` is counted, written to the reject log and skipped, so a
    malformed packet degrades one row and never the run.
    """

    def __init__(
        self,
        logger: SessionLogger,
        context: Any = None,
        verbose: bool = False,
    ) -> None:
        self.logger = logger
        self.context = context
        self.verbose = verbose
        self.routed = 0
        self.schema_rejected = 0
        self.context_errors = 0

    def __call__(self, raw: Dict[str, Any]) -> bool:
        return self.route(raw)

    def route(self, raw: Dict[str, Any]) -> bool:
        """Validate and route one packet. Returns True when it became a row."""
        try:
            if self.context is not None:
                record = self.context.update_from_raw(raw)
            else:
                record = TelemetryRecord.parse(raw)
        except TelemetryError as exc:
            self.schema_rejected += 1
            self.logger.reject(_wire_text(raw), str(exc))
            if self.verbose:
                _warn("rejected packet: {0}".format(exc))
            return False
        except Exception as exc:  # never let one packet kill the transport
            self.schema_rejected += 1
            self.logger.reject(_wire_text(raw), "{0}: {1}".format(type(exc).__name__, exc))
            if self.verbose:
                _warn("rejected packet: {0}: {1}".format(type(exc).__name__, exc))
            return False

        self.routed += 1
        self.logger.write(record)
        if self.verbose:
            _log(
                "telemetry activity={0} hr={1} spo2={2} intensity={3:.2f} "
                "exertion={4} steps={5} ppg={6} rows={7}".format(
                    record.activity,
                    record.heart_rate,
                    record.spo2,
                    record.motion_intensity,
                    record.exertion,
                    record.steps,
                    record.ppg_quality,
                    self.logger.rows,
                )
            )
        return True


def _wire_text(raw: Any) -> str:
    """Best-effort wire form of a rejected packet, for the reject log."""
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(raw)


async def _run_session(client: Any, args: argparse.Namespace, label: str) -> None:
    """Scan when needed, then stream until the source ends or --duration expires."""
    if client.requires_scan and not args.mac:
        devices = await client.scan(timeout=SCAN_TIMEOUT_S)
        if not devices:
            raise SourceSetupError(
                "no BLE device matching name {0!r} or the NUS service UUID "
                "{1} was found -- power on the watch, check the name filter, "
                "or pass --mac AA:BB:CC:DD:EE:FF".format(args.name_filter, NUS_SERVICE_UUID)
            )
    elif client.requires_scan:
        await client.scan(timeout=SCAN_TIMEOUT_S)

    task = asyncio.ensure_future(client.connect_and_listen())
    if args.duration > 0.0:
        try:
            await asyncio.wait_for(task, args.duration)
        except asyncio.TimeoutError:
            _log("--duration {0:g}s reached".format(args.duration))
            with contextlib.suppress(Exception):
                await client.close()
    else:
        await task


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kinetiq-ble",
        description=(
            "KINETIQ BLE NUS telemetry receiver: reassemble frames, validate the "
            "locked schema, log a CSV row, and update the assistant context."
        ),
    )
    parser.add_argument(
        "--source",
        choices=("ble", "mock"),
        default="ble",
        help="ble = real NUS peripheral, mock = in-process fake peer (default ble)",
    )
    parser.add_argument(
        "--mac",
        default=None,
        help="target this BLE address and skip discovery, e.g. AA:BB:CC:DD:EE:FF",
    )
    parser.add_argument(
        "--name-filter",
        default=TARGET_DEVICE_NAME,
        help="advertised name substring to scan for (default {0})".format(TARGET_DEVICE_NAME),
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="stop after this many seconds; 0 (default) runs until Ctrl+C or EOF",
    )
    parser.add_argument(
        "--mock-count",
        type=int,
        default=DEFAULT_MOCK_COUNT,
        help="good frames the mock source emits (default {0})".format(DEFAULT_MOCK_COUNT),
    )
    parser.add_argument(
        "--mock-interval",
        type=float,
        default=DEFAULT_MOCK_INTERVAL_S,
        help="seconds between mock frames (default {0:g})".format(DEFAULT_MOCK_INTERVAL_S),
    )
    parser.add_argument(
        "--inject-bad",
        type=int,
        default=0,
        help=(
            "dev flag: interleave N malformed frames in the mock stream "
            "(broken JSON, truncated frames and schema violations)"
        ),
    )
    parser.add_argument(
        "--no-reconnect",
        action="store_true",
        help="exit on the first unexpected disconnect instead of backing off",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print one line per routed packet and one per reject",
    )
    parser.add_argument(
        "--csv-dir",
        default=DEFAULT_CSV_DIR,
        help="directory for session CSVs and reject logs (default laptop/logs)",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> Optional[str]:
    if args.duration < 0.0:
        return "--duration must be >= 0"
    if args.mock_count < 0:
        return "--mock-count must be >= 0"
    if args.mock_interval <= 0.0:
        return "--mock-interval must be > 0"
    if args.inject_bad < 0:
        return "--inject-bad must be >= 0"
    if args.mac and not args.mac.strip():
        return "--mac must not be empty"
    return None


def _print_summary(
    label: str,
    router: PacketRouter,
    logger: SessionLogger,
    info: Dict[str, Any],
    elapsed_s: float,
    interrupted: bool,
) -> None:
    print(
        "{0} session summary: source={1} elapsed={2:.2f}s "
        "frames_emitted={3} frames_reassembled={4} invalid_json={5} overflow={6} "
        "bytes={7} packets_routed={8} csv_rows={9} schema_rejected={10} "
        "rejects_logged={11}".format(
            _LABEL,
            label,
            elapsed_s,
            info.get("frames_emitted", 0),
            info.get("frames_reassembled", 0),
            info.get("frames_invalid_json", 0),
            info.get("overflow_count", 0),
            info.get("bytes_received", 0),
            router.routed,
            logger.rows,
            router.schema_rejected,
            logger.rejects,
        )
    )
    if "mock_fragmented_frames" in info:
        print(
            "{0} mock: frames_written={1} payloads_yielded={2} fragments_yielded={3} "
            "fragmented_frames={4} burst_payloads={5} burst_frames={6} "
            "delimiterless_frames={7} whole_frames={8} bad_frames={9} rx_writes={10}".format(
                _LABEL,
                info.get("mock_frames_written", 0),
                info.get("mock_payloads_yielded", 0),
                info.get("mock_fragments_yielded", 0),
                info.get("mock_fragmented_frames", 0),
                info.get("mock_burst_payloads", 0),
                info.get("mock_burst_frames", 0),
                info.get("mock_delimiterless_frames", 0),
                info.get("mock_whole_frames", 0),
                info.get("mock_bad_frames", 0),
                info.get("rx_writes", 0),
            )
        )
    print("{0} csv: {1}".format(_LABEL, logger.csv_path))
    print("{0} rejects: {1}".format(_LABEL, logger.rejects_path))
    if interrupted:
        print("{0} interrupted by user (Ctrl+C)".format(_LABEL))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    problem = _validate_args(args)
    if problem is not None:
        _warn("error: {0}".format(problem))
        return EXIT_NO_PACKETS

    csv_dir = os.path.abspath(os.path.expanduser(args.csv_dir))
    context = ContextManager() if ContextManager is not None else None
    if context is None:
        _warn("assistant ContextManager is unavailable; logging without context")

    logger = SessionLogger(csv_dir)
    router = PacketRouter(logger, context, verbose=args.verbose)
    if args.source == "mock":
        label = "mock(count={0},interval={1:g},inject-bad={2})".format(
            args.mock_count, args.mock_interval, args.inject_bad
        )
        client: Any = MockBleClient(
            router.route,
            count=args.mock_count,
            interval_s=args.mock_interval,
            inject_bad=args.inject_bad,
            reconnect=not args.no_reconnect,
        )
    else:
        target = "{0} {1}".format(args.name_filter, args.mac) if args.mac else args.name_filter
        label = "ble({0})".format(target)
        client = BleNusClient(
            router.route,
            mac=args.mac,
            name_filter=args.name_filter,
            reconnect=not args.no_reconnect,
        )

    started = time.monotonic()
    interrupted = False
    setup_failed = False
    info: Dict[str, Any] = {}

    try:
        with logger:
            _log("source={0} session={1}".format(label, logger.csv_path))
            try:
                asyncio.run(_run_session(client, args, label))
            except KeyboardInterrupt:
                interrupted = True
            except SourceSetupError as exc:
                setup_failed = True
                _warn("error: {0}".format(exc))
            except Exception as exc:
                setup_failed = True
                _warn("error: {0}: {1}".format(type(exc).__name__, exc))
    except OSError as exc:
        _warn("error: cannot write session files in {0}: {1}".format(csv_dir, exc))
        return EXIT_SOURCE_ERROR
    finally:
        info = client.describe()
        if ContextManager is not None and context is not None:
            _log(
                "context packets={0} payload={1}".format(
                    context.packet_count, json.dumps(context.payload())
                )
            )

    _print_summary(
        label, router, logger, info, time.monotonic() - started, interrupted
    )

    if setup_failed:
        return EXIT_SOURCE_ERROR
    if router.routed == 0:
        _warn("no telemetry packets were routed from {0}".format(label))
        return EXIT_NO_PACKETS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
