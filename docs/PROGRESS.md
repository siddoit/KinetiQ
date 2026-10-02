# KINETIQ — Day-0 Sprint Progress

**Date:** 2026-10-02 · **Sprint:** Day-0 foundation · **Remote:** `origin/main` (github.com/siddoit/KinetiQ)

All Day-0 work landed as 10 commits (`c94bbc7` → `HEAD`). Working tree clean; build artifacts
(`build/`, `.venv/`, `__pycache__/`, `laptop/logs/`) verified ignored via `git status --ignored`.

---

## Hardware / PCB Lane

- **BOM v1.0 frozen** (`FINAL_BREAKDOWN.md` §11.1) with exact MPNs and Indian distributor links
  (Robu.in / Evelta / Robocraze / Probots): S3-WROOM-1-N16R8 ×5, DevKitC ×2, BMI270, MAX30102,
  GC9A01 1.28" non-touch, INMP441, MAX98357A + 20 mm speaker, TP4056, ME6211C33M5G-N, USB-C 16P,
  600 mAh LiPo, 6x6 tactile buttons, passives.
- **2-layer PCB design rules defined** (Procurement Spec §3): 15 mm antenna keepout (zero copper),
  independent 5.1 kΩ CC pull-downs, GPIO19/20 90 Ω differential USB routing with <0.5 mm length
  match, shared I2C0 4.7 kΩ pull-ups, 0603 passives, 10 µF + 0.1 µF decoupling per rail.
- **Automated PDF generation** — `scripts/generate_hardware_spec_pdf.py` (reportlab) generates
  `docs/KINETIQ_Hardware_Procurement_Spec.pdf`; committed in `c94bbc7`.

**Commits:** `c94bbc7` (scaffold + BOM/PDF), `2fd9bac` (display), `2ab4ec4` (BLE), `c9ddbed` (pin freeze).

## Firmware Lane

- **Repository scaffolding + Zephyr standalone application** initialized: `firmware/CMakeLists.txt`,
  `prj.conf` (LOG, multithreading, FP printf, GPIO/I2C/SPI + BT block), board overlay draft, `src/`.
- **Core data structures + queue architecture**: `sensor_packet_t` (`sensor_packet.h`),
  `K_MSGQ_DEFINE(sensor_msgq, …, 10, 4)`, 4 priority-ordered threads (imu 5 / ppg 6 / infer 7 /
  comm 9) + button thread (8).
- **Button FSM** (`button_fsm.*`): 25 ms symmetric debounce, short-press <800 ms screen cycle
  (HOME→ACTIVITY→BODY→ASSISTANT), long-press ≥800 ms `LONG_PRESS_START` (PTT arm) + release
  `LONG_PRESS_END`; tick-based, host-unit-tested.
- **Activity & exertion engine** (`activity_engine.*`): 128-sample ring (≥1 s @100 Hz),
  g-normalized magnitude variance thresholds 0.05 / 0.35 → REST/WALK/RUN, EMA intensity,
  exertion REST/LIGHT/MODERATE/HIGH from HR + intensity (HR bands 80/100/130).
- **Display engine** (`display_engine.*`): direct-draw 4-screen render state for GC9A01 240×240
  (no LVGL), SpO2 quality gate (`--` unless GOOD), PTT RECORDING state, 240×240 ASCII debug
  renderer (18/18 grid-sim verified), change-gated preview logging.
- **BLE NUS peripheral** (`ble_transport.*`): GATT service + TX notify/CCCD + RX write,
  MTU-aware chunking with newline frame terminator (laptop-reassembler compatible), atomic
  connection state, resilient boot (UART telemetry fallback if BLE init fails), host stubs.
- **Devicetree pin freeze + dual-mode Sensor HAL** (`c9ddbed`): overlay remapped to frozen §11.2
  pins — I2C0 SDA=4/SCL=5 @400 kHz (BMI270@68, MAX30102@57, polled), SPI2 SCK=12/MOSI=11/CS=10
  + DC=9/RST=14/BL=13, I2S0 BCK=8/WS=7/RX=6/TX=17, gpio-keys PTT button GPIO1; zero collisions
  with strapping/USB/flash/octal pins. `sensor_imu.*` / `sensor_ppg.*` wrappers:
  `device_is_ready` → hardware fetch, else synthetic fallback (bit-exact port of the gait/pleth
  waveforms, 0.000 max-diff sims), soft-fail → permanent fallback after >50 errors; button thread
  samples GPIO1 via `gpio_pin_get_dt` with simulated fallback.

**Commits:** `c94bbc7`, `9327d09`, `2fd9bac`, `2ab4ec4`, `c9ddbed`.

## Laptop AI Lane

- **Telemetry schema validator + mock streamer**: strict 8-field locked schema (coercion, enum,
  range checks; 8/8 rejection tests pass), 1 Hz synthetic streamer, dual script/module import.
- **Live ANSI dashboard + CSV logger** (`dashboard.py`): Windows VT enable (ctypes), inverse-cell
  live table, POOR-quality warning tag, SpO2 `--` gate, live rate meter, `session_*.csv` +
  `rejects_*.log` per session, `mock|serial|stdin` sources; 10 s headless test reconciled
  CSV rows == received (100/100) and rejects == dropped (3/3), exit 0.
- **BLE NUS central client** (`ble_client.py`): bleak-based scan/auto-connect (name "KINETIQ" or
  NUS UUID), byte-level frame reassembly (newline + brace-scan, 8 KB cap, UTF-8 safe), exponential
  backoff reconnect (1→2→4→…→30 s, healthy-session reset), schema→CSV→`ContextManager` routing;
  mock loopback reconciled exactly (frames = routed + rejected), 9/9 reassembler checks pass.
- **STT + sensor-grounded advisor loopback**: `assistant.py` (`ContextManager`, `PromptBuilder`
  with locked system prompt, `AIAdvisor` Ollama→API→template fallback, `TTSOutput` pyttsx3 +
  console fallback, `verify_grounding`) and `voice_pipeline.py` (faster-whisper tiny.en int8
  CPU STT, stage-timed benchmark: **STT median 0.45 s, total 0.45 s vs <5.0 s target, grounding
  PASS ×3, exit 0**; `--record` mic mode validated).

**Commits:** `c94bbc7`, `340f9e4`, `5248d21`, `636f61f`, `9ae4110`.

---

## Day-1 Next Up

1. **Procure parts** from the BOM v1.0 distributor links (order spares per §11.6 — no reorders).
2. **KiCad schematic capture** for the 2-layer PCB (`hardware/schematic/`), ERC-clean target,
   freeze schematic end of W2 per risk plan.
3. **`west build` on a Zephyr-SDK host** — all firmware verified structurally + via Python sims
   only so far; first real compile is Day-1 gate #1 (overlay pinctrl syntax to confirm in-tree).
4. **Live BLE bring-up**: watch (`ble_transport`) ↔ laptop (`ble_client --source ble`) with a
   radio in range — mock-verified both ends, real GATT path untested.
5. Pull `gemma2:2b` in Ollama to switch the assistant/voice loopback from template fallback to
   the live LLM path.

> Note: the DT pin freeze + dual-mode Sensor HAL were scheduled as Day-1 items but were
> completed during this Day-0 session (commit `c9ddbed`) — see Firmware Lane above.

## Known Honest Gaps

- No Zephyr SDK / C compiler on the dev host: all C verified by structural checks + Python
  waveform/FSM/render sims (bit-exact), not by compilation. LSP `zephyr/*.h` errors are expected.
- Real BLE radio path, real I2C silicon, and live mic/STT on-device remain untested (hardware).
- SpO2 everywhere is a still-only, non-medical estimate (`--` unless quality GOOD).
- pyttsx3 measured ~13.6 s synchronous TTS vs the 0.7 s Piper target — swap to Piper on Windows
  if the build issue recurs, or accept console-fallback latency for Day-1 demos.
