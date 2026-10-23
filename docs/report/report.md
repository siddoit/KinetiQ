# KINETIQ — Real-Time Wellness Mirror with FreeRTOS and Sensor-Grounded AI

### *Mirror, Mirror — a wellness mirror that shows live state, listens, and answers*

**B.Tech Minor Project — Final Status Update**
**Programme:** [B.Tech — Department Name, College Name]
**Academic Year:** 2026–27
**Prepared:** 23 October 2026

| Role | Name | Contribution |
|---|---|---|
| Software, RTOS and mirror | Sidharth Sajith | Native FreeRTOS node, telemetry contract, browser mirror, AI and integration scripts |
| Wearable integration and figures | Tharun Oommen Jacob | Huawei Band 6 relay, Android streamer, receiver, field evidence and figures |

**Repository:** github.com/siddoit/KinetiQ
**Faculty guide:** [Name — to be filled]

> **Safety boundary.** KINETIQ is a wellness-assistance prototype. It is not a medical device, does not diagnose or treat conditions, and must not be used for clinical decisions or emergency alerts.

---

## Abstract

KINETIQ is a laptop-hosted wellness mirror that brings live telemetry into a conversational interface. Its screen is the mirror's face; the laptop microphone and speakers provide speech interaction; a browser dashboard shows the origin and freshness of every value. The implementation deliberately separates two evidence lanes that share one strict telemetry contract. **Lane A** carries genuine Huawei Band 6 values through Huawei Health, an Android Accessibility-service streamer, an authenticated local receiver and a validation bridge. **Lane B** is an offline, deterministic FreeRTOS control path: a native Windows simulator running the real FreeRTOS v11 kernel emits one schema-valid JSON frame per second to the same mirror. Lane A demonstrates real wearable biometrics when a live Health reading is available; Lane B demonstrates RTOS tasks, queues, timing diagnostics and reproducible activity changes without depending on a phone, network or venue.

At the shared boundary, every packet must contain exactly eight fields: timestamp, heart rate, SpO₂, activity, motion intensity, exertion, steps and PPG-quality status. Invalid or unknown fields are rejected. The browser mirror derives LIVE, UNCHANGED, STALE and OFFLINE labels from local receipt and value-change clocks, not from a cached wearable timestamp. The assistant uses the current validated packet as context and returns a visible grounding verdict. It supports local Ollama, an optional HTTP API and a deterministic telemetry-grounded template fallback; browser voice input uses faster-whisper when installed.

The project is transparent about provenance. The Band 6 path does not invent motion or activity: the bridge marks them "unknown" and "0.00" because Huawei Health supplies only observed biometric tiles. The native FreeRTOS path is synthetic physiology, clearly labelled as an RTOS control simulation rather than a human measurement. A documented 22 October field run relayed Huawei Health values of 60 BPM, 100% SpO₂, stress 57 and 4,181 steps end-to-end.

---

## 1. Objectives and Requirement Coverage

The project solves an integration problem: wearables contain useful wellness data, but custom applications commonly receive it only through proprietary apps, cached displays or restrictive APIs. A general assistant can also sound useful without knowing a user’s present state. KINETIQ turns clearly labelled, validated state into a visual and conversational wellness experience rather than claiming clinical assessment.

The final objectives are:

1. demonstrate task, queue and timing behaviour under FreeRTOS;
2. move validated telemetry into a laptop interface over a local, inspectable transport;
3. show genuine Band 6 values without bypassing, modifying or impersonating Huawei Health;
4. keep provenance and recency visible so cached data cannot silently appear current; and
5. provide text/voice assistance whose response is checked against current telemetry.

| Requirement | KINETIQ evidence |
|---|---|
| RTOS | Native node uses real FreeRTOS v11 tasks, queue, mutex and periodic scheduling; Wokwi retains an ESP32 FreeRTOS exhibit. |
| IoT | Local phone-to-laptop HTTP relay, authenticated POST ingestion, receiver polling/bridge, newline JSON stdin transport and browser API. |
| Embedded systems | ESP32/Wokwi task architecture, GPIO/I²C pin plan and a FreeRTOS node with explicit producer/consumer diagnostics. |
| Innovation | A mirror UI combines provenance, freshness, browser voice/text interaction and a telemetry-grounding verdict. |

---

## 2. Final Architecture

KINETIQ has one shared browser mirror and two intentionally separate input lanes. The separation is a safety and demonstration feature: a deterministic simulated lane cannot be mistaken for the genuine wearable lane.

    LANE A — genuine wearable data (local only)

    Huawei Band 6 ──BLE──> Huawei Health on phone
                                │ visible home-screen cards; no app modification
                                v
                  KinetiQ Streamer Android AccessibilityService
                                │ authenticated HTTP POST
                                v
            local receiver :8000 ──> genuine-only bridge ──> stdin
                                                              │
    LANE B — deterministic RTOS control                         v
                                                      KINETIQ browser mirror
    native FreeRTOS v11 node ──stdout JSON─────────────────> :8777 / :8778
         │ stderr: task, queue and timing diagnostics              │
         └─ keys: 1 rest, 2 walk, 3 run, b fall, 0 auto             ├─ dashboard + freshness
                                                                     └─ grounded text/voice assistant

The mirror is served by "laptop/web/app.py". It accepts newline-delimited telemetry in "--stdin" mode and exposes telemetry, question and transcription APIs. It binds only to 127.0.0.1 and chooses port 8777 or 8778. The optional browser-camera feature is a coarse pixel-difference motion proxy; it is **not** face-pose or posture diagnosis.

### 2.1 Lane A — Huawei Band 6 relay

The phone remains paired with Huawei Health. The KinetiQ Streamer Android app uses the Android Accessibility API to read visible home-screen tiles and posts them to a local receiver. The receiver maintains separate "real" and "simulated" slots; only approved Huawei Band 6 sources marked "simulated: false" enter the real slot. Its POST endpoint requires a token.

"kinetiq-integration/band_bridge.py" emits a mirror packet only when the reading is live, genuine and complete for heart rate, SpO₂ and steps. It never reads the simulated slot.

| Mirror field | Lane A source | Provenance |
|---|---|---|
| heart_rate, spo2, steps | Huawei Health home-screen values | genuine Band 6 values, relayed locally |
| activity | unknown | not provided by this relay |
| motion_intensity | 0.00 | not provided by this relay |
| exertion | existing mirror heuristic applied to heart rate | derived, not measured by the band bridge |
| ppg_quality | good when Health supplies a completed SpO₂ value | proxy, not raw PPG quality |

The Health home screen must stay foregrounded and refresh for a new reading to arrive. Stress remains in wearable-side provenance data because the eight-field mirror schema has no stress field.

### 2.2 Lane B — native FreeRTOS control path

"hardware/native/" builds a Windows process around the real FreeRTOS v11 kernel and its Win32/MinGW simulator port.

| Task | Period / role |
|---|---|
| Console | polls keyboard input every 50 ms; changes virtual activity or triggers a fall event |
| Sensor | produces virtual IMU/environment samples every 100 ms into an eight-slot queue |
| Classify | drains the queue and applies hysteresis to rest, walk or run |
| Telemetry | updates synthetic physiology and emits one JSON packet per second |

Telemetry is the sole stdout writer, keeping the pipe clean for the mirror; status stays on stderr. Diagnostics use a monotonic QueryPerformanceCounter clock to show measured mean period, maximum lateness, missed deadlines, queue high-water mark and counted oldest-first drops. The producer intentionally exceeds the classifier’s drain rate, making queue pressure visible rather than hidden. This demonstrates FreeRTOS scheduling and dataflow, not hard real-time behaviour: Windows remains the host scheduler and physiology values are synthetic.

### 2.3 ESP32 and Wokwi artefacts

"hardware/sim/sketch.ino" retains an ESP32/Wokwi implementation with FreeRTOS tasks, queue-based telemetry, an activity potentiometer and a fall button. "firmware/" is the earlier Zephyr design and remains architectural reference, not the shipping or field-tested firmware path. The ESP32 BOM, GPIO plan and board bring-up material are design artefacts; this report does not claim a new physical-sensor measurement from them.

---

## 3. Telemetry Contract and Freshness

All mirror lanes must satisfy "laptop/receiver/telemetry_schema.py". Unknown and missing keys are rejected before rendering or AI context use.

    {"timestamp":1750000000,"heart_rate":72,"spo2":97,"activity":"rest","motion_intensity":0.08,"exertion":"rest","steps":4218,"ppg_quality":"good"}

| Field | Validation |
|---|---|
| timestamp | positive integer Unix second |
| heart_rate | integer 30–220 BPM |
| spo2 | integer 70–100 percent |
| activity | rest, walk, run, active or unknown |
| motion_intensity | numeric 0.00–1.00 |
| exertion | rest, light, moderate or high |
| steps | integer greater than or equal to zero |
| ppg_quality | good or poor |

The mirror uses local monotonic clocks for first acceptance, most recent arrival and last observed change in data-bearing values. It does not accept a source timestamp as proof of currentness, because a phone can repost a cached tile with a new POST timestamp.

- **LIVE** — visible values changed within 60 seconds.
- **UNCHANGED** — arrivals continue, but observed values have remained unchanged for at least 60 seconds.
- **STALE** — no valid arrival for more than 60 seconds.
- **OFFLINE** — no valid arrival for more than 300 seconds; numerical tiles are blanked.

"LINK" is independent of value freshness: it shows whether any valid line is still arriving. This makes “phone connected, same Band value” distinguishable from “pipe disconnected.”

---

## 4. Mirror and AI Assistance

The UI renders heart rate, SpO₂, activity, exertion, steps, PPG label, motion intensity and a 60-sample heart-rate trace. It displays lane, source, link and freshness next to each interaction. A compact chat tray provides typed questions, optional local speech synthesis and a browser microphone flow. Audio is capped at approximately 1 MB, decoded to 16 kHz mono through PyAV and transcribed with faster-whisper tiny.en when available.

"laptop/ai/assistant.py" supplies the advisor logic. A ContextManager holds the latest validated packet; PromptBuilder incorporates it in the question prompt; and AIAdvisor tries Ollama (gemma2:2b), then an optional HTTP API, then a deterministic telemetry-grounded template. It gives wellness-only suggestions such as rest, hydration, stretching or a brief walk.

After a reply is produced, verify_grounding checks it against the current payload. A normal reply must mention live activity or exertion; an exact current heart-rate reference beside a heart/BPM term, or an exact step count, is accepted as direct biometric grounding. Poor PPG quality must be acknowledged. The client shows PASS or FAIL beside the response. A PASS is required for a valid stage demonstration; a failed verdict is not evidence of a sensor-grounded recommendation and must be corrected or replaced by the deterministic template.

---

## 5. Verification Evidence

The project has repeatable software gates and a separately documented wearable field result. These evidence types must not be conflated.

| Item | Evidence | Status and interpretation |
|---|---|---|
| Schema boundary | strict parser accepts only the eight locked fields and values in range | malformed frames are rejected |
| Dashboard mock gate | 75 frames over 15 s with zero drops in the recorded headless run | laptop logger regression evidence, not sensor evidence |
| BLE client mock | 30 packets routed with zero schema rejections | framing/routing evidence; no claim of a live GATT server |
| Grounded assistant | local LLM/template paths emit an explicit grounding verdict | PASS is required for a valid demonstration response |
| Speech path | faster-whisper real path measured 0.48 s on a synthesized clip; earlier benchmark median 0.45 s | local STT evidence; venue microphone remains environment-dependent |
| FreeRTOS path | native v11 simulator, task/queue diagnostics and schema JSON share the mirror stdin path | deterministic offline RTOS-control demonstration |
| Huawei Band 6 path | documented 22 Oct Health UI relay: HR 60, SpO₂ 100%, stress 57, steps 4,181 | genuine wearable data when a fresh Health tile is available |

During this report update, the active Python modules (web app, assistant, telemetry schema, dashboard, BLE client and voice pipeline) compiled successfully with the project virtual environment. This lightweight check is not reported as a device or field test.

### 5.1 Stage launchers

    # Lane B: native FreeRTOS control -> browser mirror, offline
    powershell -ExecutionPolicy Bypass -File scripts\demo_rtos_lane.ps1

    # Lane A: genuine Band 6 receiver + bridge -> browser mirror
    powershell -ExecutionPolicy Bypass -File scripts\demo_band_lane.ps1

    # Fallback drill: start Lane B; do not fabricate Band frames
    powershell -ExecutionPolicy Bypass -File scripts\demo_fallback.ps1

"KINETIQ_DEMO.bat" remains a menu launcher. "scripts/morning_check.ps1" provides an additional pre-stage smoke check; its camera and Ollama outcomes are environment-dependent.

---

## 6. Limitations and Risk Controls

| Limitation | Control / honest presentation |
|---|---|
| Band 6 route is not direct BLE decoding | Huawei Health remains untouched; the Android service reads visible Health UI values. Missing fields stop the bridge instead of becoming zeros. |
| Band data can be unchanged or stale | Show LIVE/UNCHANGED/STALE/OFFLINE and link state. Refresh Huawei Health rather than presenting cached values as live. |
| Band bridge has no motion/activity measurement | Keep activity=unknown and motion_intensity=0.00; do not describe them as Band-derived. |
| ppg_quality=good on Lane A is a completion proxy | It means Health supplied SpO₂, not that KINETIQ examined raw PPG quality. |
| Native node is a simulation | Label it RTOS CONTROL; Windows timing is best-effort and physiology values are synthetic. |
| ESP32 hardware path is not current field evidence | Retain Wokwi and BOM as design work; do not substitute simulation values for measured sensor values. |
| Local receiver security | POST is token-gated. Run unauthenticated read endpoints only on a trusted LAN or USB adb reverse; do not expose the demo service publicly. |
| LLM and speech are fallible | Use the visible grounding verdict, template fallback and typed-input fallback. Never treat an answer as medical advice. |
| Browser camera is optional | It is a coarse motion proxy; no posture or biometric claim depends on it. |

---

## 7. Conclusion

KINETIQ now presents a clear final project: a single wellness mirror renders two sources with different evidential weight without confusing one for the other. Lane A supplies genuine Band 6 biometrics through an ethical, local Huawei Health relay. Lane B supplies a reproducible, offline FreeRTOS demonstration with visible task, queue and scheduling instrumentation. Both pass through the same strict telemetry validator and freshness model before reaching the dashboard or conversational assistant.

The result is not a medical product and is not presented as one. Its contribution is an inspectable architecture for turning live—or honestly labelled simulated—wellness telemetry into a context-aware mirror interaction. The source label, freshness state, bridge gates and grounding verdict are as important to the demonstration as the dashboard numbers themselves.

---

## References

1. Amazon FreeRTOS. *FreeRTOS Kernel Developer Documentation*. https://www.freertos.org/Documentation/
2. Espressif Systems. *Arduino-ESP32 and ESP32 Documentation*. https://docs.espressif.com/
3. Wokwi. *ESP32 Simulator Documentation*. https://docs.wokwi.com/
4. Android Developers. *AccessibilityService*. https://developer.android.com/reference/android/accessibilityservice/AccessibilityService
5. Huawei. *Huawei Health* application. Band data is read from its visible UI; KINETIQ does not bypass the paired application.
6. SYSTRAN. *faster-whisper*. https://github.com/SYSTRAN/faster-whisper
7. Ollama. *Local model runtime and Gemma 2 model support*. https://ollama.com/

---

*End of report — KINETIQ Minor Project final status update, 23 October 2026.*
