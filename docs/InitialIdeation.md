A custom ESP32-S3 smartwatch that continuously senses motion and basic physiological signals, runs a real-time sensor/fitness pipeline under Zephyr RTOS, and communicates with a laptop-hosted AI assistant that understands the wearer’s current activity and gives context-aware coaching through voice and the watch display.

The key idea is:

Sense → interpret → communicate → coach → verify

So the watch isn't trying to be an Apple Watch clone. The watch is the real-time embedded system, while the laptop acts as the AI brain.

What the user actually experiences

You wear the watch.

The round display shows things like:

72 BPM
Walking
Exertion: Moderate
Steps: 4,218

Then you can speak:

“How am I doing?”

The microphone captures the question and sends it to the laptop.

The laptop receives something like:

{
  "heart_rate": 128,
  "motion_state": "walking",
  "activity": "brisk_walk",
  "steps": 4218,
  "spo2": 97,
  "session_time": 21,
  "exertion_state": "moderate"
}

The AI assistant understands both the question and the current sensor context.

So instead of a generic chatbot saying:

“You should exercise regularly.”

it can say something like:

“You’ve been walking at a moderate intensity for about 20 minutes. Your heart rate is elevated but stable. Another 10 minutes at this pace would keep the session consistent.”

Then the watch could display:

GOOD PACE
128 BPM
MODERATE

That sensor-grounded AI interaction is the interesting part.

The actual niche

Don't pitch this as:

“A smartwatch with AI.”

That's generic as hell.

Pitch it as:

“A context-aware embedded fitness assistant that combines real-time physiological sensing, motion analysis and voice interaction.”

The interesting research/engineering problem becomes:

Can an embedded wearable infer the user's current physical state from multiple sensors and use that state to provide personalized, context-aware feedback?

That gives you an actual engineering story.

Hardware

I'd build around an ESP32-S3.

It's a much better fit than a basic ESP32 because you get substantially more headroom for RTOS tasks, BLE/Wi-Fi, peripherals and potentially lightweight ML.

Your watch PCB could contain:

Main processor

ESP32-S3-WROOM module

This handles:

Zephyr RTOS
sensor acquisition
BLE
display
buttons/touch
audio interface
activity classification
power management
communication with laptop

You absolutely do not need Linux or some fake “watch OS.”

The OS is:

Zephyr RTOS

and your firmware is the actual product.

Sensors
IMU

Use something like:

BMI270 / ICM-42688-P

The IMU becomes one of the most important sensors.

It can detect:

walking
running
resting
wrist movement
exercise motion
potentially basic exercise patterns
steps
sudden movement
orientation

You can eventually train a small classifier:

          IMU
           ↓
     feature extraction
           ↓
     activity classifier
           ↓
  REST / WALK / RUN / EXERCISE

That gives you an actual ML component rather than just “ChatGPT connected to an ESP32.”

Heart-rate / SpO₂

Use a PPG module such as a MAX3010x-family sensor.

From it you can derive:

heart rate
approximate SpO₂
pulse waveform

But here's an important engineering detail:

Don't pretend your SpO₂ value is medically accurate.

Motion causes huge PPG errors.

So make SpO₂ a secondary measurement, ideally taken during relatively stationary periods, while heart rate is the main continuous signal.

That actually gives you a nice engineering problem:

Motion artifact detection and signal quality estimation.

For example:

PPG signal
   ↓
Signal quality check
   ↓
Good? ───── No ───→ Ignore measurement
   │
  Yes
   ↓
Heart rate / SpO₂

That is much more impressive than blindly displaying a number.

Audio
Microphone

Use an I²S MEMS microphone, such as an INMP441-class device.

Speaker

Use an I²S amplifier such as a MAX98357A-class interface with a small speaker.

Then the watch can eventually do:

“Start workout.”

“How's my heart rate?”

“Am I walking or running?”

“Give me a status.”

The heavy speech processing stays on the laptop.

That's actually a smart architectural decision because you're not wasting ESP32 resources trying to run a full speech model.

Display

I would absolutely use the round display.

Something around:

1.28-inch / 240×240 round TFT

using a controller such as GC9A01.

This makes the project visually look like a smartwatch immediately.

The display can have several screens:

Home
      72 BPM

      4,218
       STEPS

     18:42
Workout
     RUNNING

      142
      BPM

   MODERATE
AI
     KINETIQ

  Listening...
Health
 HR       72
 SpO₂     97
 Motion   REST
Touchscreen?

Possible.

But this is where I'd be careful.

A touchscreen sounds cool, but in a 5-week project, it can become unnecessary integration work.

I'd actually use:

one side button + one capacitive touch/button

or perhaps:

one button + touchscreen

instead of building a complicated gesture UI.

Your engineering effort should go toward the RTOS architecture and sensor intelligence, not fighting with GUI drivers.

GPS

I'd remove GPS from the first version.

Honestly, GPS is one of those features that sounds important but isn't necessary for your actual concept.

Your project isn't:

“A GPS fitness tracker.”

Your project is:

context-aware fitness intelligence.

You already have plenty of data:

IMU + HR + PPG + voice + time + activity state

GPS can become a stretch goal.

The really interesting part: Zephyr RTOS

This is where I'd make the project academically strong.

Don't just say:

“We used Zephyr.”

Actually design the firmware around RTOS concurrency.

For example:

                 ESP32-S3
                     │
             ┌───────┴────────┐
             │   Zephyr RTOS  │
             └───────┬────────┘
                     │
       ┌─────────────┼─────────────┐
       │             │             │
       ↓             ↓             ↓
   IMU Task       PPG Task      Audio Task
       │             │             │
       ↓             ↓             ↓
 Activity        HR/SpO₂        Voice
 Detection       Processing     Buffer
       │             │             │
       └─────────────┼─────────────┘
                     ↓
               Sensor Manager
                     │
           ┌─────────┴─────────┐
           ↓                   ↓
       Display Task        BLE Task
           │                   │
           └─────────┬─────────┘
                     ↓
                  Laptop

And then maybe:

Power Task
System Monitor
Watchdog
Logging Task

Use actual Zephyr mechanisms:

Threads
Queues
Semaphores
Timers
Device tree
Interrupts
Work queues
BLE stack
power states

That becomes a real RTOS project, not just an Arduino sketch with loop().

Why RTOS actually matters

You can demonstrate something like:

At the same time:

100 Hz IMU sampling

25–100 Hz PPG sampling

BLE communication

display refresh

voice recording

button handling

activity classification

and perhaps:

low-power sleep

All running concurrently.

Then you can demonstrate scheduling.

For example:

IMU Task        → High priority
PPG Task        → High priority
BLE Task        → Medium
Activity ML     → Medium
Display         → Medium
Audio           → Medium
UI              → Low
Logging         → Low

Now you have something excellent to talk about in a viva:

“We selected Zephyr because the wearable has multiple concurrent real-time sensing and communication workloads with different timing requirements.”

That's a much stronger justification than “Zephyr is modern.”

Laptop AI architecture

This is where I'd make the system clever.

The laptop runs:

AI Assistant Server

and receives watch telemetry via:

BLE or Wi-Fi

For example:

WATCH
ESP32-S3
   │
   │ BLE
   ↓
LAPTOP
   │
   ├── Sensor Data Processor
   │
   ├── Activity Recognition
   │
   ├── STT
   │
   ├── LLM
   │
   └── TTS
   │
   ↓
WATCH

You can implement the laptop side in Python.

Something like:

watch_data.json
       ↓
Sensor fusion
       ↓
Current state
       ↓
AI context
       ↓
LLM
       ↓
Response
       ↓
TTS
       ↓
Bluetooth
       ↓
Watch speaker
STT / TTS

This part should also stay primarily on the laptop.

For speech-to-text:

Whisper-class model

For reasoning:

A local or API-based LLM.

For TTS:

Any lightweight local TTS engine.

So the watch doesn't need to understand speech.

It just does:

MIC
 ↓
audio buffer
 ↓
BLE
 ↓
Laptop STT

and response comes back:

Laptop
 ↓
TTS
 ↓
audio
 ↓
BLE
 ↓
Watch speaker

That keeps the ESP32 workload realistic.

The AI feature I would make the centerpiece

Here's where we make it unique.

Context-Aware Exertion Coach

The AI doesn't simply answer questions.

It knows what your body/device is currently doing.

For example:

User:

“Should I slow down?”

Watch says:

HR = 157
Activity = running
Duration = 27 min
Motion intensity = high

The AI gets that context.

Another example:

User:

“Why am I getting tired?”

The system can combine:

Current HR
Previous HR
Activity
Duration
Motion intensity
Rest periods
Recent workout history

and explain the observed pattern.

It shouldn't diagnose anything medically.

It's a fitness/wellness assistant, not a medical device.

An even cooler feature

I'd add a Fatigue / Exertion Trend rather than just displaying heart rate.

You could build an experimental score from:

heart rate
+
heart-rate trend
+
motion intensity
+
exercise duration
+
activity type

and classify:

REST
LIGHT
MODERATE
HIGH
VERY HIGH

Then your AI can reason using this state.

That gives the watch something like:

EXERTION: HIGH

instead of merely:

HR: 161

That's a much more meaningful system.

You'd explicitly describe the score as a project-specific wellness metric, not a clinical measurement.

Another genuinely nice feature: “Explain My State”

This could be your signature feature.

Press the watch button.

The watch records your question.

You ask:

“What's going on?”

The AI receives the current sensor state and responds:

“You're currently walking at moderate intensity. Your heart rate has increased steadily over the last five minutes and your movement is consistent.”

The important part is that the AI answer is grounded in sensor data.

That's the novelty.

Not:

AI + smartwatch

but:

AI + real-time physiological context.

PCB

I would absolutely make the PCB yourself.

Target:

2-layer PCB

with:

ESP32-S3 module
      │
 ┌────┼────────┐
 │    │        │
IMU  PPG    Display
 │    │        │
 │    │      Touch
 │    │
 │   Audio
 │
Battery / PMIC

You can make a circular PCB, but here's the engineering compromise I'd choose:

Circular outer PCB + rectangular flex/display connector area

rather than trying to cram every component into a perfect circle.

Use mostly:

SMD components

and avoid unnecessary through-hole parts.

That gives you something genuinely manufacturable and nice-looking.

CAD

Then your project isn't just electronics.

You design:

PCB

→

3D watch enclosure

→

strap mount

→

display bezel

→

sensor window

→

button

→

speaker/microphone openings

in Fusion 360 / SolidWorks / whatever CAD you're comfortable with.

And you can 3D print the enclosure.

Now the final demo becomes an actual custom wearable rather than a bunch of modules taped together.

Firmware architecture

I'd structure it roughly like:

/app
    main.c
    ui/
    fitness/
    ai_interface/
    power/

/drivers
    imu/
    ppg/
    display/
    audio/

/services
    activity/
    telemetry/
    bluetooth/
    sensor_fusion/

/boards
    custom_watch/

And your data flow:

Sensor Drivers
      ↓
Sensor Manager
      ↓
Filtering
      ↓
Feature Extraction
      ↓
Activity / Exertion
      ↓
Telemetry
      ↓
BLE

That gives you clean separation between hardware and application logic.

The watch UI

Keep it ridiculously simple.

Four screens.

HOME

72 BPM
4,218 steps
18:42

ACTIVITY

WALKING
128 BPM
MODERATE
22:41

BODY

HR       72
SpO₂     97
QUALITY  GOOD

AI

● READY

Hold button
to talk

That's enough.

Don't waste three weeks creating a smartwatch operating system.

What “OS” actually is

Don't call it:

“Our custom operating system.”

That's bullshit if you're using Zephyr.

Call it:

“A custom wearable firmware platform built on Zephyr RTOS.”

Much more technically accurate.

Your layers are:

Application
↓
Fitness / AI Interface
↓
Middleware
↓
Zephyr RTOS
↓
ESP32-S3 HAL / Drivers
↓
Hardware

That's your software architecture.

5-week reality check

This is the important bit.

You have five weeks, so don't build every fucking feature you've listed.

Week 1

Get the hardware alive.

ESP32-S3
Zephyr
IMU
Display
PPG

Make sure:

IMU → ESP32
PPG → ESP32
ESP32 → Display

works.

Week 2

Build the RTOS architecture.

Separate tasks:

IMU
PPG
UI
BLE
logging

Get stable concurrent operation.

Week 3

Build fitness intelligence.

Implement:

activity detection

step counting

heart-rate processing

exertion classification

Then BLE telemetry.

Week 4

Build laptop AI.

BLE → Python → sensor state
                    ↓
                   LLM
                    ↓
                   TTS

Then get:

voice question → sensor-aware answer

working.

Week 5

PCB + enclosure + polish.

This is when you:

finalize PCB
solder
3D print enclosure
integrate display
fix UI
clean wiring
benchmark RTOS
document everything
prepare demo
Your final demonstration

This is how I'd demo it.

You put the watch on.

Display:

REST

Then start walking.

After detecting motion:

WALKING

Heart rate changes:

94 BPM

Then run.

System:

RUNNING

147 BPM

HIGH EXERTION

Then press the AI button.

You say:

“How am I doing?”

Laptop processes the speech and current telemetry.

Watch replies through speaker:

“You're running at high exertion with a heart rate of 147 BPM. You've maintained this intensity for approximately eight minutes.”

Then show the laptop dashboard simultaneously:

       KINETIQ LIVE

Activity      RUNNING
Heart Rate       147
Motion          HIGH
Exertion        HIGH
Session       08:21
Connection     BLE

Now the examiner sees:

embedded systems + RTOS + sensors + PCB + firmware + ML + AI + CAD + communications

all in one project.

That's a fucking coherent project instead of 12 disconnected features.

Final project definition

I'd define it officially as:

KINETIQ is a custom RTOS-based wearable fitness and wellness assistant built around an ESP32-S3 and Zephyr RTOS. The system integrates inertial sensing and photoplethysmography to estimate physical activity, heart rate and exertion state in real time. A round embedded display provides immediate feedback, while BLE communication connects the wearable to a laptop-hosted AI assistant capable of speech interaction and sensor-context-aware fitness guidance. The project includes custom SMD PCB design, embedded firmware, real-time task scheduling, sensor processing, activity recognition, wireless communication and a 3D-printed wearable enclosure.

And the killer line for the presentation would be:

“Instead of making a smartwatch that simply measures fitness data, we are building a wearable that understands the user’s current physical context and communicates with them through that context.”

That is the direction I'd take.

One important constraint: do not make GPS, advanced touchscreen UI, full on-device AI, cloud backend, ECG, sleep tracking, notifications, music control, Android app, and ten other features part of the core scope. They will absolutely murder the five-week timeline. The core product is IMU + PPG + round display + Zephyr + BLE + sensor-aware AI voice assistant + custom PCB/enclosure. Everything else is a stretch goal.