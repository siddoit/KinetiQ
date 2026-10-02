# RTOS-Based Wearable Fitness & Wellness Assistant

## Project Overview

This project is a custom wearable fitness and wellness device built around an **ESP32-S3** and **Zephyr RTOS**.

The device is intended to be a compact wrist-worn system with a round display, inertial sensing, physiological sensing, audio input/output, and wireless communication with a laptop.

The core concept is:

**Sense → Process → Infer → Communicate → Assist**

The wearable collects real-time sensor data such as motion and heart rate, processes the data on the ESP32-S3, determines the user's current physical/activity state, and communicates that state to a laptop-hosted AI assistant.

The AI assistant can use the current sensor context when responding to voice queries, allowing the system to provide context-aware fitness and wellness feedback rather than functioning as a generic chatbot.

The project is primarily an **embedded systems + RTOS + sensor processing + AI integration + custom hardware** project.

---

# Primary Objectives

The system should:

1. Run on an **ESP32-S3**.
2. Use **Zephyr RTOS** as the primary firmware platform.
3. Avoid Arduino framework and Arduino-style application architecture.
4. Use **ESP-IDF-compatible hardware capabilities where appropriate**, while keeping Zephyr as the primary RTOS/application environment.
5. Continuously acquire data from an IMU.
6. Acquire heart-rate data from a PPG sensor.
7. Support SpO₂ measurement where hardware and signal quality permit.
8. Provide a small round embedded display.
9. Support a microphone and speaker for voice interaction.
10. Communicate with a laptop using BLE and/or Wi-Fi.
11. Run the computationally heavy AI functionality on the laptop rather than the ESP32.
12. Provide an activity/exertion state derived from sensor data.
13. Present real-time status through the wearable UI.
14. Use a custom PCB rather than a breadboard-based final implementation.
15. Have a custom CAD-designed enclosure suitable for a wearable device.

---

# Core System Concept

The system should not be treated as a generic smartwatch clone.

The main differentiating feature is **sensor-context-aware assistance**.

Instead of simply exposing raw measurements such as:

* Heart rate: 147 BPM
* Steps: 4218
* Motion: high

the system should derive a useful physical state such as:

* Resting
* Walking
* Running
* Active
* High exertion

The laptop-hosted AI assistant can then use this state as context when processing voice requests.

For example:

User:

> "How am I doing?"

The system should provide the AI assistant with information such as:

```text
Heart Rate: 147 BPM
Activity: Running
Motion Intensity: High
Exertion: High
Session Duration: 08:21
```

The assistant can then generate a response grounded in the actual sensor state.

The AI must not be presented as a medical diagnostic system.

---

# Hardware

## Main MCU

Preferred:

**ESP32-S3**

Reasons:

* Suitable performance for a connected wearable
* BLE and Wi-Fi
* Good peripheral support
* I2S support for audio
* SPI/I2C support for sensors and displays
* Suitable RAM/CPU resources for RTOS-based multitasking
* Good ecosystem for custom embedded development

Do not replace the ESP32-S3 with a substantially weaker MCU unless a strong engineering reason exists.

---

# Sensors

## IMU

Preferred class:

* BMI270
* ICM-42688-P
* Similar low-power 6-axis IMU

The IMU is a primary sensor and should support:

* Motion detection
* Step estimation
* Activity classification
* Motion intensity estimation
* Orientation estimation where useful
* Exercise/activity feature extraction

The firmware should acquire IMU data using an appropriate sampling rate rather than polling unnecessarily slowly.

The exact sampling frequency should be configurable.

---

## Heart Rate / PPG

Preferred sensor class:

* MAX3010x-family sensor
* Equivalent optical PPG sensor

Primary uses:

* Heart rate estimation
* Pulse waveform acquisition
* Optional SpO₂ estimation

Important:

PPG measurements are highly sensitive to motion.

The system should therefore include a concept of **signal quality** or **measurement confidence**.

Do not blindly display every PPG-derived number as accurate.

A useful pipeline is:

```text
PPG Signal
    ↓
Filtering
    ↓
Signal Quality Assessment
    ↓
Good Measurement?
   / \
 No   Yes
 ↓      ↓
Reject  HR / SpO₂
```

SpO₂ should be considered an optional feature and should not become a critical dependency for the project.

---

# Audio

## Microphone

Preferred:

**I2S MEMS microphone**

Example class:

* INMP441-style device
* Equivalent digital MEMS microphone

The microphone is primarily intended for voice commands.

Heavy speech processing should be performed on the laptop.

---

## Speaker

Preferred architecture:

* I2S audio output
* Small speaker
* Digital amplifier such as a MAX98357A-class device

The speaker is intended for:

* AI responses
* Notifications
* Status feedback
* Optional voice prompts

---

# Display

Preferred:

**Small round color display**

Example class:

* 1.28-inch round TFT
* 240 × 240
* GC9A01-class controller

The display should provide a minimal wearable UI.

Suggested primary screens:

## Home

```text
72 BPM

4,218 STEPS

18:42
```

## Activity

```text
RUNNING

147 BPM

HIGH
```

## Body

```text
HR       72
SpO₂     97
QUALITY  GOOD
```

## Assistant

```text
ASSISTANT

Hold button
to talk
```

The UI should remain simple.

Do not spend project time building a smartphone-level smartwatch interface.

---

# Touch / Controls

Touchscreen support is optional.

The preferred initial control system is:

* One physical button
* Optional capacitive touch input

The button should be capable of triggering actions such as:

* screen wake
* screen switching
* assistant activation
* start/stop workout

A full touchscreen interface is not a core requirement.

---

# GPS

GPS is optional.

It should not be part of the critical path.

GPS may be added later if:

* the hardware budget permits it,
* power consumption remains acceptable,
* integration does not interfere with the main project,
* enough development time remains.

Do not delay the core project because GPS is unavailable.

---

# Processing Architecture

The project follows a **split-processing architecture**.

## On the ESP32-S3

The wearable should handle:

* Sensor acquisition
* Filtering
* Basic signal processing
* Feature extraction
* Activity detection/classification
* Exertion estimation
* UI rendering
* Audio buffering
* BLE communication
* Power management
* Device state management

## On the Laptop

The laptop should handle:

* Speech-to-text
* Large language model inference
* Higher-level reasoning
* Text-to-speech
* Historical analysis
* Optional advanced ML
* Development/debugging dashboard

This keeps the wearable computationally realistic.

---

# High-Level Architecture

```text
                         WEARABLE
                    ┌─────────────────┐
                    │    ESP32-S3     │
                    │                 │
 IMU ──────────────►│ Sensor Tasks    │
                    │                 │
 PPG ──────────────►│ Signal Process  │
                    │                 │
 MIC ──────────────►│ Audio Task      │
                    │                 │
                    │ Activity Engine │
                    │ Exertion Engine │
                    │                 │
                    │ UI / Display    │
                    │                 │
                    │ BLE / Wi-Fi     │
                    └────────┬────────┘
                             │
                         BLE / Wi-Fi
                             │
                             ▼
                         LAPTOP
                    ┌─────────────────┐
                    │ Data Receiver   │
                    │                 │
                    │ Sensor State    │
                    │    Engine       │
                    │        │        │
                    │        ▼        │
                    │      STT        │
                    │        │        │
                    │        ▼        │
                    │      LLM        │
                    │        │        │
                    │        ▼        │
                    │      TTS        │
                    └────────┬────────┘
                             │
                         Response
                             │
                             ▼
                         WEARABLE
                          Speaker
```

---

# RTOS Architecture

Zephyr RTOS is a core part of the project.

The firmware should use a proper task/thread architecture rather than a single monolithic loop.

Possible task structure:

```text
IMU Task
   ↓
Sensor Data Queue

PPG Task
   ↓
PPG Processing Queue

Audio Task
   ↓
Audio Buffer

Activity Task
   ↓
Current Activity State

Exertion Task
   ↓
Current Exertion State

BLE Task
   ↓
Telemetry / Commands

Display Task
   ↓
UI State

Power Management Task
   ↓
Sleep / Wake / Battery State
```

Potential Zephyr mechanisms:

* Threads
* Message queues
* Work queues
* Semaphores
* Mutexes
* Timers
* Interrupts
* Device tree
* Drivers
* Logging
* Power management

The exact implementation should be chosen based on the timing and data-rate requirements.

---

# Suggested Task Priorities

A possible starting point:

```text
Highest
│
├── IMU acquisition
├── PPG acquisition
├── Critical sensor processing
│
├── BLE communication
├── Activity processing
├── Audio processing
├── Display/UI
│
└── Logging / diagnostics
Lowest
```

These priorities are not fixed.

The implementation should be validated using actual timing and CPU-load measurements.

Do not choose priorities arbitrarily just for documentation.

---

# Sensor Data Pipeline

The basic pipeline should be:

```text
Raw Sensor Data
       ↓
Sampling
       ↓
Filtering
       ↓
Feature Extraction
       ↓
State Estimation
       ↓
Telemetry / UI / AI Context
```

For IMU:

```text
Accelerometer
+
Gyroscope
      ↓
Filtering
      ↓
Motion Features
      ↓
Activity Classification
```

For PPG:

```text
PPG
 ↓
Filtering
 ↓
Peak / waveform analysis
 ↓
Heart Rate
 ↓
Signal Quality
```

---

# Activity Recognition

The project should initially support a small number of reliable states.

Recommended initial classes:

```text
REST
WALK
RUN
ACTIVE
UNKNOWN
```

The system may later be extended with additional exercise categories.

Do not attempt a large activity taxonomy until the basic classifier is stable.

A simple rule-based classifier can be used initially.

A lightweight ML classifier may be added afterward.

Possible features:

* Accelerometer magnitude
* Gyroscope magnitude
* Variance
* Signal energy
* Frequency-domain features
* Step frequency
* Motion intensity

---

# Exertion Estimation

The system should combine multiple signals rather than using heart rate alone.

Possible inputs:

```text
Heart Rate
+
Heart Rate Trend
+
Motion Intensity
+
Activity
+
Session Duration
```

Result:

```text
REST
LIGHT
MODERATE
HIGH
```

This should be treated as a **project-specific wellness/exertion metric**, not a medical measurement.

The exact scoring method should be documented and experimentally validated.

---

# AI Assistant

The AI assistant is a laptop-hosted component.

It should not require a large model to run on the ESP32.

Possible processing flow:

```text
User Speech
    ↓
Watch Microphone
    ↓
BLE / Wi-Fi
    ↓
Laptop STT
    ↓
Intent / Query
    +
Current Sensor State
    ↓
LLM
    ↓
Response Text
    ↓
TTS
    ↓
Watch Speaker
```

The AI should receive structured context.

Example:

```json
{
  "heart_rate": 147,
  "spo2": 97,
  "activity": "running",
  "motion_intensity": 0.84,
  "exertion": "high",
  "session_time_seconds": 501
}
```

The AI should use this context when appropriate.

---

# Example AI Interaction

User:

> "How am I doing?"

Sensor context:

```text
Activity: Running
HR: 147 BPM
Motion: High
Exertion: High
Duration: 8 min 21 sec
```

Possible response:

> "You're currently running at high exertion with a heart rate of 147 BPM. You've maintained this intensity for about eight minutes."

The response should be grounded in available sensor data.

The assistant must not invent measurements.

If sensor quality is poor or data is unavailable, it should say so.

---

# Medical Safety Boundary

This is a fitness/wellness prototype.

It is not a medical device.

Do not implement or claim:

* Disease diagnosis
* Medical diagnosis
* Clinical-grade SpO₂
* Clinical-grade ECG
* Emergency medical detection
* Medical treatment recommendations

Sensor values should be presented as estimates where appropriate.

---

# Communication

Preferred communication method:

**BLE**

BLE should be used for:

* Telemetry
* Commands
* Device configuration
* Assistant interaction
* State synchronization

Wi-Fi may be used for:

* Development
* Debugging
* Higher-bandwidth communication
* Future functionality

BLE should be preferred for the wearable's normal low-power operation.

---

# Data Format

Use a clear structured telemetry representation.

Example:

```json
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
```

The exact protocol may be binary or JSON depending on performance requirements.

Do not optimize prematurely.

Start with a simple, debuggable protocol.

---

# PCB

The final device should use a custom PCB.

Target:

**2-layer PCB**

Maximum:

**3-layer PCB if necessary**

Preferred component type:

**SMD**

The PCB should integrate as many of the following as practical:

* ESP32-S3 module
* IMU
* PPG sensor
* Power management
* Battery connector
* Display connector
* Microphone
* Audio amplifier
* Buttons
* Charging circuitry
* Status indicators

Avoid unnecessary breakout boards in the final wearable.

Breakout boards are acceptable during prototyping.

---

# PCB Design Priorities

PCB design should prioritize:

1. Electrical correctness
2. Signal integrity
3. Power integrity
4. Compactness
5. Manufacturability
6. Wearable form factor

Do not sacrifice reliability purely to achieve a visually perfect circular PCB.

A practical PCB shape is preferable to an unnecessarily difficult layout.

---

# Power System

The wearable will likely use a small rechargeable Li-ion/LiPo battery.

The power system should include:

* Battery connector
* Charging circuit
* Regulation
* Power monitoring where practical
* Proper decoupling
* Power gating where beneficial

Power consumption should be measured rather than assumed.

Potential low-power states:

```text
Active
Idle
Display Off
Sensor Reduced Rate
Deep Sleep
```

Battery life is not required to be smartwatch-commercial-grade, but the design should demonstrate awareness of embedded power constraints.

---

# CAD / Enclosure

The physical wearable should be designed in CAD.

The enclosure should account for:

* Round display
* PCB
* Battery
* Sensor placement
* PPG optical contact
* Microphone opening
* Speaker opening
* Button
* Charging access
* Wrist strap

A 3D-printed enclosure is acceptable and preferred for prototyping.

The PPG sensor should have sensible physical contact with the wrist.

---

# Software Structure

Suggested repository structure:

```text
/
├── README.md
│
├── firmware/
│   ├── src/
│   ├── include/
│   ├── boards/
│   ├── dts/
│   ├── drivers/
│   ├── sensors/
│   ├── ui/
│   ├── audio/
│   ├── bluetooth/
│   ├── fitness/
│   └── power/
│
├── laptop/
│   ├── receiver/
│   ├── ai/
│   ├── stt/
│   ├── tts/
│   ├── dashboard/
│   └── tools/
│
├── pcb/
│   ├── schematic/
│   ├── layout/
│   └── gerbers/
│
├── cad/
│   ├── enclosure/
│   └── renders/
│
├── docs/
│   ├── architecture/
│   ├── testing/
│   └── experiments/
│
└── scripts/
```

The actual repository structure may change as implementation progresses.

---

# Development Philosophy

The project should be developed incrementally.

Do not attempt to build the full system at once.

Recommended progression:

```text
ESP32-S3
   ↓
Zephyr boots
   ↓
Display works
   ↓
IMU works
   ↓
PPG works
   ↓
RTOS tasks work concurrently
   ↓
Activity detection
   ↓
BLE telemetry
   ↓
Laptop receiver
   ↓
Voice pipeline
   ↓
AI context integration
   ↓
Custom PCB
   ↓
Enclosure
   ↓
Final integration
```

Every stage should be independently testable.

---

# Five-Week Development Constraint

The project is expected to reach a demonstrable prototype within approximately **5 weeks**.

Therefore:

## Core Features

These should receive the highest priority:

* ESP32-S3
* Zephyr RTOS
* IMU
* Heart-rate/PPG
* Round display
* BLE
* Activity recognition
* Exertion estimation
* Laptop AI assistant
* Voice interaction
* Custom PCB
* CAD enclosure

## Secondary Features

Only implement these after the core system is stable:

* SpO₂ improvements
* Touchscreen
* Wi-Fi
* Advanced activity recognition
* Better audio integration
* Power optimization
* Historical analytics

## Stretch Features

Potential future additions:

* GPS
* More sophisticated ML
* Personalized activity models
* Sleep analysis
* Smartphone application
* Notifications
* Advanced gesture recognition

Do not allow stretch features to destabilize the core system.

---

# Suggested Timeline

## Week 1 — Hardware Bring-Up

Targets:

* ESP32-S3
* Zephyr
* Display
* IMU
* PPG

Deliverable:

**Live sensor values on display**

---

## Week 2 — RTOS Architecture

Targets:

* Threads
* Sensor queues
* Interrupts
* Timing
* BLE
* Logging

Deliverable:

**Multiple sensor and UI tasks running concurrently under Zephyr**

---

## Week 3 — Fitness Intelligence

Targets:

* Step estimation
* Motion analysis
* Activity classification
* Heart-rate filtering
* Exertion estimation

Deliverable:

**Wearable can determine basic user activity and exertion state**

---

## Week 4 — AI Integration

Targets:

* BLE telemetry
* Laptop receiver
* STT
* LLM
* TTS
* Sensor-context injection

Deliverable:

**User can speak to the assistant and receive a context-aware response**

---

## Week 5 — Hardware Integration

Targets:

* PCB
* SMD assembly
* CAD enclosure
* 3D printing
* Final UI
* Testing
* Documentation

Deliverable:

**Integrated wearable prototype**

---

# Engineering Requirements

The implementation should prioritize:

* Reliability
* Deterministic behavior
* Modular firmware
* Clean interfaces
* Low coupling
* Debuggability
* Measurable performance
* Power awareness

Avoid:

* Giant monolithic source files
* Hidden global state
* Blocking loops
* Arbitrary delays
* Arduino-style architecture
* Hard-coded sensor assumptions
* Fake sensor values
* Unnecessary dependencies
* Premature optimization

---

# Agent Instructions

When working on this project, follow these rules.

## 1. Preserve the Architecture

Do not casually replace:

* ESP32-S3
* Zephyr RTOS
* BLE
* laptop-hosted AI

with unrelated technologies.

Any architectural change should have a concrete engineering reason.

---

## 2. Do Not Overbuild

The five-week schedule is a real constraint.

Always prioritize the smallest implementation that proves the intended concept.

Before implementing a new feature, determine whether it belongs to:

* Core
* Secondary
* Stretch

---

## 3. Prefer Incremental Validation

After implementing a component:

* Build it
* Flash it
* Test it
* Verify logs
* Measure behavior
* Only then integrate the next component

Do not stack multiple unverified changes together.

---

## 4. Use Real Hardware Constraints

Assume:

* Limited MCU resources
* Limited battery capacity
* Limited PCB area
* Limited development time
* Sensor noise
* BLE bandwidth limitations

Engineering decisions should account for these constraints.

---

## 5. Do Not Fake Functionality

Do not claim that a feature works because the code compiles.

For every major feature, distinguish between:

```text
Implemented
Tested
Partially Tested
Prototype
Planned
```

Use simulated sensor data only when explicitly working on software before hardware is available.

---

## 6. Keep Hardware Abstraction Clean

Application code should not depend unnecessarily on raw GPIO/SPI/I2C implementation details.

Use appropriate driver/service boundaries.

The intended flow is:

```text
Hardware Driver
      ↓
Sensor Interface
      ↓
Processing
      ↓
Application
```

---

## 7. Zephyr Is a Core Project Requirement

Do not bypass Zephyr's architecture with an Arduino-style main loop.

Prefer appropriate Zephyr mechanisms:

* `k_thread`
* `k_msgq`
* `k_fifo`
* `k_sem`
* `k_mutex`
* `k_timer`
* Work queues
* Device tree
* Zephyr drivers
* Bluetooth APIs
* Logging subsystem

Use the mechanism that fits the actual problem.

---

## 8. Favor Observable Systems

Important states should be observable through:

* Logs
* Debug output
* Display
* Telemetry
* Metrics

The development process should make it easy to determine:

* Sensor sampling rate
* Task timing
* CPU load
* Queue utilization
* BLE connection state
* Sensor quality
* Activity state
* Battery state

---

# Testing

Testing should happen at multiple levels.

## Unit-Level

Test:

* Filters
* Feature extraction
* Classifiers
* Exertion calculations
* Data parsing

## Hardware-Level

Test:

* IMU
* PPG
* Display
* Microphone
* Speaker
* BLE

## System-Level

Test:

```text
Sensors
 ↓
ESP32
 ↓
BLE
 ↓
Laptop
 ↓
AI
 ↓
Response
 ↓
Wearable
```

## Physical Tests

Test the system while:

* Resting
* Walking
* Running
* Performing repeated movements
* Speaking to the assistant
* Moving the wrist during PPG acquisition

---

# Success Criteria

The project is successful when a functional wearable prototype can:

1. Boot using Zephyr RTOS.
2. Acquire IMU data.
3. Acquire usable heart-rate data.
4. Display real-time information.
5. Estimate basic activity state.
6. Estimate an exertion state.
7. Send telemetry to a laptop.
8. Receive user voice input.
9. Process the query on the laptop.
10. Provide the AI with current sensor context.
11. Generate a response.
12. Return the response to the wearable through audio and/or display.
13. Operate using a custom PCB.
14. Fit within a custom CAD-designed enclosure.

---

# Definition of Done

A feature is considered complete only when:

```text
Code exists
   +
Code builds
   +
Hardware/software integration works
   +
Feature has been tested
   +
Failure cases are understood
   +
Documentation reflects reality
```

Do not mark features complete simply because their source code exists.

---

# Final Design Principle

The project should remain centered around one idea:

> **A wearable embedded system that understands the user's current physical context and uses that context to provide useful, real-time interaction.**

Everything else should support that objective.

The goal is not to reproduce every feature of a commercial smartwatch.

The goal is to demonstrate a well-engineered combination of:

**Embedded Hardware + Zephyr RTOS + Real-Time Sensing + Sensor Processing + Wireless Communication + AI + Custom PCB + Mechanical Design**
