// KINETIQ — Wokwi simulation firmware (Arduino-ESP32 core, real FreeRTOS).
// File: hardware/sim/sketch.ino
//
// Pin plan: docs/OVERHAUL_PLAN.md section 3 (locked).
//   I2C SDA=21/SCL=22 (MPU6050 @ 0x68) | DHT22=GPIO13 | buzzer=GPIO17
//   button=GPIO4 (INPUT_PULLUP) | status LED=GPIO16 | examiner trimpot=GPIO34
//
// Tasks: sensorTask (1 Hz) -> classifyTask -> telemetryTask (1 Hz JSON on
// Serial) + buttonTask (debounce, alert toggle + fall-event beep)
// + potTask (100 ms examiner trimpot) + ledTask (heartbeat).
// Shared state is guarded by a mutex; pacing is vTaskDelay(), never delay().
// Serial carries exactly ONE JSON telemetry line per second (schema-locked)
// plus one plain-text "RTOS ..." status line (never JSON — parsers that keep
// only lines starting with '{' are unaffected).

#include <Arduino.h>
#include <Wire.h>
#include <DHTesp.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

// ---------- pins ----------
static const uint8_t PIN_I2C_SDA = 21;
static const uint8_t PIN_I2C_SCL = 22;
static const uint8_t PIN_DHT     = 13;
static const uint8_t PIN_BUZZER  = 17;
static const uint8_t PIN_BUTTON  = 4;    // INPUT_PULLUP, active LOW
static const uint8_t PIN_LED     = 16;
static const uint8_t PIN_TRIMPOT = 34;   // ADC1_CH6, input-only; examiner knob

// ================= RTOS CONFIG (exhibit block — single source of truth) =====
// Task priorities (larger = more urgent), pacing periods (ms), stack depths
// (xTaskCreate usStackDepth units) and core affinity. Tasks stay UNPINNED
// (tskNO_AFFINITY) so the Wokwi browser build schedules exactly like the
// classroom build. Every xTaskCreate / vTaskDelay below sources its numbers
// from these constants — no magic numbers in the task bodies.
#define CFG_PRIO_SENSOR    2
#define CFG_PRIO_CLASSIFY  1
#define CFG_PRIO_TELEMETRY 1
#define CFG_PRIO_BUTTON    2
#define CFG_PRIO_POT       1
#define CFG_PRIO_LED       1
#define CFG_PERIOD_SENSOR_MS    1000
#define CFG_PERIOD_CLASSIFY_MS   250
#define CFG_PERIOD_TELEMETRY_MS 1000
#define CFG_PERIOD_BUTTON_MS      10
#define CFG_PERIOD_POT_MS        100
#define CFG_PERIOD_LED_SLOW_MS   500
#define CFG_PERIOD_LED_FAST_MS   100
#define CFG_STACK_SENSOR     4096
#define CFG_STACK_CLASSIFY   2048
#define CFG_STACK_TELEMETRY  4096
#define CFG_STACK_BUTTON     2048
#define CFG_STACK_POT        2048
#define CFG_STACK_LED        2048
static const BaseType_t CFG_CORE_DEFAULT = tskNO_AFFINITY;  // documented, unpinned

// ---------- examiner trimpot bands (12-bit ADC 0..4095, smoothed) ----------
static const int POT_MID_MIN  = 1366;  // >= : walk band
static const int POT_HIGH_MIN = 2731;  // >= : run band (below both: rest band)

// ---------- fall-event recovery window ----------
static const uint32_t FALL_RECOVERY_MS = 8000;  // ramp length after button press

// ---------- MPU6050 registers (raw Wire access, no library needed) ----------
static const uint8_t MPU_ADDR         = 0x68;  // AD0 tied to GND
static const uint8_t MPU_REG_PWR      = 0x6B;
static const uint8_t MPU_REG_ACCEL_XH = 0x3B;
static const float   MPU_LSB_PER_G    = 16384.0f;  // +/-2g full scale (reset default)

// ---------- shared state ----------
struct SharedState {
  float    ax_g = 0.0f, ay_g = 0.0f, az_g = 1.0f;
  float    mag_g = 1.0f;
  float    tempC = 25.0f, humidPct = 50.0f;
  bool     dhtOk = false;
  char     activity[8] = "rest";     // rest|walk|run (schema subset)
  char     exertion[10] = "rest";    // rest|light|moderate|high
  float    intensity = 0.0f;         // 0.00..1.00
  uint32_t steps = 0;
  bool     alertOn = false;
  uint16_t potRaw = 0;        // last examiner-trimpot ADC sample (0..4095)
  float    potSmooth = 0.0f;   // exponentially smoothed trimpot (band select)
  uint32_t fallUntilMs = 0;    // millis() stamp: fall ramp active while > now
};

static SharedState g_state;
static SemaphoreHandle_t g_mutex = NULL;
static DHTesp g_dht;

// Sim epoch base: no RTC/NTP in the sim, so timestamp = base + uptime(s).
// Schema only requires a positive unix-seconds int.
static const uint32_t SIM_EPOCH_BASE = 1727544000UL;

// ---------- helpers ----------
static bool mpuWake() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(MPU_REG_PWR);
  Wire.write(0x00);  // clear SLEEP bit
  return Wire.endTransmission() == 0;
}

static bool mpuReadAccel(float &ax, float &ay, float &az) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(MPU_REG_ACCEL_XH);
  if (Wire.endTransmission(false) != 0) return false;  // repeated start
  if (Wire.requestFrom(MPU_ADDR, (uint8_t)6) != 6) return false;
  int16_t rx = (int16_t)((Wire.read() << 8) | Wire.read());
  int16_t ry = (int16_t)((Wire.read() << 8) | Wire.read());
  int16_t rz = (int16_t)((Wire.read() << 8) | Wire.read());
  ax = (float)rx / MPU_LSB_PER_G;
  ay = (float)ry / MPU_LSB_PER_G;
  az = (float)rz / MPU_LSB_PER_G;
  return true;
}

// ---------- (a) sensor task: MPU6050 via Wire + DHT22, 1 Hz ----------
static void sensorTask(void *arg) {
  (void)arg;
  for (;;) {
    float ax, ay, az;
    bool mpuOk = mpuReadAccel(ax, ay, az);
    TempAndHumidity th = g_dht.getTempAndHumidity();  // DHTesp (Wokwi-safe on ESP32)
    bool dhtOk = (g_dht.getStatus() == 0);
    if (xSemaphoreTake(g_mutex, portMAX_DELAY) == pdTRUE) {
      if (mpuOk) {
        g_state.ax_g = ax;
        g_state.ay_g = ay;
        g_state.az_g = az;
        g_state.mag_g = sqrtf(ax * ax + ay * ay + az * az);
      }
      if (dhtOk) {
        g_state.tempC = th.temperature;
        g_state.humidPct = th.humidity;
      }
      g_state.dhtOk = dhtOk;
      xSemaphoreGive(g_mutex);
    }
    vTaskDelay(pdMS_TO_TICKS(CFG_PERIOD_SENSOR_MS));
  }
}

// ---------- (b) classification task: examiner trimpot drives the band ----------
// The Wokwi MPU6050 is static, so accel alone would sit at rest forever in the
// browser. The trimpot (smoothed, ~100 ms) selects rest|walk|run; real accel
// can only raise intensity above the band baseline (matters on real hardware).
// Enum strings stay inside the locked schema set (rest|walk|run) — "fall" is
// NOT a schema value, so the button fall event uses rest + high intensity.
static void classifyTask(void *arg) {
  (void)arg;
  uint32_t lastStepMs = 0;
  for (;;) {
    if (xSemaphoreTake(g_mutex, portMAX_DELAY) == pdTRUE) {
      float dev = fabsf(g_state.mag_g - 1.0f);  // deviation from 1g at rest
      float accelIntensity = constrain(dev / 0.6f, 0.0f, 1.0f);
      float pot = g_state.potSmooth;
      const char *act = "rest";
      const char *exe = "rest";
      float base = 0.02f;
      if (pot >= (float)POT_HIGH_MIN)     { act = "run";  exe = "high";     base = 0.75f; }
      else if (pot >= (float)POT_MID_MIN) { act = "walk"; exe = "moderate"; base = 0.35f; }
      g_state.intensity = constrain(fmaxf(accelIntensity, base), 0.0f, 1.0f);
      strncpy(g_state.activity, act, sizeof(g_state.activity) - 1);
      strncpy(g_state.exertion, exe, sizeof(g_state.exertion) - 1);
      g_state.activity[sizeof(g_state.activity) - 1] = 0;
      g_state.exertion[sizeof(g_state.exertion) - 1] = 0;
      // crude step peak detector: one step per accel burst, 300 ms refractory
      uint32_t now = millis();
      if (dev > 0.15f && (now - lastStepMs) > 300) {
        g_state.steps++;
        lastStepMs = now;
      }
      // band-driven steps so walk/run visibly advance the counter in the sim
      uint32_t refractory = 0;
      if (strcmp(act, "run") == 0)       refractory = 250;
      else if (strcmp(act, "walk") == 0) refractory = 400;
      if (refractory > 0 && (now - lastStepMs) > refractory) {
        g_state.steps++;
        lastStepMs = now;
      }
      // FALL override LAST: schema-safe "rest" + intensity spike ~0.95 that
      // decays back to the trimpot baseline over FALL_RECOVERY_MS.
      if (g_state.fallUntilMs > now) {
        strncpy(g_state.activity, "rest", sizeof(g_state.activity) - 1);
        strncpy(g_state.exertion, "high", sizeof(g_state.exertion) - 1);
        g_state.activity[sizeof(g_state.activity) - 1] = 0;
        g_state.exertion[sizeof(g_state.exertion) - 1] = 0;
        float k = constrain((float)(g_state.fallUntilMs - now)
                            / (float)FALL_RECOVERY_MS, 0.0f, 1.0f);
        g_state.intensity = constrain(0.95f * k + base * (1.0f - k), 0.0f, 1.0f);
      }
      xSemaphoreGive(g_mutex);
    }
    vTaskDelay(pdMS_TO_TICKS(CFG_PERIOD_CLASSIFY_MS));  // faster than 1 Hz sensors
  }
}

// ---------- (c) telemetry task: ONE JSON line per second on Serial ----------
// Field names/order match laptop/receiver/telemetry_schema.py EXACTLY.
// HR/SpO2 are synthetic: slow sine + noise.
// synthetic: no PPG sensor in razor BOM (MAX30102 was cut; band bridge = stretch).
static void telemetryTask(void *arg) {
  (void)arg;
  char line[220];
  for (;;) {
    SharedState s;
    if (xSemaphoreTake(g_mutex, portMAX_DELAY) == pdTRUE) {
      s = g_state;
      xSemaphoreGive(g_mutex);
    }
    uint32_t now = millis();
    float t = (float)now / 1000.0f;
    // Synthetic HR baseline follows the SELECTED activity (examiner knob);
    // the fall event adds a spike that decays over FALL_RECOVERY_MS.
    float baseHr = 72.0f;
    if (strcmp(s.activity, "walk") == 0)      baseHr = 96.0f;
    else if (strcmp(s.activity, "run") == 0)  baseHr = 132.0f;
    float fallBoost = 0.0f;
    if (s.fallUntilMs > now) {
      fallBoost = 30.0f * constrain((float)(s.fallUntilMs - now)
                                    / (float)FALL_RECOVERY_MS, 0.0f, 1.0f);
    }
    int hr = (int)(baseHr + fallBoost + 4.0f * sinf(2.0f * 3.14159f * t / 60.0f)
                   + (float)random(-15, 16) / 10.0f);
    hr = constrain(hr, 30, 220);
    int spo2 = (int)(97.0f + 1.0f * sinf(2.0f * 3.14159f * t / 90.0f + 1.0f)
                     + (float)random(-5, 6) / 10.0f);
    spo2 = constrain(spo2, 70, 100);
    uint32_t ts = SIM_EPOCH_BASE + (uint32_t)(now / 1000UL);
    // RTOS exhibit line: plain text, NEVER JSON — printed BEFORE the JSON line
    // so line-oriented parsers (keep only lines starting with '{') skip it.
    Serial.printf("RTOS tick=%lu tasks=%u uptime_s=%lu heap=%u\n",
                  (unsigned long)xTaskGetTickCount(),
                  (unsigned)uxTaskGetNumberOfTasks(),
                  (unsigned long)(now / 1000UL),
                  (unsigned)ESP.getFreeHeap());
    snprintf(line, sizeof(line),
             "{\"timestamp\":%lu,\"heart_rate\":%d,\"spo2\":%d,"
             "\"activity\":\"%s\",\"motion_intensity\":%.2f,"
             "\"exertion\":\"%s\",\"steps\":%lu,\"ppg_quality\":\"good\"}",
             (unsigned long)ts, hr, spo2,
             s.activity, (double)s.intensity,
             s.exertion, (unsigned long)s.steps);
    Serial.println(line);
    vTaskDelay(pdMS_TO_TICKS(CFG_PERIOD_TELEMETRY_MS));
  }
}

// ---------- (d) button task: debounce, alert toggle + fall event ----------
// Press = examiner "fall event": short double-beep, motion_intensity spikes
// ~0.95 with activity forced to schema-safe "rest" ("fall" is NOT a schema
// value), then an 8 s recovery ramp (HR spikes, then decays). Alert toggle
// behaviour is unchanged.
static void buttonTask(void *arg) {
  (void)arg;
  int stable = HIGH;
  int last = HIGH;
  uint32_t lastChange = 0;
  for (;;) {
    int r = digitalRead(PIN_BUTTON);
    uint32_t now = millis();
    if (r != last) {
      last = r;
      lastChange = now;
    }
    if ((now - lastChange) > 50 && r != stable) {  // 50 ms debounce
      stable = r;
      if (stable == LOW) {  // pressed (INPUT_PULLUP)
        bool on;
        if (xSemaphoreTake(g_mutex, portMAX_DELAY) == pdTRUE) {
          g_state.alertOn = !g_state.alertOn;
          on = g_state.alertOn;
          g_state.fallUntilMs = now + FALL_RECOVERY_MS;  // start fall ramp
          xSemaphoreGive(g_mutex);
        }
        // tone() drives the Wokwi piezo buzzer (smooth mode); double-beep ack.
        uint16_t pitch = on ? 880 : 440;
        tone(PIN_BUZZER, pitch, 120);
        vTaskDelay(pdMS_TO_TICKS(150));
        tone(PIN_BUZZER, pitch, 120);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(CFG_PERIOD_BUTTON_MS));
  }
}

// ---------- (e2) examiner trimpot task: 100 ms smoothed ADC ----------
// Exponential smoother (alpha 0.2) kills knob jitter; classifyTask maps the
// smoothed value to rest|walk|run bands. No filtering library needed.
static void potTask(void *arg) {
  (void)arg;
  bool first = true;
  for (;;) {
    int raw = analogRead(PIN_TRIMPOT);  // 0..4095, examiner knob
    if (xSemaphoreTake(g_mutex, portMAX_DELAY) == pdTRUE) {
      g_state.potRaw = (uint16_t)constrain(raw, 0, 4095);
      if (first) { g_state.potSmooth = (float)g_state.potRaw; first = false; }
      else {
        g_state.potSmooth =
            0.2f * (float)g_state.potRaw + 0.8f * g_state.potSmooth;
      }
      xSemaphoreGive(g_mutex);
    }
    vTaskDelay(pdMS_TO_TICKS(CFG_PERIOD_POT_MS));
  }
}

// ---------- (f) status LED heartbeat: slow blink, fast blink while alerting ----------
static void ledTask(void *arg) {
  (void)arg;
  bool level = false;
  for (;;) {
    bool on = false;
    if (xSemaphoreTake(g_mutex, portMAX_DELAY) == pdTRUE) {
      on = g_state.alertOn;
      xSemaphoreGive(g_mutex);
    }
    level = !level;
    digitalWrite(PIN_LED, level ? HIGH : LOW);
    vTaskDelay(pdMS_TO_TICKS(on ? CFG_PERIOD_LED_FAST_MS : CFG_PERIOD_LED_SLOW_MS));
  }
}

void setup() {
  Serial.begin(115200);
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  mpuWake();
  g_dht.setup(PIN_DHT, DHTesp::DHT22);  // "DHT sensor library for ESPx" (Wokwi-safe)
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);
  randomSeed(analogRead(PIN_TRIMPOT));  // knob noise seed only
  g_mutex = xSemaphoreCreateMutex();
  // All priorities/stacks from the RTOS CONFIG block; tasks unpinned
  // (CFG_CORE_DEFAULT = tskNO_AFFINITY) — plain xTaskCreate throughout.
  xTaskCreate(sensorTask, "sensor", CFG_STACK_SENSOR, NULL, CFG_PRIO_SENSOR, NULL);
  xTaskCreate(classifyTask, "classify", CFG_STACK_CLASSIFY, NULL, CFG_PRIO_CLASSIFY, NULL);
  xTaskCreate(telemetryTask, "telemetry", CFG_STACK_TELEMETRY, NULL, CFG_PRIO_TELEMETRY, NULL);
  xTaskCreate(buttonTask, "button", CFG_STACK_BUTTON, NULL, CFG_PRIO_BUTTON, NULL);
  xTaskCreate(potTask, "pot", CFG_STACK_POT, NULL, CFG_PRIO_POT, NULL);
  xTaskCreate(ledTask, "led", CFG_STACK_LED, NULL, CFG_PRIO_LED, NULL);
}

void loop() {
  vTaskDelay(portMAX_DELAY);  // all work lives in FreeRTOS tasks
}
