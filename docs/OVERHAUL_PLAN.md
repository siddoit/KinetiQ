# KINETIQ — Overhaul Plan (locked 2026-10-12)

> Sprint plan after the 10-10 pivot discussion. Written 12 Oct 2026, the day of the
> idea-confirmation review. **Final presentation: 22 Oct 2026.**
> Team: sid (software/firmware) + Tarun (CAD, wiring, figures). Shahz not involved.
> Constraints: no PCB fabrication, no soldering, no slow ordering. Breadboard only.

---

## 0. Decisions locked (2026-10-12)

| Decision | Choice | Why |
|---|---|---|
| **Board** | **ESP32 DevKit V1 WROOM-32 ×2** | ₹459 verified, next-day, breadboard-friendly, dual-core (real SMP FreeRTOS demo), 2×I2S if audio survives, every example exists. S3 skipped (no round display/PSRAM need), C3 skipped (too weak). |
| **Toolchain** | **Arduino IDE + arduino-esp32 core** — but written as **real FreeRTOS** | `xTaskCreate` / `xQueueCreate` / `vTaskDelay`, task files, telemetry via queue, no `delay()` spaghetti. Report wording: *"FreeRTOS on ESP32 (Arduino-ESP32 core, built on ESP-IDF)"*. ESP-IDF migration declined: schedule risk > purity for a team that has never flashed an MCU. Simulate first in **Wokwi** (works without hardware). |
| **Display** | **Laptop dashboard is the primary UI** | Most feasible, zero extra cost. Optional ₹369 SSD1306 OLED = on-device glance + I²C proof — it is *not* a second screen; buy only if it doesn't slow the order. |
| **Transport** | **Serial = guaranteed path, WiFi = IoT layer, BLE = optional** | `dashboard.py --source serial` already exists. WiFi → ~40-line Python bridge → `--source stdin`. BLE demoted (repo client never tested against real GATT; firmware GATT server = extra work). |
| **Display/venue** | Laptop + screen; WiFi **or** hotspot will be available | Still keep the ESP-AP fallback (§5) so the demo never depends on venue WiFi. |
| **Narrative (12 Oct)** | **"Mirror, Mirror"** — Snow White's talking mirror, as a healthcare assistant: sees you, talks back, corrects posture, suggests. Laptop screen = the mirror's face, laptop mic/speakers = its voice. | Hook lives in the pitch + a report subtitle; technical title stays examiner-safe (§1). |
| **Cost rule (12 Oct)** | **Minimise ruthlessly: device = sensing node, laptop = the mirror.** Every rupee must buy a capability a reviewer can see. | Razor cart in §3 ≈ ₹1,950 (was ₹3,310). |

## 1. Idea (narrative locked 12 Oct — "Mirror, Mirror")

**The story:** Snow White's queen asked the mirror and the mirror talked back.
This project builds the healthcare version — a mirror that **sees you** (camera +
motion), **talks back** (voice assistant), **corrects your posture**, and
**suggests health actions**, grounded in live sensor data. The hook is for the
pitch and a report subtitle (*"Mirror, Mirror — an RTOS-based healthcare
assistant"*); the technical title below is what gets graded.

**Title:** *KINETIQ — RTOS-Based IoT Healthcare Assistant with Sensor-Grounded Voice AI*
(keep the registered/AMS wording if it already differs; "Mirror, Mirror" is a
subtitle, never the formal title.)

**One-liner:**
> A laptop-hosted voice assistant — "the mirror" — sees the user via webcam,
> hears and answers them, and corrects posture using a live motion feed driven
> by an ESP32 RTOS sensing node streaming telemetry over serial/WiFi.

**Story beat → buildable mechanism (locked mapping):**

| Story beat | Mechanism | Tier |
|---|---|---|
| Mirror talks back | laptop mic → offline STT → grounded LLM → TTS — **already built and working** | core |
| Sees you / expression | laptop webcam: face-mesh → head-tilt + posture angles (MediaPipe, free). No camera exists on the ESP32 — the webcam is the mirror's eyes; MPU6050 head-tilt is the on-device stand-in | webcam = core; fine-grained expression = stretch |
| Posture correction | MPU6050 node + webcam angle cross-check → "sit up" voice + buzzer alert | core |
| Health sensors (HR/SpO2) | **Huawei band bridge = stretch, strictly timeboxed** (§3). Without it: posture + activity + environment, claimed honestly | stretch |
| Health suggestions | assistant already grounds replies in telemetry; add simple rules (posture bad, sedentary, temp high) | core |
| Mirror's face | laptop screen (free). Optional ₹150 acrylic + dark paper locally for the reflection illusion | free |

**Punching points (unchanged):** RTOS = FreeRTOS tasks/queues on the ESP32 ·
IoT = live telemetry off-device to the dashboard · Embedded = I²C/GPIO/serial ·
Innovation = voice AI **grounded** in that live data (`[grounding] PASS` shown live).

**1-pager for the review:** story hook (2 lines) · technical one-liner · block
diagram (`docs/architecture/system_overview.md`) · razor BOM ≈₹1,950 (§3) ·
roles (sid = software/firmware, Tarun = wiring/figures/band hunt) · timeline to
22 Oct · fallback line: *"health-metric bridging from a wearable is a stretch
goal; the sensing-and-assistance pipeline is sensor-agnostic."*

## 2. Toolchain rule (locked)

- **Arduino IDE + arduino-esp32 core** (already the team's comfort zone — no change
  required). Discipline rules so the RTOS claim is real and demonstrable:
  - each function is a **FreeRTOS task** (`xTaskCreate`), shared data goes through
    **queues** (`xQueueCreate`), pacing is `vTaskDelay()`, never bare `delay()`.
  - Report wording: *"FreeRTOS on ESP32 (Arduino-ESP32 core, built on ESP-IDF)"* —
    honest, verifiable, examiner-safe. Never claim `idf.py`/`sdkconfig` that cannot
    be demonstrated.
- **Simulate before hardware:** build + run the firmware in **Wokwi** (ESP32 +
  FreeRTOS + serial output) — lets all firmware work start **today**, before the
  board even arrives.
- **Zephyr is not shipping.** Keep the 2,300-line firmware in-repo as an artifact
  ("firmware implemented, board bring-up this week"). No toolchain setup gamble.

## 3. Hardware list — RAZOR VERSION, confirmed 2026-10-12 (cost-minimised)

**Cost rule: the device is the sensing node; the laptop IS the mirror** (screen =
face, mic = ears, speakers = voice). Every rupee must buy a capability a reviewer
can see on the 22nd.

**TIER A — the entire order (≈₹1,950)**

| # | Item | Qty | ₹ | Purpose / note |
|---|---|---|---|---|
| 1 | ESP32 DevKit V1 WROOM-32 (**headers pre-soldered**) | 2 | 998 | One dies → dead project. |
| 2 | Micro-USB **data** cables | 2 | 0–300 | Charge-only cable = day-1 failure. Check the drawer first. |
| 3 | 830-pt breadboard + M-M jumper kit (verify 1kΩ/2k2Ω/10kΩ resistors inside; else +₹30 assortment) | 1 | 499 | Echo divider + DHT pullup. |
| 4 | MPU6050 + HC-SR04 + IR combo (**headers soldered**) | 1 | 470 | Posture/activity + head-tilt stand-in + distance. |
| 5 | DHT22 module (built-in pullup) | 1 | 309 | Environment (room comfort line in the story). |
| 6 | F-F jumper 20-pack | 1 | 80 | Module-to-module; lets Tarun wire off-board while sid flashes (replaces 2nd breadboard). |
| 7 | Tact buttons 6 mm | 10 | 40 | GPIO input task. |
| 8 | Caps: 100µF + 0.1µF | pack | 50 | Brownout insurance. |
| 9 | **Active** buzzer | 1 | 35 | "Sit up!" alert + threshold demo. |
| | **Tier A total** | | **≈₹1,950** | |

**CUT — deliberately, do not buy:**

| Cut | Saves | Why |
|---|---|---|
| MAX30102 HR/SpO2 sensor | ₹309 | HR/SpO2 story = band bridge (stretch). Onboard PPG needs validated finger contact + gain tuning — days we don't have. |
| SSD1306 OLED | ₹369 | The laptop screen **is** the mirror's face. |
| INMP441 + MAX98357A + speaker | ₹563 | Laptop mic + speakers do all the talking (pipeline already works). |
| 2nd breadboard | ₹99 | F-F jumpers cover parallel wiring. |
| GC9A01 · LiPo · PCB · S3 · soldering gear · gas/soil sensors | — | Previously cut, stays cut. |

**Free additions:** phone = second camera/mic + hotspot · nRF Connect app (band
BLE scan, free) · MediaPipe/OpenCV (webcam posture + expression, free) · acrylic
sheet + dark paper ~₹150 local = optional "mirror" frame illusion (Tarun CAD-frames
it; never blocks the demo).

**Huawei band — timebox STRICTLY (Tarun, tonight):**
1. **10 minutes:** nRF Connect → scan → look for Heart Rate (0x180D) or any readable
   service. **Instant win →** bridge it (hours of work, not days) and HR/SpO2 enters
   the demo as a real feature.
2. **If nothing is readable (expected — Huawei Health holds an exclusive bonded
   link): park it.** Do NOT reverse-engineer the app: Huawei's BLE is
   bonded+encrypted; full RE is a multi-week job, not a 10-day one. The report
   then positions HR/SpO2 as a *bridged stretch goal*, with the browser-Bluetooth
   demo (§5) as the honest UI prototype.

**Power:** one board off laptop USB; both boards → 5V/2A phone charger / powered hub.

**Pin plan (lock this in code):** I²C SDA=21 / SCL=22 (MPU 0x68; OLED slot 0x3C
reserved but empty) · DHT=GPIO13 · HC-SR04 Trig=26 / Echo=25 (**echo via 1k+2k2
divider — 5V into a 3.3V pin kills ESP32s**) · button=GPIO4 (INPUT_PULLUP) ·
buzzer=GPIO17 · status LED=GPIO16.
**Keep clear:** GPIO1/3 (USB-UART), 6–11 (flash), 0/2/12/15 (strapping — a sensor
tied there holds the chip in boot loops).

**Day-1 setup gotchas:** CH340/CP2102 driver if no COM port appears · confirm cable
is data-capable · modules must ship with headers soldered (no soldering allowed).

**Place the order immediately after the review confirms the idea.** Screenshot the
delivery ETA at checkout. **If the fastest ETA is later than 16 Oct: switch to a
local electronics shop / same-day delivery** — identify the nearest option *now*,
don't wait for the gate to fail.

## 4. Battle plan → presentation on the 22nd

| Day | Gate |
|---|---|
| **Mon 12** | ✅ idea confirmed · **order placed (2× board)** · arduino-esp32 core installed · **Wokwi: blink + FreeRTOS tasks compiling tonight (no board needed)** |
| **Tue 13** | Firmware written hardware-agnostic (tasks + queue + `\n`-JSON) · WiFi→stdin bridge written · abstract + references drafted · record 2-min mock-demo backup video |
| **Wed 14 – Thu 15** | ⚠️ **Board gate:** flash → serial JSON → `dashboard.py --source serial` = real device data. **If ETA slips: local shop same-day; Wokwi run + video = accepted floor** |
| **Fri 16** | I²C sensors live (+ OLED if bought) · Tarun's figures + wiring diagrams |
| **Sat–Sun 17–18** | **WiFi layer:** ESP32 → TCP/HTTP → `python -u bridge.py` → dashboard `--source stdin`. Fallback = ESP32 softAP (`192.168.4.1`) + laptop connects to it; allow Python in Windows firewall |
| **Mon 19** | audio = cut-or-go at noon (buzzer if mic fights back) |
| **Tue 20** | **SERIAL-ONLY DEMO FROZEN — this is the pass threshold** · photos, screenshots, results section |
| **Wed 21** | report complete · full read-through · rehearse the serial fallback if WiFi dies in the hall |
| **Thu 22** | **PRESENT** |

**Serial path technical rules (bake in from the first line of code):** `\n`-terminated
JSON only · baud rate matched to `dashboard.py` · dashboard auto-reconnect · bridge
run with `python -u` (unbuffered) so lines reach stdin live.

## 5. Pre-decided fallbacks (no panic on the day)

- Parts late ⇒ ESP streams **on-chip temp / uptime / RSSI** — still live device
  telemetry, pipeline is sensor-agnostic (mock only as last resort).
- **Boards arrive 20–22 Oct (worst case) ⇒ the floor is: Wokwi-simulated firmware
  run + recorded video + serial demo of the laptop side + report.** Firmware is
  written and compiling from the 13th — hardware only upgrades "simulated" to
  "live", it never blocks writing code.
- WiFi fails in the hall ⇒ ESP32 softAP fallback, else serial-only demo still shows
  RTOS + embedded + IoT-to-host.
- **Band bridge fails (expected) ⇒ HR/SpO2 presented as a bridged stretch goal** +
  a browser-Bluetooth UI mock (Web Bluetooth HR service demo, labelled "UI
  prototype") — honest, and the mirror still talks posture + environment.
- Webcam refused in the hall ⇒ MPU-only posture + typed queries still demo the loop.
- Voice pipeline fails ⇒ buzzer alert + typed assistant (dashboard stays up).
- One board dies ⇒ 2nd board from today's order; else local same-day purchase.

## 6. Zero-hardware demo (works offline — verify tonight, record a 2-min backup video)

```
laptop\.venv\Scripts\python.exe laptop\receiver\dashboard.py --source mock --duration 15
laptop\.venv\Scripts\python.exe laptop\ai\assistant.py --query "How am I doing?" --once --no-tts
laptop\.venv\Scripts\python.exe laptop\ai\voice_pipeline.py --no-tts
```

Run on the exact presentation laptop, WiFi off. Backup video via Win+G.

## 7. Open questions (answer as they resolve)

**Resolved 12 Oct:** board = WROOM-32 DevKit ×2 · toolchain = Arduino IDE (no change) ·
display = laptop UI (= the mirror's face; OLED, mic/amp/speaker kit, MAX30102 all CUT)
· transport = serial core + WiFi layer · **narrative = "Mirror, Mirror" healthcare
mirror (§1)** · **razor BOM ≈₹1,950 (§3)** · nothing in hand, nothing ordered yet —
order goes out right after the review confirms the idea.

Still open:

1. **Will reviewers accept the laptop webcam + screen as the mirror's eyes and
   face**, or must all sensing/display live on the ESP32? (answer today if possible)
2. **5-minute hotspot test now:** laptop hotspot on + phone connected + ping/HTTP —
   proves ESP32↔laptop TCP will work at the venue.
3. **Nearest local electronics shop / same-day delivery option** (backup if Amazon
   ETA slips past the 16th).
4. Delivery ETA screenshot after ordering.
5. Huawei band nRF Connect scan result (Tarun,10-min timebox — instant win or park).
6. Presentation hall: laptop + display confirmed? WiFi or hotspot?
7. Is 23rd a submission date in addition to the 22nd presentation?

## 8. Reuse inventory (what already exists)

- **laptop/ — 100% reusable:** telemetry schema validator, mock streamer, ANSI dashboard
  + CSV logger, bleak BLE-NUS client, assistant (Ollama → API → offline template fallback,
  grounding check), faster-whisper voice pipeline (offline model cached).
- **firmware/ — ideas reusable, code not shipping:** 5-thread Zephyr design, sensor
  synthetic-fallback pattern, telemetry packet format (`sensor_packet.h`), pin freeze.
- **docs/ — ~80% of the report:** README 1364 lines (objectives, architecture, BOM,
  timeline, success criteria) + FINAL_BREAKDOWN + system_overview. Missing: abstract,
  references, figures/photos, results section.
