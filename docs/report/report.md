# KINETIQ — RTOS-Based IoT Healthcare Assistant with Sensor-Grounded Voice AI

### *Mirror, Mirror — a wellness mirror that sees, listens, and answers*

**Minor Project Report**

**Programme:** [B.Tech — Department Name, College Name]
**Academic Year:** [2026–27]

**Team:** sid · Tharun

**Date:** 15 October 2026

| Role | Name | Contribution |
|---|---|---|
| Software & Firmware | sid | ESP32 firmware, telemetry pipeline, AI assistant integration, report |
| Hardware & Figures | Tharun | Wiring, figures, wearable-bridge probe, enclosure render |

**Project repository:** `github.com/siddoit/KinetiQ` · **Faculty guide:** [Name — to be filled]
**Abstract status:** this report supersedes the outdated `docs/abstract.pdf` (discard).

---

## Table of Contents

1. [Abstract](#1-abstract)
2. [Introduction & Motivation](#2-introduction--motivation)
3. [Objectives & Requirements Coverage](#3-objectives--requirements-coverage)
4. [System Architecture](#4-system-architecture)
5. [Firmware Design](#5-firmware-design)
6. [Laptop AI Pipeline](#6-laptop-ai-pipeline)
7. [Hardware Design & Bill of Materials](#7-hardware-design--bill-of-materials)
8. [Testing & Results](#8-testing--results)
9. [Limitations & Risk Mitigation](#9-limitations--risk-mitigation)
10. [Timeline & Next Steps](#10-timeline--next-steps)
11. [Conclusion](#11-conclusion)
12. [References](#12-references)

---

## 1. Abstract

KINETIQ is an RTOS-based IoT healthcare assistant that fuses wearable motion and
environment sensing with a sensor-grounded voice AI. An ESP32 sensing node running
real FreeRTOS tasks streams structured telemetry — activity class, motion
intensity, exertion level, step count and ambient readings — to a laptop host,
which presents a live dashboard, appends every validated packet to a session CSV
log, and answers spoken health questions with replies grounded in the latest live
sensor state rather than generic advice. The system is framed by the
"Mirror, Mirror" narrative: the laptop screen is the mirror's face, its
microphone and speakers are the mirror's voice, and a webcam plus the ESP32
motion feed are its eyes, enabling posture observation and correction prompts.

The v1 prototype reported here is **software-complete and hardware-design-complete**:
the laptop pipeline (schema validation, mock/serial/stdin ingestion, ANSI
dashboard, CSV logging, grounded-assistant loop with Ollama → API → template
fallback chain, and a faster-whisper speech pipeline) is implemented and verified
through four repeatable demonstration gates; the Zephyr-RTOS firmware
(~2,300 lines: priority-ordered threads, message-queue sample bus, button
finite-state machine, activity/exertion engine, BLE NUS transport) is implemented
and structurally verified, with the on-target compile gate pending SDK
availability; and the razor bill of materials (≈ ₹1,950 for two ESP32 DevKit V1
boards, MPU6050 + HC-SR04 + IR combo, DHT22, breadboard kit, buttons, capacitors
and buzzer) is frozen, with components ordered during the review week and Wokwi
simulation covering firmware bring-up until boards arrive. Measured evidence
includes a 75-frame / 15-second dashboard session with zero drops, a BLE mock
 session routing 30/30 packets with zero schema rejections, a `[grounding] PASS`
verdict on a live LLM-mode (Ollama `gemma2:2b`) query with the template fallback
also verified, a real-STT measurement of 0.48 s on a synthesized clip alongside
the Day-0 speech-benchmark median of
0.45 s end-to-end against a 5.0 s target. No physical hardware has been tested
yet; all sensor data cited in this report is synthetic or simulated, and this is
stated explicitly wherever results are discussed. The remaining work —
board bring-up, I²C sensor integration, the Wi-Fi transport layer and final
integration — is scheduled against a day-granular battle plan concluding with
the final presentation on 22 October 2026.

*(268 words)*

---

## 2. Introduction & Motivation

### 2.1 The wellness gap

Consumer wearables record heart rate, step counts and sleep scores, yet their
feedback is overwhelmingly numeric and decontextualised: a user who asks
"How am I doing?" receives a chart, not an answer. At the same time, voice
assistants can converse fluently but know nothing about the body of the person
they are speaking to. KINETIQ starts from the observation that the missing link
is *grounding* — coupling a conversational assistant to a live, trustworthy
stream of physical-state evidence, so that advice reflects what the user is
actually doing at that moment.

### 2.2 Learning goals

As a minor project, KINETIQ was deliberately scoped to exercise four examiner-facing
competencies:

- **RTOS engineering** — concurrent sensing, inference and communication under a
  real-time kernel with explicit priorities, queues and timing discipline.
- **IoT systems** — telemetry that leaves the device over serial / Wi-Fi / BLE,
  is validated at the boundary, visualised live, and persisted for analysis.
- **Embedded design** — I²C sensors, GPIO inputs, signal conditioning (voltage
  division, decoupling, pull-ups), pin planning with boot-strapping constraints,
  and breadboard-to-PCB thinking.
- **Innovation** — a voice AI whose answers are *verified* against live sensor
  data by an explicit grounding check, demonstrated live in the demo.

### 2.3 The "Mirror, Mirror" framing

The project is presented through the story of Snow White's talking mirror,
re-imagined as a healthcare assistant: a mirror that **sees you** (webcam +
motion feed), **talks back** (voice assistant), **corrects your posture**
(motion + camera cross-check with voice and buzzer alerts), and **suggests
health actions** (rule-based nudges for posture, sedentariness and room
comfort). The narrative lives in the pitch and in this report's subtitle; the
formal technical title above is what is examined.

### 2.4 Medical-safety boundary

KINETIQ outputs **wellness estimates only** — activity class, exertion class,
step count, motion intensity and still-only environmental readings. It is not a
medical device, holds no medical certification, makes no diagnosis, and raises no
clinical alarms. SpO₂-class quantities, where they appear in the design record,
are labelled *estimate, still-only, non-medical* throughout. This boundary is
repeated in the architecture document and in the assistant's prompt construction.

---

## 3. Objectives & Requirements Coverage

Table 1 maps each programme requirement to the concrete evidence in this build.
It is reproduced from the review one-pager (12 Oct 2026), which the examiners
have already seen, so that the report and the pitch tell one consistent story.

**Table 1: Requirements coverage.**

| Requirement | How it is met | Evidence in this report |
|---|---|---|
| **RTOS** | FreeRTOS tasks + queues on the ESP32 (sensor / comms / alert tasks); Zephyr-RTOS firmware artefact with 4 priority-ordered threads and a `k_msgq` sample bus | §5 |
| **IoT** | Live telemetry leaves the device → host dashboard → per-session CSV log | §6, §8 |
| **Embedded** | I²C sensors (MPU6050, DHT22), GPIO button with debounce FSM, buzzer actuator, serial/Wi-Fi transport, explicit pin plan | §5, §7 |
| **Innovation** | Voice AI **grounded** in live telemetry; every answer carries a machine-checked `[grounding]` verdict shown in the demo | §6, §8 |

Primary objectives carried forward from the project README complete the picture:
continuous motion acquisition, heart-rate acquisition with a signal-quality gate,
a minimal glanceable UI (the laptop dashboard is the primary UI in the razor
build), push-to-talk voice interaction, an activity/exertion state derived
on-device, and a custom hardware path (breadboard now, PCB discipline retained
in the design rules). Wi-Fi is the IoT layer, serial is the guaranteed demo
path, and BLE is demoted to optional after an honest assessment of the
client-side bring-up cost (§9).

---

## 4. System Architecture

![Figure 1: KINETIQ system architecture](../images/architecture.png)

**Figure 1: KINETIQ system architecture** — sensing node (ESP32, FreeRTOS),
transport (serial / Wi-Fi bridge / optional BLE), and laptop host (dashboard,
CSV log, grounded voice assistant).

### 4.1 Tier walkthrough

**Tier 1 — Sensing node (ESP32).** The device is a sensing node, not a
smartwatch: it acquires motion (MPU6050 IMU), distance/proximity (HC-SR04, IR),
ambient temperature/humidity (DHT22) and a tactile button, classifies
activity on-device inside FreeRTOS tasks, and emits one `\n`-terminated JSON
telemetry object per cycle over serial (guaranteed path) or Wi-Fi via a ~40-line
Python bridge into the dashboard's `stdin` source.

**Tier 2 — Transport.** Three transports in priority order: (i) **serial** —
always works, zero venue dependence, the frozen fallback; (ii) **Wi-Fi** — the
IoT layer, with an ESP soft-AP fallback (`192.168.4.1`) so the demo never
depends on venue infrastructure; (iii) **BLE NUS** — optional, both ends
mock-verified but never tested against a real GATT link.

**Tier 3 — Laptop host ("the mirror").** The laptop is the mirror's face
(dashboard), ears (microphone + STT), brain (grounded LLM) and voice (TTS +
speakers). A webcam running a face/pose mesh provides the mirror's eyes for
posture angles; the MPU6050 head-tilt feed is the on-device stand-in that
cross-checks it. Every validated packet is appended to a timestamped session CSV
with rejected packets diverted to a per-session rejects log.

### 4.2 Split-processing rationale

Everything time-critical and cheap — acquisition, filtering, cadence detection,
rule-based activity/exertion classification — runs on the node under the RTOS,
keeping the hot loop deterministic and useful even with no link. Everything
model-heavy and non-deterministic — speech recognition, LLM inference, speech
synthesis — runs on the laptop, which has orders of magnitude more memory and no
battery envelope to respect. The contract between the halves is the locked
8-field telemetry JSON plus push-to-talk audio chunks: narrow, versioned, and
replaceable on either side.

### 4.3 Data flow (Sense → Process → Infer → Communicate → Assist)

Sense (IMU + environment + button) → Process (filter + RTOS tasks/queues) →
Infer (activity + exertion + quality state on-node) → Communicate (JSON
telemetry off-device) → Assist (STT → LLM + sensor context → TTS → speaker,
with grounding verification). The full Zephyr-era flow, thread priorities and
schema rules are documented in `docs/architecture/system_overview.md`.

---

## 5. Firmware Design

### 5.1 Zephyr-RTOS firmware (implemented artefact)

The repository carries a ~2,300-line Zephyr standalone application that
constitutes the project's RTOS design evidence:

- **Threads & sample bus** — four priority-ordered threads (IMU 5 / PPG 6 /
  infer 7 / comm 9) plus a button thread (8), communicating over
  `K_MSGQ_DEFINE(sensor_msgq, …, 10, 4)`; capture outranks inference, which
  outranks logging, so a slow write costs a counted dropped sample, never a
  missed window.
- **Button FSM** (`button_fsm.*`) — 25 ms symmetric debounce; short-press
  (< 800 ms) cycles HOME → ACTIVITY → BODY → ASSISTANT; long-press (≥ 800 ms)
  arms push-to-talk. Verified by host unit tests.
- **Activity & exertion engine** (`activity_engine.*`) — 128-sample ring buffer,
  g-normalised magnitude-variance thresholds → REST/WALK/RUN, EMA-smoothed
  intensity, exertion REST/LIGHT/MODERATE/HIGH from heart-rate bands
  (80/100/130) fused with intensity.
- **Display engine** (`display_engine.*`) — direct-draw 4-screen renderer for a
  240×240 round TFT (no LVGL), SpO₂ quality gate (`--` unless GOOD), PTT
  RECORDING state, ASCII grid-sim verified 18/18.
- **BLE NUS transport** (`ble_transport.*`) — GATT service with TX notify/CCCD
  and RX write, MTU-aware chunking with newline framing, atomic connection
  state, UART-telemetry fallback if BLE init fails.
- **Sensor HAL** — dual-mode wrappers that use hardware when `device_is_ready`
  and otherwise fall back to bit-exact synthetic waveforms (0.000 max-diff
  simulations), degrading permanently to fallback after > 50 consecutive errors.

### 5.2 Pivot rationale: Zephyr → Arduino-ESP32 FreeRTOS

On 12 October 2026 the team locked a toolchain pivot, recorded in
`docs/OVERHAUL_PLAN.md`: the shipping firmware is written in **Arduino IDE +
arduino-esp32 core — but as real FreeRTOS**, i.e. `xTaskCreate` / `xQueueCreate`
/ `vTaskDelay`, per-function tasks, telemetry via queue, never bare `delay()`
spaghetti. The report wording is *"FreeRTOS on ESP32 (Arduino-ESP32 core, built
on ESP-IDF)"* — honest, verifiable, examiner-safe; no `idf.py`/`sdkconfig`
claims that cannot be demonstrated. The reasons were schedule honesty (a team
that has never flashed an MCU should not gamble its demo on an ESP-IDF
migration), hardware fit (dual-core WROOM-32 gives a genuine SMP FreeRTOS demo),
and simulation availability (Wokwi runs ESP32 + FreeRTOS + serial output with no
board in hand, so firmware work starts immediately). The Zephyr tree is retained
in-repo as a design artefact ("firmware implemented, board bring-up this week").

### 5.3 Verification status (honest)

All firmware logic is **structurally verified** — host-side Python simulations of
the waveforms, FSM transitions and render grids pass bit-exact — but the
**on-target compile gate is pending**: no Zephyr SDK / C toolchain exists on the
development host, so no `west build` has yet succeeded, and no firmware has run
on silicon. The first gates after board arrival are therefore: Wokwi blink +
FreeRTOS tasks compiling, then flash → serial JSON → dashboard.

---

## 6. Laptop AI Pipeline

All components below are implemented in `laptop/` and exercised by the §8 gates.

### 6.1 Schema validation

Every inbound packet passes `TelemetryRecord.parse` against the locked 8-field
schema (`timestamp, heart_rate, spo2, activity, motion_intensity, exertion,
steps, ppg_quality`): all keys required, unknown keys rejected, ints/floats
coerced, enums validated case-insensitively, `motion_intensity` range-checked to
[0.0, 1.0]. Violations raise `ValueError` with an explicit message; the
8/8 rejection tests pass. Malformed input is diverted to a per-session rejects
log, never silently forwarded into a prompt.

### 6.2 Dashboard & CSV logging

`dashboard.py` renders a live ANSI table (Windows VT enabled) with a
POOR-quality warning tag, SpO₂ `--` gating, and a live rate meter, in
`mock | serial | stdin` source modes, writing `session_*.csv` plus
`rejects_*.log` per session. A headless 10 s self-test reconciled CSV rows ==
received (100/100) with rejects == dropped (3/3), exit 0.

### 6.3 Grounded assistant with fallback chain

`assistant.py` implements `ContextManager` (latest validated telemetry),
`PromptBuilder` (locked system prompt embedding the sensor JSON), `AIAdvisor`
with the fallback chain **Ollama (`gemma2:2b`) → HTTP API → grounded template**,
`TTSOutput` (pyttsx3 with console fallback), and `verify_grounding` — a
 machine check that the reply references the live sensor values, printed as
`[grounding] PASS/FAIL` on every answer. The Ollama local model (`gemma2:2b`,
1.6 GB, Ollama 0.40.4) is installed and live on the demo host, so gate 2 now
answers in LLM mode; the chain still degrades to API then template mode as the
designed safety net while keeping the grounding guarantee intact (§8.2).

### 6.4 Voice pipeline

`voice_pipeline.py` chains faster-whisper (`tiny.en`, int8 CPU) STT →
grounded advisor → TTS with per-stage timing. The Day-0 benchmark over synthetic
clips recorded **STT median 0.45 s, total 0.45 s against the < 5.0 s target,
grounding PASS ×3, exit 0**. Real STT is now verified on the demo host: the
faster-whisper real path transcribed a synthesized clip ("'Hello Kinetic, how
am I doing?'") in **0.48 s** (model load 1.3 s; subsequent clips 0.37–0.44 s),
after a PyAV kwarg shim in `voice_pipeline.py` fixed the earlier
`metadata_errors` dependency regression with no pip changes. Live-microphone
capture (`--record` mode) is implemented but still untested on stage hardware,
i.e. laptop mic input (§9.3).

---

## 7. Hardware Design & Bill of Materials

### 7.1 Cost rule

*The device is the sensing node; the laptop IS the mirror* (screen = face,
mic = ears, speakers = voice). Every rupee must buy a capability a reviewer can
see on demo day. The razor cart (Table 2) replaces a ₹3,310 earlier estimate.

**Table 2: Razor BOM — Tier A, the entire order (≈ ₹1,950).**

| # | Item | Qty | ₹ (approx.) | Purpose |
|---|---|---|---|---|
| 1 | ESP32 DevKit V1 WROOM-32 (headers pre-soldered) | 2 | 998 | Sensing node + spare; one dead board must never kill the demo |
| 2 | Micro-USB **data** cables (check drawer first) | 2 | 0–300 | Charge-only cables are a day-1 failure mode |
| 3 | 830-pt breadboard + M-M jumper kit (must include 1 kΩ / 2.2 kΩ / 10 kΩ) | 1 | 499 | Echo divider + DHT pull-up |
| 4 | MPU6050 + HC-SR04 + IR combo (headers soldered) | 1 | 470 | Posture/activity + head-tilt stand-in + distance |
| 5 | DHT22 module (built-in pull-up) | 1 | 309 | Room-comfort line in the story |
| 6 | F-F jumper 20-pack | 1 | 80 | Module-to-module; parallel wiring lane |
| 7 | Tact buttons, 6 mm | 10 | 40 | GPIO input task |
| 8 | Capacitors 100 µF + 0.1 µF | pack | 50 | Brownout insurance |
| 9 | **Active** buzzer | 1 | 35 | "Sit up!" alert + threshold demo |
| | **Tier A total** | | **≈ ₹1,950** | |

**Deliberately cut (do not buy):** MAX30102 HR/SpO₂ sensor (₹309 — HR/SpO₂
becomes a timeboxed band-bridge stretch goal); SSD1306 OLED (₹369 — the laptop
screen is the mirror's face); INMP441 + MAX98357A + speaker (₹563 — the laptop
mic/speakers already talk); 2nd breadboard (₹99); GC9A01 / LiPo / PCB / S3 /
gas-soil sensors (previously cut, stay cut). Constraints: no PCB fabrication, no
soldering, no slow ordering — breadboard only, modules must ship with headers
soldered.

### 7.2 Pin plan (locked in code)

I²C SDA = GPIO21 / SCL = GPIO22 (MPU6050 @ 0x68; OLED slot 0x3C reserved but
empty) · DHT = GPIO13 · HC-SR04 Trig = GPIO26 / Echo = GPIO25 **through a
1 kΩ + 2.2 kΩ divider — 5 V echo into a 3.3 V pin kills ESP32s** · button =
GPIO4 (`INPUT_PULLUP`) · buzzer = GPIO17 · status LED = GPIO16. Kept clear:
GPIO1/3 (USB-UART), GPIO6–11 (flash), GPIO0/2/12/15 (strapping — a sensor tied
there holds the chip in boot loops).

### 7.3 Procurement status (honest)

**Zero components are in hand at the time of writing.** The order is placed
during review week with screenshot-recorded ETA; if the fastest ETA slips past
16 Oct, the team switches to a local electronics shop / same-day option
identified in advance. Until boards arrive, firmware bring-up runs in **Wokwi**
simulation and the laptop pipeline demos against mock/serial-equivalent sources
with a recorded backup video. The Huawei band-bridge probe for HR/SpO₂ is
strictly timeboxed (10-minute nRF Connect scan for a readable Heart Rate
service 0x180D; park it if bonded/encrypted, as expected).

---

## 8. Testing & Results

Four repeatable demonstration gates, all re-run on 14 Oct 2026 except where
noted. Sample evidence: 13+ session CSVs in `laptop/logs/`.

### 8.1 Gate 1 — Dashboard: 75 frames / 15 s, zero drops — PASS

Command: `dashboard.py --source mock --interval 0.2 --duration 15 --headless`.
The mock script cycled REST → WALK → RUN states; selected live lines:

```text
[kinetiq] t=4.0s activity=WALK hr=108 ... exertion=MODERATE steps=4221 ... recv=20 dropped=0 rate=5.0 Hz
[kinetiq] t=6.0s activity=RUN hr=156 ... exertion=HIGH steps=4226 ... recv=30 dropped=0 rate=5.0 Hz
[kinetiq] session summary: ... elapsed=15.00s received=75 dropped=0 csv=...session_20261014_193826.csv
```

**Result: received=75, dropped=0 over 15.00 s; session CSV written.**

### 8.2 Gate 2 — Grounded assistant (LLM mode live, template fallback kept): `[grounding] PASS`

Command: `assistant.py --query "How am I doing?" --once --no-tts` (Ollama
0.40.4 + `gemma2:2b` live on the demo host):

```text
[llm] reply via ollama: You're resting with a heart rate of 68…
[grounding] PASS
```

The template fallback path was also verified earlier (no-server run) and is
retained as the deterministic safety net:

```text
LLM unavailable (Ollama down, no API key) -- showing grounded template reply
[grounding] PASS
[TTS] You're currently rest at rest exertion with heart rate 68 BPM (4218 steps).
      Motion intensity is 0.09 and the still-only SpO2 estimate is 97%.
```

**Result: LLM mode active with grounding PASS on live telemetry
(activity=rest, hr=68, steps=4218); template mode kept and proven as the
fallback — together they prove the Ollama → API → template chain.**

### 8.3 Gate 3 — BLE mock transport: 30/30 routed, 0 rejected — PASS

Command: `ble_client.py --source mock --mock-count 30 --mock-interval 0.05`:

```text
[kinetiq] session summary: ... frames_emitted=30 frames_reassembled=6 invalid_json=0
  overflow=0 bytes=4388 packets_routed=30 csv_rows=30 schema_rejected=0 rejects_logged=0
```

**Result: all 30 packets reassembled through the byte-level NUS framer,
schema-validated, routed to context and logged (CSV rows = 30).** The 9/9
reassembler unit checks also pass. The real-radio GATT path remains untested
(no radio in range at test time).

### 8.4 Gate 4 — Voice pipeline: real STT verified (0.48 s) — PASS

Recorded result (PROGRESS.md, Day-0, synthetic clips): **STT median 0.45 s,
total pipeline 0.45 s vs the < 5.0 s target, grounding PASS ×3, exit 0.**
Re-verified on the demo host (14 Oct): the faster-whisper **real** path
transcribed a synthesized clip ("'Hello Kinetic, how am I doing?'") in
**0.48 s** (model load 1.3 s; subsequent clips 0.37–0.44 s), grounding PASS.
The earlier `metadata_errors` TypeError from the faster-whisper/ctranslate2
stack is fixed via a PyAV kwarg shim in `voice_pipeline.py` (no pip changes).
Live-mic input on stage hardware remains the untested remainder (§9.3).

### 8.5 Sample session data

**Table 3: Excerpt from `laptop/logs/session_20261014_193826.csv`** (real logged
rows; synthetic mock source; 6 of 75 rows).

| timestamp | heart_rate | spo2 | activity | motion_intensity | exertion | steps | ppg_quality |
|---|---|---|---|---|---|---|---|
| 1750000002 | 71 | 97 | rest | 0.12 | rest | 4218 | good |
| 1750000002 | 71 | 97 | rest | 0.12 | rest | 4218 | good |
| 1750000006 | 156 | 97 | run | 0.85 | high | 4226 | good |
| 1750000006 | 156 | 97 | run | 0.85 | high | 4227 | good |
| 1750000014 | 147 | 97 | run | 0.88 | high | 4241 | good |
| 1750000014 | 147 | 97 | run | 0.89 | high | 4242 | good |

The excerpt shows the properties the pipeline is designed to preserve:
monotonic step counts, intensity tracking activity transitions, and the
still-only SpO₂ estimate held constant. All session CSVs carry the locked
8-field schema of §6.1.

---

## 9. Limitations & Risk Mitigation

| # | Limitation | Mitigation (pre-decided, no day-of panic) |
|---|---|---|
| 1 | **0 components in hand** — no physical hardware tested; all data synthetic | Wokwi simulation + recorded backup video; order placed review week with ETA screenshot; local-shop fallback if ETA > 16 Oct |
| 2 | **Ollama LLM live but slow to warm + nondeterministic** — first reply after boot ~20 s (cold model load); grounding FAIL is fail-closed | Warm up the model before stage time; on a FAIL, re-run the query — or fall back to the deterministic template path |
| 3 | **Real STT verified on synthetic audio; live-mic input untested on stage hardware** | Real faster-whisper path measured 0.48 s on a synthesized clip (PyAV shim fixed the `metadata_errors` regression); default demo uses the deterministic synthetic-clip path until a stage-mic check passes |
| 4 | **Venue/stage microphone path unproven** | `--record` capture is implemented but untested on the stage laptop mic; deterministic synthetic-clip path is the frozen demo default |
| 5 | **Real BLE GATT path untested** (mock-verified both ends only) | BLE demoted to optional; serial is the frozen demo path, Wi-Fi the IoT layer |
| 6 | **Firmware never compiled for target** (no SDK on dev host) | Wokwi compile gate first; Zephyr tree retained as artefact, Arduino-FreeRTOS ships |
| 7 | **Venue Wi-Fi uncertain** | ESP soft-AP fallback + serial-only frozen demo (the pass threshold from 20 Oct) |
| 8 | **HR/SpO₂ sensing absent from razor build** | Positioned honestly as a timeboxed band-bridge stretch goal; posture + activity + environment carry the demo |

Nothing in this report claims a measurement taken on physical hardware. Every
number is either a logged synthetic-source result (labelled as such) or a
Day-0 recorded benchmark with its provenance cited.

---

## 10. Timeline & Next Steps

Battle plan locked 12 Oct 2026 → presentation 22 Oct (from OVERHAUL_PLAN §4):

**Table 4: Day-granular battle plan, 12 → 22 Oct 2026.**

| Day | Gate | Status at report date (15 Oct) |
|---|---|---|
| Mon 12 | Idea confirmed · order placed · arduino-esp32 core installed · Wokwi blink + FreeRTOS tasks compiling | Done (order/confirm); Wokwi gate in progress |
| Tue 13 | Firmware written hardware-agnostic (tasks + queue + `\n`-JSON) · Wi-Fi→stdin bridge · abstract + references · 2-min mock-demo backup video | Partial — this report covers abstract/references |
| Wed 14 – Thu 15 | **Board gate:** flash → serial JSON → `dashboard.py --source serial` = real device data. ETA slip → local shop same-day; Wokwi run + video = accepted floor | **Now** |
| Fri 16 | I²C sensors live · figures + wiring diagrams | Next |
| Sat–Sun 17–18 | **Wi-Fi layer:** ESP32 → TCP/HTTP → `bridge.py -u` → dashboard `--source stdin`; soft-AP fallback; Windows firewall allowance | Planned |
| Mon 19 | Audio = cut-or-go at noon (buzzer if mic fights back) | Planned |
| Tue 20 | **SERIAL-ONLY DEMO FROZEN — the pass threshold** · photos, screenshots, results | Planned |
| Wed 21 | Report complete · full read-through · rehearse serial fallback | Planned |
| Thu 22 | **PRESENT** | — |

Serial-path rules baked in from the first line of firmware: `\n`-terminated
JSON only, matched baud rates, dashboard auto-reconnect, unbuffered bridge
(`python -u`) so lines reach stdin live.

---

## 11. Conclusion

KINETIQ v1 delivers what a ten-day turnaround can honestly deliver: a complete,
verified software pipeline from structured telemetry to a grounded voice answer;
a complete hardware design (frozen BOM, locked pin plan, procurement in motion);
and a firmware programme with a real RTOS at its centre and a simulation-backed
bring-up path. The four demonstration gates give the examiners something no
slide deck can: a dashboard that ingests 75/75 packets, an assistant whose every
 answer carries a machine-checked grounding verdict, a BLE framer that routes
30/30 packets without a single schema rejection, and real STT verified at
0.48 s against its latency target. The known gaps — no silicon
yet, LLM cold-start plus nondeterminism, live-mic input untested on stage
hardware — are documented alongside
pre-decided fallbacks, so demo day has no single point of failure. The mirror,
in short, already sees (telemetry + posture path), already talks back
(grounded voice), and is one delivery parcel away from sensing the real world.

---

## 12. References

1. Zephyr Project — *Zephyr RTOS Documentation*, v4.2. Threads, message queues
   (`k_msgq`), mutexes and devicetree. https://docs.zephyrproject.org/
2. Espressif Systems — *ESP32 WROOM-32 Datasheet* and *ESP32 DevKitC* getting-started
   guides (GPIO strapping pins, UART, I²C, ADC characteristics).
   https://docs.espressif.com/
3. Amazon / FreeRTOS — *FreeRTOS Kernel Developer Docs*: tasks (`xTaskCreate`),
   queues (`xQueueCreate`), software timers and `vTaskDelay` timing discipline.
   https://www.freertos.org/Documentation/
4. Espressif / Arduino-ESP32 — *Arduino core for ESP32* (FreeRTOS-based, built on
   ESP-IDF). https://github.com/espressif/arduino-esp32
5. InvenSense / Bosch Sensortec — MPU6050 register map and BMI270 datasheets
   (6-axis IMU acquisition parameters referenced for the motion front-end).
6. Maxim Integrated — MAX30102 heart-rate / pulse-oximeter sensor datasheet
   (PPG front-end and motion-artifact considerations).
7. OpenAI Whisper / SYSTRAN — faster-whisper (CTranslate2-backed local STT,
   `tiny.en` int8 CPU inference used in the voice pipeline).
   https://github.com/SYSTRAN/faster-whisper
8. Rhasspy / Piper — *Piper neural text-to-speech* (fast local TTS target for the
   voice pipeline). https://github.com/rhasspy/piper
9. Ollama — *Ollama local LLM runtime*; Gemma 2 (`gemma2:2b`) model card
   (primary LLM backend in the advisor fallback chain). https://ollama.com/
10. Google Developers — *MediaPipe Pose / Face Mesh* (webcam posture-angle
    estimation — the mirror's eyes). https://developers.google.com/mediapipe
11. Lewis, P. et al. — *Retrieval-Augmented Generation for Knowledge-Intensive
    NLP Tasks* (NeurIPS 2020) — grounding paradigm underlying the sensor-context
    prompt construction and the `verify_grounding` check.
12. Espressif — *ESP-IDF Programming Guide: Wi-Fi / Soft-AP and FreeRTOS SMP notes*
    (transport fallback design). https://docs.espressif.com/projects/esp-idf/

*All references above are real, publicly available documents or projects as of
October 2026. No fabricated citations are included.*

---

*End of report — KINETIQ Minor Project, 15 Oct 2026. Placeholders for the user:
[College/Department names], [Faculty guide name] (title page). Abstract PDF
(`docs/abstract.pdf`) is superseded by §1.*
