# KINETIQ — System Overview

KINETIQ is a wrist-worn health-and-fitness assistant built from an ESP32-S3 running
Zephyr RTOS and a laptop Python pipeline. The watch senses and infers; the laptop
listens, reasons and speaks.

> **Medical safety boundary:** every number KINETIQ produces is a *wellness
> estimate*. It is not a medical device, not a diagnostic tool, and must not be used
> for diagnosis, treatment, or alerting on clinical conditions. SpO2 is a still-only
> estimate. See [Medical Safety Boundary](#medical-safety-boundary).

---

## Overview

- **Sense** — BMI270 6-axis IMU at 100 Hz, MAX30102 red/IR PPG at 50 Hz, and a
  push-to-talk (PTT) MEMS microphone on I2S.
- **Process** — four Zephyr threads and a `k_msgq` sample bus; sensor capture is
  scheduled by priority, and samples are filtered/aggregated on-device.
- **Infer** — rule-based activity classification (`REST` / `WALK` / `RUN`),
  exertion grading (`REST` / `LIGHT` / `MODERATE` / `HIGH`), and a PPG quality
  gate that downgrades confidence instead of inventing it.
- **Communicate** — one telemetry JSON object per second over BLE / UART log, plus
  discrete PTT audio chunks streamed to the laptop.
- **Assist** — laptop does STT (faster-whisper) -> LLM (with the latest telemetry as
  context) -> TTS (piper) and streams the spoken answer back to the watch.

### Execution flow

```
                     +---------------------------------------------+
   SENSE             |            ESP32-S3 / Zephyr (watch)       |
                     +---------------------------------------------+
  BMI270 100 Hz -->  | imu_thread  (prio 5)  ---\
  MAX30102 50 Hz --> | ppg_thread (prio 6)  ---+--> k_msgq (depth 10)
  PTT mic ------------|                          |        |
                     +--------------------------|--------v-------+
                                                  | infer_thread (prio 7)
                                                  | activity / exertion
                                                  | PPG quality gate
                                                  +--------+-----------+
                                                           | shared state
                                                           | (k_mutex)
                                                           v
                     +---------------------------------------------+
   COMMUNICATE        | comm_thread (prio 9, 1 Hz) -> telemetry JSON |
                     +---------------------------------------------+
                                       |  BLE / UART log
                                       v
                     +---------------------------------------------+
   ASSIST            |        Laptop Python pipeline               |
                     |  mock_streamer / serial+bleak receiver       |
                     |        -> faster-whisper STT                 |
                     |        -> LLM (+ telemetry context)         |
                     |        -> piper TTS -> watch speaker         |
                     +---------------------------------------------+
```

---

## Execution Flow

### 1. Sense

- **IMU (BMI270, I2C0 @ 0x68, 400 kHz)** — sampled at 100 Hz by `imu_thread`.
  Supplies a 3-axis accelerometer and 3-axis gyroscope; gravity magnitude is the
  ~9.81 m/s² reference the activity classifier keys off.
- **PPG (MAX30102, I2C0 @ 0x57, 400 kHz)** — sampled at 50 Hz by `ppg_thread`.
  Red and IR photodiode counts are converted to a heart-rate estimate; SpO2 is
  reported as a still-only estimate (fixed at 97 % while at rest).
- **PTT microphone (I2S)** — single-button push-to-talk. Audio is only captured
  while the button is held, chunked, and streamed to the laptop, which keeps the
  always-on audio path (and its power and privacy cost) off the watch.
- **Display (GC9A01, SPI2 @ 40 MHz)** — renders steps, heart rate, activity and
  exertion; backlight is PWM-dimmed on GPIO47.

### 2. Process

- **Zephyr threads** — four statically allocated threads, each with a distinct
  priority so capture never waits on inference or logging.
- **`k_msgq` sample bus** — `sensor_msgq` (10 slots, 4-byte aligned) carries
  `sensor_packet_t` records from both capture threads to the inference thread.
- **Filtering on-device** — the IMU vertical axis is low-passed for the cadence
  detector, and motion energy is smoothed with an exponential moving average so
  the activity label does not flicker sample to sample.
- **Back-pressure is explicit** — a full queue means a dropped sample plus a
  counted warning; the watch never blocks capture on inference.

### 3. Infer

- **Activity** — blended motion energy `0.7 * |‖accel‖ - 9.81| + 0.3 * ‖gyro‖`,
  EMA-smoothed, then thresholded: `< 0.6` -> `REST`, `< 1.8` -> `WALK`, otherwise
  `RUN`.
- **Exertion** — derived from heart rate and activity together: `REST` while resting
  under 80 bpm, `LIGHT` under 100 bpm, `MODERATE` under 130 bpm, `HIGH` above.
- **PPG quality gate** — signal floor check on red and IR counts. When the gate
  fails, `ppg_quality` is reported as `poor` and the wearer is not told a confident
  number; the pipeline surfaces the degraded confidence instead of hiding it.
- **Step count** — zero-crossing cadence detector on the filtered vertical axis with
  a 250 ms refractory window, so steps only advance during real gait.

### 4. Communicate

- **Telemetry** — `comm_thread` emits exactly one JSON object per second. The
  schema is locked; see [Telemetry Schema](#telemetry-schema).
- **Transport** — BLE notification on the production path; `LOG_INF` over UART/USB
  serial for bench bring-up and logging.
- **Audio** — PTT audio chunks are framed and streamed while the button is held.

### 5. Assist

- **STT** — faster-whisper transcribes the PTT audio chunk locally.
- **LLM** — the transcript plus the most recent validated telemetry record is the
  prompt context, so answers can reference "you have been walking for 4 minutes at
  104 bpm" instead of generic advice.
- **TTS** — piper renders the reply and streams it back to the watch speaker.
- **Validation gate** — every incoming packet passes `TelemetryRecord.parse`; a
  malformed or schema-violating packet is dropped with a log line rather than being
  silently forwarded into the prompt.

---

## Thread / Queue Architecture

| Component        | Role                                    | Rate / Trigger | Priority | Stack  |
| ---------------- | --------------------------------------- | -------------- | -------- | ------ |
| `imu_thread`     | BMI270 capture, synthetic motion stub   | 100 Hz (`k_msleep(10)`)  | 5 (highest) | 2048 |
| `ppg_thread`     | MAX30102 capture, heart-rate estimate   | 50 Hz (`k_msleep(20)`)   | 6        | 2048 |
| `infer_thread`   | `k_msgq` consumer: activity, exertion, PPG quality | per received sample (`k_msgq_get`, `K_FOREVER`) | 7 | 2048 |
| `comm_thread`    | Snapshots shared state, emits telemetry JSON | 1 Hz (`k_msleep(1000)`) | 9 (lowest) | 2048 |

Shared objects:

| Object         | Type                                  | Purpose |
| -------------- | ------------------------------------- | ------- |
| `sensor_msgq`  | `K_MSGQ_DEFINE`, 10 slots, align 4    | Sensor sample bus, capture -> inference |
| `state_mutex`  | `K_MUTEX_DEFINE`                      | Guards `current_state` between `infer_thread` and `comm_thread` |
| `current_state`| `struct shared_state`                 | Latest activity, exertion, HR, SpO2, steps, PPG quality, motion intensity |
| `g_steps`      | `atomic_t`                            | Step total, incremented by `imu_thread`, snapshotted elsewhere |

Priority rationale: capture (5, 6) outranks inference (7), which outranks
communication (9). A slow log write or a full queue therefore costs a dropped
sample, never a missed window.

---

## Telemetry Schema

Locked, one object per second, one line of compact JSON:

```json
{"timestamp":1750000000,"heart_rate":72,"spo2":97,"activity":"rest","motion_intensity":0.08,"exertion":"rest","steps":4218,"ppg_quality":"good"}
```

| Key                | Type    | Range / Values                              | Notes |
| ------------------ | ------- | ------------------------------------------- | ----- |
| `timestamp`        | int     | unix seconds, > 0                           | uptime + fixed epoch base |
| `heart_rate`       | int     | 30-220 bpm                                  | PPG-derived estimate |
| `spo2`             | int     | 70-100                                      | still-only estimate |
| `activity`         | string  | `rest`, `walk`, `run`, `active`, `unknown`  | normalised lowercase |
| `motion_intensity` | float   | 0.00 - 1.00                                 | normalised motion energy |
| `exertion`         | string  | `rest`, `light`, `moderate`, `high`         | normalised lowercase |
| `steps`            | int     | >= 0                                        | cadence-detected |
| `ppg_quality`      | string  | `good`, `poor`                              | PPG quality gate result |

Rules enforced on the receiving side (`laptop/receiver/telemetry_schema.py`):
all eight keys required, unknown keys rejected, ints/floats coerced, enums
validated case-insensitively, `motion_intensity` clamped-checked to `[0.0, 1.0]`,
and any violation raises `ValueError` with an explicit message.

---

## Split-Processing Rationale

**On the watch (ESP32-S3 / Zephyr):** everything time-critical and cheap —
sensor acquisition, filtering, cadence, rule-based activity/exertion, and the
telemetry packet. This keeps the hot loop deterministic under a real-time kernel,
survives a dropped WiFi/BLE link, and costs milliwatts. The watch alone is
already useful for fitness logging without any network.

**On the laptop:** everything model-heavy and non-deterministic — speech
recognition, LLM inference, and speech synthesis. `faster-whisper`, an LLM client,
and piper TTS do not fit in the S3's memory budget and would blow the battery
envelope even if they did. The laptop also keeps the wake-word-free audio path
short-lived.

**The contract between them** is the locked telemetry JSON plus PTT audio chunks.
That boundary is deliberately narrow and versioned, so either half can be
replaced (a different MCU, a different LLM) without rewriting the other.

---

## Medical Safety Boundary

- KINETIQ outputs **wellness estimates only**: heart rate, a still-only SpO2
  estimate, activity class, exertion class, steps and a PPG confidence flag.
- It is **not a medical device**, holds **no medical certification**, and makes
  **no diagnosis**. Nothing it reports is a clinical measurement.
- **No alerting.** KINETIQ does not raise, and must not be used to raise, emergency
  or clinical alarms. An abnormal-looking reading is a prompt to re-measure or to
  consult a clinician, not evidence of a condition.
- **SpO2 is still-only.** Movement degrades the estimate, so SpO2 is only reported
  as meaningful while the wearer is at rest; otherwise the PPG quality gate downgrades
  it to `poor`.
- **The LLM is not a clinician.** Assistant replies are general wellness guidance
  only, and the prompt path must keep the telemetry estimates labelled as
  non-diagnostic.
