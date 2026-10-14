# KINETIQ — Wokwi simulation (`hardware/sim/`)

Browser-only ESP32 "hardware" for the demo. Real FreeRTOS firmware
(`sketch.ino`) + exact pin plan from `docs/OVERHAUL_PLAN.md`, no board needed.

## Primary path — wokwi.com online editor (no account, no install)

1. Open **https://wokwi.com/projects/new/esp32** in Chrome/Edge (no login needed
   to simulate; sign-in only required to *save* — screenshot instead if needed).
2. Replace the default code with the full contents of **`sketch.ino`**.
3. Add the DHT library: click **+** next to files → `libraries.txt` → one line:
   `DHT sensor library for ESPx` (Wokwi installs it on build; stock DHT libs
   fail on ESP32 — this one is the documented ESP32-safe choice).
4. Open the **diagram.json** tab in the editor, delete its contents, paste the
   JSON from the **Appendix** below (generated 1:1 from `diagram.toml`), save.
5. Press the green **Play** button. Open the **Serial Monitor** (bottom panel).

**Expected output:** one JSON line per second, e.g.
`{"timestamp":1727544001,"heart_rate":73,"spo2":97,"activity":"rest",
"motion_intensity":0.01,"exertion":"rest","steps":0,"ppg_quality":"good"}`
Each JSON line is preceded by a plain-text RTOS status line, e.g.
`RTOS tick=12345 tasks=6 uptime_s=12 heap=31204` (never JSON — parsers that
keep only lines starting with `{` skip it automatically).

## Examiner controls (live in the browser sim)

- **Activity knob (`pot1`, wired to GPIO34):** drag the potentiometer to drive
  the activity state — left third → `rest`, middle third → `walk` (HR ~96,
  steps tick up), right third → `run` (HR ~132, steps tick faster). The Wokwi
  MPU6050 is static, so without the knob everything would sit at `rest`.
- **Red button (`btn1`) = fall event:** one press → short double-beep,
  `motion_intensity` spikes to ~0.95 with `activity` forced to `rest`
  (`fall` is NOT a valid schema value — see `laptop/receiver/telemetry_schema.py`,
  so the incident is encoded as `rest` + high intensity + `exertion: high`),
  then an ~8 s recovery ramp: HR spikes (~+30 bpm) and decays while intensity
  eases back to the knob baseline. Green LED + buzzer acknowledge as before.
- **RTOS status line:** `RTOS tick=<ticks> tasks=6 uptime_s=<s> heap=<bytes>`,
  once per second before the JSON line. Task priorities/periods/stacks live in
  the `RTOS CONFIG` block at the top of `sketch.ino` — point examiners at it.

## Fallback — VS Code + Wokwi extension

1. Install the **Wokwi Simulator** extension, open this folder.
2. Create `wokwi.toml` pointing at the Arduino-ESP32 build output + convert
   `diagram.toml` → `diagram.json` with the one-liner below, then `F1` →
   *Wokwi: Start Simulator*. (Needs a free Wokwi account token — that is why it
   is the fallback, not the primary.)

```python
python -c "import tomllib,json;d=tomllib.load(open('diagram.toml','rb'));json.dump({'version':d['version'],'author':d.get('author',''),'editor':'wokwi','parts':d['parts'],'connections':[[c['from'],c['to'],c.get('color',''),[]] for c in d['connections']]},open('diagram.json','w'),indent=2)"
```

## Wiring table (matches `diagram.toml`)

| ESP32 | Part pin | Wire | Note |
|---|---|---|---|
| 21 | `imu1:SDA` | green | I2C data |
| 22 | `imu1:SCL` | yellow | I2C clock |
| 3V3 | `imu1:VCC`, `dht1:VCC` | red | power |
| GND.1 | `imu1:GND`, `dht1:GND`, `bz1:1`, `btn1:2.r`, `led1:C` | black | ground |
| GND.2 | `imu1:AD0` | black | 0x68 address |
| 13 | `dht1:SDA` | green | DHT22 single data wire (not I2C) |
| 17 | `bz1:2` (+) | green | `tone()` beep |
| 4 | `btn1:1.r` | green | INPUT_PULLUP, active LOW |
| 16 → 220R → `led1:A` | `r1:1`→`r1:2`→`led1:A` | green | status LED |
| 34 | `pot1:SIG` | blue | examiner activity knob (ADC1, input-only) |
| 3V3 | `pot1:VCC` | red | knob supply |
| GND.1 | `pot1:GND` | black | knob ground |

## Honest scope note

**Serial→dashboard bridge = next week.** Tomorrow shows (v1) the simulated
device streaming schema-valid JSON in Wokwi + (v2) the laptop mock demo
separately. HR/SpO2 in the sim are synthetic (clearly commented in code —
no PPG sensor in the razor BOM).

## Appendix — paste-ready `diagram.json` (from `diagram.toml`)

```json
{
  "version": 1,
  "author": "KINETIQ team",
  "editor": "wokwi",
  "parts": [
    { "id": "esp", "type": "board-esp32-devkit-c-v4", "left": 0, "top": 0, "attrs": {} },
    { "id": "imu1", "type": "wokwi-mpu6050", "left": -180, "top": 80, "attrs": {} },
    { "id": "dht1", "type": "wokwi-dht22", "left": -180, "top": -120, "attrs": {} },
    { "id": "bz1", "type": "wokwi-buzzer", "left": 200, "top": 60, "attrs": {} },
    { "id": "btn1", "type": "wokwi-pushbutton-6mm", "left": 200, "top": -80, "attrs": { "label": "ALERT", "color": "red" } },
    { "id": "led1", "type": "wokwi-led", "left": 200, "top": -180, "attrs": { "color": "green" } },
    { "id": "r1", "type": "wokwi-resistor", "left": 110, "top": -180, "attrs": { "value": 220 } },
    { "id": "pot1", "type": "wokwi-potentiometer", "left": 200, "top": 160, "attrs": {} }
  ],
  "connections": [
    [ "esp:21", "imu1:SDA", "green", [] ],
    [ "esp:22", "imu1:SCL", "yellow", [] ],
    [ "esp:3V3", "imu1:VCC", "red", [] ],
    [ "esp:GND.1", "imu1:GND", "black", [] ],
    [ "esp:GND.2", "imu1:AD0", "black", [] ],
    [ "esp:3V3", "dht1:VCC", "red", [] ],
    [ "esp:GND.1", "dht1:GND", "black", [] ],
    [ "esp:13", "dht1:SDA", "green", [] ],
    [ "esp:17", "bz1:2", "green", [] ],
    [ "esp:GND.1", "bz1:1", "black", [] ],
    [ "esp:4", "btn1:1.r", "green", [] ],
    [ "esp:GND.1", "btn1:2.r", "black", [] ],
    [ "esp:16", "r1:1", "green", [] ],
    [ "r1:2", "led1:A", "green", [] ],
    [ "led1:C", "esp:GND.1", "black", [] ],
    [ "esp:3V3", "pot1:VCC", "red", [] ],
    [ "esp:GND.1", "pot1:GND", "black", [] ],
    [ "esp:34", "pot1:SIG", "blue", [] ]
  ],
  "dependencies": {}
}
```
