# KINETIQ — Final Project Scope

## 1. Definition

**KINETIQ is a Zephyr-RTOS wearable on ESP32-S3 that fuses IMU+PPG into live activity/exertion state on-watch, and grounds a laptop-hosted PTT voice assistant (STT->LLM->TTS over BLE) in that state — custom 2-layer SMD PCB + 3D-printed enclosure.**

Source of truth: `README.md` (1364 lines) wins on all conflicts. `abstract.pdf` is **superseded — DISCARD**.

| abstract.pdf (outdated) | Locked scope (this doc) |
|---|---|
| CoachOS / C3 | No custom OS; firmware platform on Zephyr RTOS; ESP32-S3 only |
| FreeRTOS | Zephyr RTOS (`k_thread`, `k_msgq`, devicetree) |
| Touchscreen | CUT — 1 physical button only (short=cycle, long=talk) |
| WiFi primary | BLE primary; WiFi dev-only fallback |
| Node.js laptop | Python laptop (BLE RX + dashboard + STT->LLM->TTS) |

## 2. What each old file contributed

**README.md (source of truth):** Full system contract — 15 objectives, Sense->Process->Infer->Communicate->Assist loop, split-processing architecture (watch=sensing/inference, laptop=STT/LLM/TTS), Zephyr thread/msgq/devicetree rules, rule-based activity REST/WALK/RUN/ACTIVE/UNKNOWN + exertion REST/LIGHT/MODERATE/HIGH as wellness metric, PPG quality gate, telemetry JSON schema, 4-screen UI, 2-layer SMD PCB + CAD enclosure, 5-week plan, 14 success criteria, medical-safety boundary.

**InitialIdeation.md (KINETIQ concept):** Product identity and differentiator — "understands physical context, communicates through that context" killer line; exertion trend + "Explain My State" signature features; sensor-grounded "How am I doing?" demo script; RTOS concurrency justification for viva; firmware layering (app -> fitness/AI -> middleware -> Zephyr -> HAL); circular-PCB-with-rectangular-compromise guidance.

**BRAINSTORM.md (raw notes):** Constraint seeds — round display + ESP32-class MCU, IMU + HR/SpO2, mic+speaker with laptop-side processing, SMD, Zephyr over FreeRTOS, no Arduino, 2-layer max-3 PCB, wrist-watch form factor in 5 weeks, open questions on TTS/STT/AI/touch/OS that this doc locks.

## 3. Locked stack

| Subsystem | Locked choice |
|---|---|
| MCU | ESP32-S3, **S3-WROOM-1 MODULE** (no bare-chip RF risk) |
| RTOS | **Zephyr 4.2+** (production-ready S3 since 4.0; S3-BOX-3 merged 2026-06) |
| IMU | BMI270 (`bosch,bmi270`, in-tree + 100 Hz sample) or ICM-42688 (`invn,icm4268x`, refactor PR) |
| PPG | MAX30102 via in-tree `maxim,max30102` compat (max30101 driver) |
| Display | GC9A01 1.28" 240x240 round TFT, SPI direct-draw framebuffer, **no LVGL** |
| Mic | INMP441-class I2S MEMS (verify binding W1, bitbang/custom fallback) |
| Speaker | MAX98357A-class I2S amp + small speaker (verify binding W1, fallback) |
| Controls | 1 physical button: short=cycle screen, long=PTT talk |
| Comms | BLE GATT NUS-style JSON telemetry + PTT audio chunks; WiFi dev-only; USB-serial invisible fallback |
| Laptop | Python 3.10+: BLE RX + live dashboard + faster-whisper (tiny/base.en) STT -> local/API small LLM (e.g. gemma3:1b, fixed sensor-JSON schema) -> Piper TTS -> speaker; CSV history secondary |
| PCB | 2-layer SMD target (max 3L); TOP signal+parts, BOTTOM minimal, solid GND under chip/RF/crystal, 50-ohm RF, CLC match, 15 mm antenna keepout, module at edge, USB far from antenna |
| Enclosure | 3D-printed PLA, rectangular+round-outline compromise; openings for PPG contact, mic, speaker, button, charge; strap mount |
| Power | LiPo + charger + regulation; size for 4-6 hr demo; measure, don't assume |

## 4. System loop + telemetry + UI

Loop: **Sense -> Process -> Infer -> Communicate -> Assist.**

Sense (IMU 100 Hz + PPG 25-100 Hz + PTT mic) -> Process (filter + Zephyr threads/msgq) -> Infer (activity + HR filter + quality + exertion on-watch) -> Communicate (BLE JSON + audio chunks) -> Assist (laptop STT->LLM+JSON->TTS->speaker/display).

Telemetry JSON:

```json
{"timestamp":1727544000,"heart_rate":72,"spo2":97,"activity":"walking","motion_intensity":0.42,"exertion":"moderate","steps":4218,"ppg_quality":"good"}
```

PTT audio: 3-5 s clips, 16 kHz mono (~64 kbps ADPCM-ish), 8 kHz fallback.

4 static framebuffer screens (GC9A01, no LVGL):

1. HOME: `72 BPM / 4,218 STEPS / 18:42`
2. ACTIVITY: `RUNNING / 147 BPM / HIGH`
3. BODY: `HR 72 / SpO2 97 / QUALITY GOOD`
4. ASSISTANT: `READY — hold button to talk`

## 5. CORE / SECONDARY / CUT

| Bucket | Items |
|---|---|
| CORE (ship in 5 wks, non-negotiable) | Rule-based activity REST/WALK/RUN/ACTIVE/UNKNOWN; exertion REST/LIGHT/MODERATE/HIGH (wellness metric, not medical); HR via MAX30102 + quality gate GOOD/POOR; 1-button PTT (short=cycle, long=talk); 4 static framebuffer screens on GC9A01 (no LVGL); BLE JSON telemetry + PTT audio chunks; **laptop PTT voice + sensor-grounded LLM is CORE MUST** — Python BLE RX + dashboard + faster-whisper STT -> LLM + sensor JSON -> Piper TTS -> speaker; 2-layer SMD with S3 MODULE; PLA enclosure with PPG/mic/speaker/button/charge openings |
| SECONDARY (only after core demoable) | SpO2 still-only + GOOD else `--`; WiFi dev-only fallback; laptop CSV history; **GPS (never critical path — only if core demoable + time remains + power/antenna budget allows)** |
| STRETCH / LAST-RESORT | **Touchscreen (last-resort — only if button UI done + driver proven + zero regression risk; default stays 1-button)**; **4-layer PCB (last-resort fallback — owner targets 2L only; 4L only if 2L routing proves impossible + lead sign-off; max 3L per README)**; **phone app (stretch/undecided — "we will see"; laptop dashboard stays primary peer; phone only as post-core experiment, doubles test matrix)** |
| CUT for 5 weeks | On-device wake-word, on-device ML (rule-based ships; laptop sklearn optional), continuous listening, LVGL, coating/gasket, C3/FreeRTOS/Node.js legacy |

## 6. Feasibility notes

- **S3 Zephyr is real:** CPU/IRQ/UART/I2C/SPI/I2S/BT supported on S3; SMP non-functional + BT limits; I2S echo sample exists — pin Zephyr 4.2+, budget W1 for I2S-duplex+BLE+GC9A01+devicetree bring-up, each driver standalone first.
- **Drivers mostly in-tree:** BMI270 (`bosch,bmi270`) and ICM-42688 (`icm4268x`) yes; MAX30102 yes via max30101 compat — GC9A01/INMP441/MAX98357A bindings must be verified W1 with bitbang/custom fallback; display via SPI direct-draw, never LVGL.
- **BLE voice = PTT chunks only:** S3 is BLE-only (no Classic); no LE Audio CIS/BIS in Zephyr-S3 path for 5 wks — 16 kHz mono ~64 kbps 3-5 s NUS chunks (8 kHz fallback); continuous streaming will choke; USB-serial audio as invisible demo fallback; pre-cache one TTS reply.
- **Laptop pipeline is sub-second-capable:** faster-whisper tiny/base.en on CPU + Piper voices (20-50 MB, 10-20x realtime) + Ollama small local LLM (gemma3:1b) or API LLM with fixed JSON schema; short prompts + whisper tiny/small keep PTT round-trip <5 s median; Python 3.10+.
- **PCB + power are the schedule risks:** Espressif allows 2L (solid GND, 50-ohm RF, keepout 15 mm, module-at-edge) but compact round-on-2L is tight — accept rectangular+round compromise (DevKitC 69x25 mm precedent), S3-WROOM-1 module mandatory, 4L premium (~$5-10) not worth fab-time risk; S3+display+sensors+BLE continuous needs a 4-6 hr demo-sized battery — measure draw, powerbank acceptable for devkit fallback.

## 7. 5-week MVP

| Week | Goal | Exit criteria |
|---|---|---|
| W1 Sense | Zephyr boot + display + IMU+PPG raw + logs | Zephyr 4.2+ boots on S3; GC9A01 shows static frame; IMU streams 100 Hz; PPG streams raw; logs show rates |
| W2 Process | Threads+msgq+button+BLE JSON+dashboard row | IMU/PPG/UI/BLE threads concurrent via msgq; button short cycles screens; BLE JSON reaches laptop dashboard row |
| W3 Infer | Activity+steps+HR filter+quality+exertion | Scripted REST->WALK->RUN transitions visible on display + dashboard; steps ±10% on flat 50-step; HR filtered + GOOD/POOR gate; exertion state live |
| W4 Assist | PTT->STT->LLM+JSON->TTS->speaker | Long-press records 3-5 s; "How am I doing?" returns sensor-grounded reply <5 s median; says `--`/unavailable when POOR |
| W5 Integrate | PCB parallel from W3, print v1 W4, full assembly | Custom PCB populates+boots OR devkit-in-shell (both count); enclosure holds all parts; end-to-end demo recorded |

Parallel lanes: **firmware** (Zephyr boot->threads->BLE->PTT, W1-W4) / **sensors+UI** (drivers->filters->activity/exertion->4 screens, W1-W3) / **PCB+CAD+laptop** (schematic W1-W2 freeze end W2, order W3; enclosure print v1 W4; laptop BLE RX W2, STT->LLM->TTS W4).

## 8. Risks + mitigations

1. **PCB fab slip** -> freeze schematic end W2, order W3; **devkit+breakouts-in-printed-shell counts as integrated prototype.**
2. **Zephyr S3 bring-up (I2S duplex+BLE+GC9A01+devicetree)** -> W1 each driver standalone before integration.
3. **PPG motion artifact** -> quality gate GOOD/POOR; demo resting/walking HR only; SpO2 still-only.
4. **BLE audio drop** -> PTT 3-5 s 16 kHz mono NUS chunks (8 kHz fallback); USB-serial fallback; pre-cached TTS reply.
5. **STT-LLM-TTS >5 s** -> whisper tiny/small, short fixed-schema prompt, wired backup, local Piper.
6. **Fit/power** -> size battery+speaker W1; PPG must touch wrist; 4-6 hr demo target, powerbank acceptable.
7. **Scope creep** -> Core gate: no secondary until core demoable; CUT list is final; GPS/touch/4L/phone re-tiers need core-demoable + lead sign-off, never critical path.

## 9. Honest metrics vs never-claim

Honest metrics: HR ±5 BPM **rest+still+GOOD only**; steps ±10% flat 50-step; activity correct on scripted REST->WALK->RUN demo; PTT round-trip <5 s median; report BLE drop rate + IMU rate + CPU idle %.

NEVER claim: medical SpO2/HR/ECG, diagnosis, calories, sleep, waterproof, custom OS, on-device LLM, GPS. SpO2 label everywhere: **estimate, still-only, non-medical** (show `--` unless still+GOOD).

Softened success criteria: #3 HR usable at rest with quality flag (not continuous clinical); #5/#6 rule-based, correct on scripted demo; #8 PTT-gated voice only (no continuous listening/wake-word); #13 custom PCB populates+boots, devkit-in-shell fallback accepted as integrated prototype; #14 print holds all parts with sensor/audio/button/charge access, no IP rating claimed.

## 10. Immediate next actions (W1 checklist)

- [ ] Order parts: S3 devkit + S3-WROOM-1 module, BMI270/ICM-42688 breakout, MAX30102 breakout, GC9A01 1.28" round, INMP441, MAX98357A + micro speaker, LiPo + charger, 1 button
- [ ] Zephyr 4.2+ boot on S3 (blinky + logging + timers)
- [ ] GC9A01 SPI direct-draw static frame (no LVGL)
- [ ] IMU streaming at 100 Hz to logs
- [ ] PPG raw streaming + quality-gate stub
- [ ] Size battery + speaker against measured draw; confirm PPG wrist-contact plan
- [ ] Freeze pin map + devicetree; verify GC9A01/INMP441/MAX98357A bindings or fallback path

## 11. W1 Build Detail

### 11.1 FROZEN BOM v1.0 (2026-09-28 — changes need lead sign-off)

| Qty | Locked part (MPN) | Interface | Footprint | Proto vs PCB note |
|---|---|---|---|---|
| 1 | ESP32-S3-DevKitC-1 | Native USB + BLE | Devkit | Proto/fallback bring-up only, not final PCB |
| 3 | ESP32-S3-WROOM-1-N16R8 (1+2 spare) | WiFi+BLE, native USB (GPIO19/20) | SMD module | Custom-PCB target, module at board edge, 15 mm antenna keepout |
| 1+spare | BMI270 IMU (Bosch, LGA 2.5x3 mm) — Zephyr `bosch,bmi270` | I2C0 400 kHz | LGA-12 | Proto: BMI270 breakout; ICM/QMI variants REJECTED for this build |
| 1+spare | MAX30102 PPG (Maxim, red+IR) — Zephyr `maxim,max30102` | I2C0 400 kHz | Module-fit | Proto: MAX30102 breakout at 3V3; still-only SpO2 |
| 1+spare | 1.28" 240x240 round GC9A01 NON-TOUCH | 4-wire SPI (SCK/MOSI/CS/DC/RST/BL) | Panel + FPC/pin connector | Proto: pin-header module; touch variant REJECTED |
| 1+spare | INMP441 I2S MEMS mic (bottom-port) | I2S0-RX 16 kHz mono | Breakout-fit | Proto: INMP441 breakout |
| 1 set | MAX98357A I2S amp (no MCLK) + 8Ω 0.5 W 20 mm micro speaker (15-20 mm OK) | I2S-TX shared BCK/WS | Breakout + speaker | Proto: MAX98357A breakout + speaker |
| 2 | 6x6 mm tactile SMD button | GPIO IRQ + pull-up | SMD 6x6 | 1x UI PTT (short=cycle, long=talk) + 1x RESET/BOOT helper |
| 1 set | 3.7 V LiPo 600-800 mAh (503040 class) + MCP73831/TP4056 charger + 3V3 LDO 500 mA+ (XC6206/ME6211 class) + USB-C 16P (charge+flash via S3 native USB) + ESD + 10 uF+0.1 uF per rail + I2C 4k7 pull-ups | Power + USB D+/D- | SMD + JST | Custom-PCB power target; bench-supply OK W1 |
| 1 lot | Passives 0201/0402 (one size only) + headers + strap + PLA | — | SMD | PCB guy picks one size and sticks to it; auto-reset not needed (native USB) |

2-layer only; 4L last-resort with lead sign-off. GPS / phone / touch NOT in BOM.

### 11.2 Suggested S3 pin map (PROPOSAL — freeze W1)

- I2C0 (SDA/SCL + 4k7 pull-ups): IMU + MAX30102 shared bus, 400 kHz.
- SPI2: GC9A01 SCK/MOSI + GPIO DC/CS/RST/BL; CS high-idle, BL via PWM-capable pin.
- I2S: INMP441 RX (SD/WS/BCK) on I2S0-RX; MAX98357A TX sharing BCK/WS where driver allows else I2S1-TX; 16 kHz mono target.
- Button: 1 GPIO, internal pull-up + IRQ on falling edge, debounce in software; short=cycle, long (>800 ms)=PTT.
- UART0 over native USB-serial for logs/debug (USB D+/D- = GPIO19/20 reserved).
- Constraints: keep strapping pins (GPIO0/3/45/46) boot-safe, no pull conflicts; keep USB D+/D- clear; module antenna edge with 15 mm keepout; charger/LDO far from antenna.

### 11.3 Laptop pipeline detail

BLE NUS RX -> JSON validator (drop + count bad frames) -> live dashboard (activity/HR/motion/exertion/session/RSSI) + CSV logger -> PTT wav (3-5 s, 16 kHz mono) -> faster-whisper tiny/base.en -> prompt builder (fixed sensor-JSON schema + short system prompt: state + question, no history) -> LLM (local Ollama gemma3:1b OR API) -> Piper TTS wav -> BLE TX chunks to watch + laptop-speaker fallback.

Round-trip budget (<5 s median): BLE up ~0.5 s / STT ~0.8 s / LLM ~1.5 s / TTS ~0.7 s / BLE down + playback ~0.8 s; remainder = slack. Pre-cache one TTS reply ("Steady — keep going") to mask BLE stalls.

### 11.4 What every component is for

- **ESP32-S3-WROOM-1-N16R8 (x3):** The whole watch brain — runs Zephyr threads (Sense+Process+Infer), drives display/audio, sends BLE. Module (not bare chip) because its pre-certified antenna keeps 2-layer routing legal. Omit it = no product.
- **ESP32-S3-DevKitC-1:** Sacrificial bring-up board for W1-W4 firmware before the custom PCB exists; also the accepted devkit-in-shell fallback if fab slips. Chosen because it matches the WROOM-1 pinout. Omit it = custom PCB becomes critical path, one fab slip kills demo.
- **BMI270 IMU:** Sense leg — accelerometer+gyro at 100 Hz feeding step count, motion intensity, and REST/WALK/RUN/ACTIVE classifier. Locked because Zephyr has in-tree `bosch,bmi270` + sample; no driver porting in 5 wks. Omit it = no activity, no steps, no exertion.
- **MAX30102 PPG:** Sense leg — red+IR optical signal feeding HR filter + GOOD/POOR quality gate (SpO2 still-only). Locked because Zephyr has `maxim,max30102` compat; run at 3V3. Omit it = no HR, exertion loses its key input, BODY screen empty.
- **GC9A01 1.28" round non-touch:** Communicate-to-user leg — 4 static framebuffer screens (HOME/ACTIVITY/BODY/ASSISTANT) over 4-wire SPI, no LVGL. Non-touch locked because button UI is already risky enough; touch driver = regression risk. Omit it = no on-watch feedback, demo is a blind box.
- **INMP441 I2S mic:** Assist uplink — captures 3-5 s PTT clips at 16 kHz mono for BLE->laptop STT. Locked because I2S digital out needs no codec and Zephyr I2S exists. Omit it = no voice questions, AI core dead.
- **MAX98357A + 8Ω micro speaker:** Assist downlink — I2S amp (no MCLK needed, simplest wiring) driving spoken LLM replies through the 20 mm speaker. Omit it = answers only on laptop, watch never talks back.
- **2x 6x6 mm tactile buttons:** Sole UI — button 1: short=cycle screen, long=PTT talk; button 2: reset/boot helper for flashing and recovery. Two locked because one misclick-bricked board without a recovery key wastes a day. Omit them = no screen control, no talk trigger.
- **LiPo 600-800 mAh + charger + 3V3 LDO + USB-C:** Power leg — battery sized for 4-6 hr demo of S3+display+sensors+BLE; MCP73831/TP4056 charges over USB-C, LDO gives clean 3V3, native USB on GPIO19/20 doubles as flash+logs. Omit any piece = dead board, unchargeable board, brown-outs, or no flashing.
- **Passives/headers/strap/PLA:** Glue — decoupling caps per rail, I2C pull-ups, ESD on USB, headers for proto, strap + printed shell that holds PCB/battery/display with PPG-against-wrist contact. Omit them = noisy resets, bus faults, and parts rattling on a bench instead of a wearable.

### 11.5 Start-tomorrow checklist (no hardware needed)

Laptop owner:
- [ ] BLE NUS stub + dashboard row + CSV logger on fake JSON — exit: fake packet renders + appends.
- [ ] faster-whisper tiny + gemma3:1b/API + Piper on fake sensor JSON — exit: "How am I doing?" answers grounded.
- [ ] Round-trip <5 s test on laptop loopback — exit: median logged.

Firmware owner:
- [ ] Zephyr 4.2+ blinky + logging + timers on S3 — exit: boots, logs tick.
- [ ] Threads + k_msgq skeleton (imu/ppg/ui/ble) — exit: queues pass, no deadlock.
- [ ] Button FSM (short=cycle, long>800 ms=PTT) — exit: state transitions logged.
- [ ] JSON builder matching telemetry schema — exit: validator passes.
- [ ] Devicetree + pin freeze per §11.2 — exit: bindings checked, strapping/USB clear.

Algo owner:
- [ ] Phone-IMU recordings (rest/walk/run, 60 s each) — exit: 3 labeled captures.
- [ ] REST/WALK/RUN thresholds in Python — exit: scripted captures classify correctly.
- [ ] HR filter + GOOD/POOR gate in Python — exit: motion segments rejected.

CAD owner:
- [ ] Datasheet models: WROOM-1 + GC9A01 + 503040 + speaker — exit: all 4 in assembly.
- [ ] Print v0 shell (fit check, no finish) — exit: devkit + display + battery fit.

PCB-support owner:
- [ ] Footprint/keepout/decoupling/pull-up/USB/strapping review — exit: checklist signed.
- [ ] ERC/DRC clean on schematic v0 — exit: zero errors.

### 11.6 Order quantities (no-reorder buffer, 5-week, no iterations)

| Qty to order | Part | Why this count |
|---|---|---|
| 2 | ESP32-S3-DevKitC-1 | 1 active + 1 spare/fallback shell |
| 5 | S3-WROOM-1-N16R8 | 1 build + 1 rework + 3 spare/attrition |
| 3 + 5 bare | BMI270 breakouts + bare sensors | 3 proto + 5 for PCB/rework/spares |
| 3 + 5 bare | MAX30102 breakouts + bare | 3 proto + 5 for PCB/rework/spares |
| 3 | GC9A01 1.28" non-touch modules | 1 active + 1 rework + 1 spare (fragile) |
| 4 | INMP441 mic | 1 + 1 rework + 2 spare (ESD-prone) |
| 4 + 4 | MAX98357A + 20 mm 8Ω speakers | Matched 4+4 sets, same attrition logic |
| 20 | 6x6 mm tactile buttons | Cheap, lose them; 2 per board + spares |
| 3 + 3 + 5 | LiPo 600-800 mAh + charger IC + LDO | 3 batteries/chargers + 5 LDOs (cheap insurance) |
| 10 | USB-C 16P | 2 per board + rework/spares |
| 100+ per value | Passives one-size (0.1 uF / 10 uF / 4k7 / 10k) | Tombstoning + loss buffer |
| 1 lot | Headers / JST / strap / PLA | Single kit, no reorder |
| 10 boards min | PCB fab | 2 good + rework + spares |

Order spares now — no time or money for a second run.
