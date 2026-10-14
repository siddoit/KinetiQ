# KINETIQ — RTOS-Based IoT Healthcare Assistant with Sensor-Grounded Voice AI

*"Mirror, Mirror"*: the laptop screen is the mirror's face, its mic and speakers
are the mirror's voice; the ESP32 is the sensing node that lets it see you.
A laptop-hosted voice assistant hears and answers the user, watches posture via
a live motion feed, and grounds every reply in real sensor telemetry.

Team: Sid (software/firmware) + Tharun (wiring, figures). V1 final review:
15 Oct 2026. Full version target: 22 Oct 2026. Pivot locked 2026-10-12 —
see `docs/OVERHAUL_PLAN.md`.

The pipeline is **Sense → Process → Infer → Communicate → Assist**.
Heavy AI (STT, LLM, TTS) runs on the laptop; the device is a sensing node.

---

## Status — what is true the night before V1 review

### Works TODAY (zero hardware needed)

- **Mock telemetry dashboard + CSV logging** — `dashboard.py --source mock`
  streams live telemetry frames to a dashboard and logs them to CSV
  (75 frames / 15 s in the gate run).
- **BLE mock client** — `ble_client.py --source mock` drives 30 mock packets
  through the same session/routing path the real GATT path will use.
- **Sensor-grounded assistant** — `assistant.py` grounds replies in live
  telemetry via Ollama `gemma2:2b` (local LLM) with an API/template fallback
  chain, plus a fail-closed grounding check (`[grounding] PASS` shown live —
  ungrounded replies are rejected, not spoken).
- **Voice pipeline** — `voice_pipeline.py` does STT → grounded LLM → TTS with
  REAL faster-whisper STT (0.48 s measured on a synthetic clip).

### Hardware — design complete, bring-up is next week

- Razor BOM ≈ ₹1,950 (2× ESP32 DevKit V1 WROOM-32, MPU6050 + HC-SR04 + IR
  combo, DHT22, breadboard + jumpers, buttons, caps, active buzzer) + pin plan
  (I2C SDA=21/SCL=22, DHT=GPIO13, sonar Trig=26/Echo=25 via divider,
  button=GPIO4, buzzer=GPIO17, LED=GPIO16). Components ordered.
- **Wokwi simulation in `hardware/sim/` runs in the browser today** — real
  FreeRTOS firmware (`sketch.ino`: tasks + queues, `\n`-JSON telemetry) with
  the exact pin plan, no board needed. See `hardware/sim/README.md`.
- Physical bring-up + serial → dashboard bridge (`--source serial`) is
  next week's work.

### Legacy — de-scoped by the pivot, kept for reference

- `firmware/` is the Zephyr tree (structural verification only). It is NOT
  shipping — toolchain is Arduino-ESP32 core written as real FreeRTOS
  (`xTaskCreate` / `xQueueCreate` / `vTaskDelay`, report wording: "FreeRTOS on
  ESP32"). Reusable ideas, not the demo path.

---

## Quickstart (Windows, venv `laptop\.venv`)

```bat
laptop\.venv\Scripts\activate
```

Four gate commands (run from the repo root):

```bat
python laptop\receiver\dashboard.py --source mock --duration 15 --headless
python laptop\ai\assistant.py --once --query "How am I doing?" --no-tts
python laptop\receiver\ble_client.py --source mock --mock-count 30
python laptop\ai\voice_pipeline.py --no-tts
```

Demo notes:

- **Warm up Ollama before demoing** — the first LLM reply takes ~20 s
  (model load); later replies are fast. If Ollama is down, the assistant
  automatically falls back to the template backend instead of failing.
- Wokwi simulation: open `hardware/sim/sketch.ino` + `diagram.toml` in the
  browser editor — full steps in `hardware/sim/README.md`.
- Native device node (no browser): build once via `hardware\native\build.ps1`,
  then pipe live RTOS telemetry into the dashboard — see below.

### Local device node

Real FreeRTOS v11 kernel on the Windows simulator port (keys 1/2/3/b/0),
stdout = 1 Hz JSON telemetry, stderr = RTOS status. Live pipe:

```bat
hardware\native\kinetiq_native.exe | laptop\.venv\Scripts\python.exe laptop\receiver\dashboard.py --source stdin --headless
```

Expected outputs (so you know a run is healthy):

- Dashboard: one telemetry JSON frame per tick for the full duration, then a
  CSV log under `laptop/logs/`.
- Assistant: the grounded reply printed to console + `[grounding] PASS`.
- BLE client: 30 mock packets routed through the session path, then a
  summary line.
- Voice pipeline: STT transcript → grounded reply → `[grounding] PASS`.

Transport order (locked by the pivot): **serial = guaranteed path**
(`--source serial`, newline-JSON, matched baud, dashboard auto-reconnect),
**WiFi = IoT layer** (ESP32 → TCP/HTTP → Python bridge → `--source stdin`,
run the bridge with `python -u`), **BLE = optional** (mock client only until
a real GATT server exists). Venue-proof fallback: ESP32 soft-AP
(`192.168.4.1`) + laptop joins it, so the demo never depends on hall WiFi.

---

## Hardware (razor BOM ≈ ₹1,950 — ordered, arrives for bring-up week)

| # | Item | Purpose |
|---|---|---|
| 1 | ESP32 DevKit V1 WROOM-32 ×2 (headers pre-soldered) | Sensing node (+1 spare — one dead board = dead project) |
| 2 | MPU6050 + HC-SR04 + IR combo | Posture/activity + head-tilt stand-in + distance |
| 3 | DHT22 module (built-in pullup) | Room temperature/humidity for the comfort line |
| 4 | 830-pt breadboard + M-M jumpers, F-F 20-pack | Wiring (echo divider + DHT pullup inside) |
| 5 | Tact buttons, 100µF + 0.1µF caps | GPIO input task + brownout insurance |
| 6 | Active buzzer | "Sit up!" posture alert + threshold demo |
| 7 | Micro-USB **data** cables ×2 | Charge-only cables are a day-1 failure — verify |

Deliberately cut: MAX30102 HR/SpO2 (band bridge is the stretch path),
SSD1306 OLED (the laptop screen IS the mirror's face), INMP441 + MAX98357A +
speaker (laptop mic/speakers already talk), 2nd breadboard, PCB/soldering gear.

Pin plan (locked in code): I2C SDA=21 / SCL=22 · DHT=GPIO13 · sonar
Trig=26 / Echo=25 (**echo through a 1k+2k2 divider — 5 V kills 3.3 V pins**)
· button=GPIO4 (`INPUT_PULLUP`) · buzzer=GPIO17 · status LED=GPIO16.
Keep clear: GPIO1/3 (USB-UART), 6–11 (flash), 0/2/12/15 (strapping).

---

## V1 demo script (15 Oct)

1. Dashboard mock run — live telemetry + CSV proof, zero hardware.
2. Wokwi sim — real FreeRTOS firmware streaming `\n`-JSON in the browser.
3. Assistant `--once` — grounded reply + `[grounding] PASS` on screen.
4. Voice pipeline — speak, transcribe (faster-whisper), grounded spoken reply.
5. Honest floor if anything dies: recorded mock-demo video + serial-path
   laptop-side run. HR/SpO2 bridging is named as a stretch goal, never faked.

---

## Repo map

```text
laptop/          receiver (dashboard, mock_streamer, ble_client) + ai (assistant, voice_pipeline)
firmware/        legacy Zephyr tree — de-scoped, reference only
hardware/sim/    Wokwi browser simulation (sketch.ino + diagram.toml + README)
hardware/native/ Native FreeRTOS v11 device node (Windows sim port, keyboard-driven, pipes to dashboard)
docs/            report (docs/report/report.md | .docx), slides
                 (docs/final review/KINETIQ_V1_final_review.pptx),
                 pivot plan (docs/OVERHAUL_PLAN.md),
                 architecture note (docs/architecture/system_overview.md)
scripts/         one-off generators (hardware spec PDF)
```

---

## Requirements coverage

- **RTOS** → FreeRTOS tasks/queues on ESP32 — live in the Wokwi sim sketch,
  designed the same way for the incoming firmware.
- **IoT** → telemetry off-device into the laptop dashboard and into CSV logs.
- **Embedded** → I2C (MPU6050) / GPIO (button, buzzer, LED) / serial JSON
  telemetry path (`--source serial` ready for bring-up week).
- **Innovation** → voice AI grounded in live sensor data, enforced by the
  fail-closed grounding check, demonstrated live.

---

## Safety boundary

Every number KINETIQ produces is a wellness estimate, not a medical measurement.
Not a medical device, not a diagnostic tool. HR/SpO2 from a wearable bridge is
an explicitly timeboxed stretch goal — the sensing-and-assistance pipeline is
sensor-agnostic and the V1 demo makes no clinical claims.
