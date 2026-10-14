# KINETIQ — Review One-Pager · 12 Oct 2026

*Bring this to the idea-confirmation review. Detail lives in `OVERHAUL_PLAN.md`.*

## Hook

Snow White's queen asked the mirror, and the mirror talked back. We're building the
healthcare version: **a mirror that sees you, talks back, corrects your posture, and
suggests health actions.**

Subtitle: *"Mirror, Mirror — an RTOS-based healthcare assistant."*

## Formal title

**KINETIQ — RTOS-Based IoT Healthcare Assistant with Sensor-Grounded Voice AI**

## One-liner

> A laptop-hosted voice assistant — "the mirror" — sees the user via webcam, hears and
> answers them, and corrects posture using a live motion feed driven by an ESP32 RTOS
> sensing node streaming telemetry over serial/WiFi.

## What it does

1. **Sees you** — webcam face/pose mesh (MediaPipe) reads posture and head position
2. **Talks back** — offline speech-to-text → LLM → speech, grounded in live sensor data
3. **Corrects posture** — MPU6050 node + camera cross-check → voice alert + buzzer
4. **Suggests** — rule-based health nudges (bad posture, sedentary, room temperature)
5. **Streams live** — RTOS node telemetry → host dashboard → CSV log

## Requirements coverage

| Requirement | Evidence |
|---|---|
| **RTOS** | FreeRTOS tasks + queues on ESP32 (sensor / comms / alert tasks) |
| **IoT** | Live telemetry off-device → host dashboard → CSV log |
| **Embedded** | I²C sensors, GPIO button, buzzer, serial/WiFi transport |
| **Innovation** | Voice AI **grounded** in live telemetry — grounding check shown in the demo |

## Hardware (razor BOM ≈ ₹1,950)

2× ESP32 WROOM-32 devkit · MPU6050 + HC-SR04 combo · DHT22 · breadboard + jumper kit ·
F-F jumpers · tact buttons · caps · active buzzer

*Not bought — the laptop is the mirror's face and voice:* HR/SpO2 sensor, OLED,
speaker/mic kit. Heart-rate/SpO₂ = **stretch goal** via a wearable bridge (timeboxed probe).

## Roles

- **sid** — firmware + software (ESP32 tasks, telemetry, assistant integration)
- **Tarun** — wiring, figures, wearable-bridge probe, enclosure render

## Timeline → 22 Oct

12 confirm + order · 13 firmware in simulator · 14–15 board arrives, serial → dashboard ·
16 sensors live · 17–18 WiFi layer · 19 audio cut/go · 20 freeze + photos ·
21 report · **22 PRESENT**

## Fallbacks

Sensors late → on-chip telemetry · boards late → simulator + recorded video + report ·
WiFi fails → serial demo · wearable bridge fails → posture + environment only.

## 60-second demo script

1. Walk up to the mirror → camera sees posture, mirror greets you by state
2. Slouch → voice "sit up" + buzzer fires on the node
3. Ask *"how am I doing?"* → grounded voice answer + `[grounding] PASS`
4. Dashboard behind shows the live RTOS node stream (the RTOS/IoT proof)
