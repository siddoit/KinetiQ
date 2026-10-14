// KINETIQ — Wokwi simulation firmware (Arduino-ESP32 core, real FreeRTOS).
// File: hardware/sim/sketch.ino
//
// Pin plan: docs/OVERHAUL_PLAN.md section 3 (locked).
//   I2C SDA=21/SCL=22 (MPU6050 @ 0x68) | DHT22=GPIO13 | buzzer=GPIO17
//   button=GPIO4 (INPUT_PULLUP) | status LED=GPIO16
//
// Tasks: sensorTask (1 Hz) -> classifyTask -> telemetryTask (1 Hz JSON on
// Serial) + buttonTask (debounce, alert toggle + beep) + ledTask (heartbeat).
// Shared state is guarded by a mutex; pacing is vTaskDelay(), never delay().

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
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}

// ---------- (b) classification task: crude activity from accel magnitude ----------
static void classifyTask(void *arg) {
  (void)arg;
  uint32_t lastStepMs = 0;
  for (;;) {
    if (xSemaphoreTake(g_mutex, portMAX_DELAY) == pdTRUE) {
      float dev = fabsf(g_state.mag_g - 1.0f);  // deviation from 1g at rest
      g_state.intensity = constrain(dev / 0.6f, 0.0f, 1.0f);
      const char *act = "rest";
      const char *exe = "rest";
      if (dev >= 0.45f)      { act = "run";  exe = "high"; }
      else if (dev >= 0.15f) { act = "walk"; exe = "moderate"; }
      else if (dev >= 0.05f) { act = "walk"; exe = "light"; }
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
      xSemaphoreGive(g_mutex);
    }
    vTaskDelay(pdMS_TO_TICKS(250));  // classify faster than the 1 Hz sensors
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
    float t = (float)millis() / 1000.0f;
    int hr = (int)(72.0f + 4.0f * sinf(2.0f * 3.14159f * t / 60.0f)
                   + (float)random(-15, 16) / 10.0f);
    hr = constrain(hr, 30, 220);
    int spo2 = (int)(97.0f + 1.0f * sinf(2.0f * 3.14159f * t / 90.0f + 1.0f)
                     + (float)random(-5, 6) / 10.0f);
    spo2 = constrain(spo2, 70, 100);
    uint32_t ts = SIM_EPOCH_BASE + (uint32_t)(millis() / 1000UL);
    snprintf(line, sizeof(line),
             "{\"timestamp\":%lu,\"heart_rate\":%d,\"spo2\":%d,"
             "\"activity\":\"%s\",\"motion_intensity\":%.2f,"
             "\"exertion\":\"%s\",\"steps\":%lu,\"ppg_quality\":\"good\"}",
             (unsigned long)ts, hr, spo2,
             s.activity, (double)s.intensity,
             s.exertion, (unsigned long)s.steps);
    Serial.println(line);
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}

// ---------- (d) button task: debounce, toggle alert + buzzer beep ----------
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
          xSemaphoreGive(g_mutex);
        }
        // tone() drives the Wokwi piezo buzzer (smooth mode); two-tone ack.
        tone(PIN_BUZZER, on ? 880 : 440, 150);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// ---------- (e) status LED heartbeat: slow blink, fast blink while alerting ----------
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
    vTaskDelay(pdMS_TO_TICKS(on ? 100 : 500));
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
  randomSeed(analogRead(34));  // floating-pin noise seed only; no sensor analogReads
  g_mutex = xSemaphoreCreateMutex();
  xTaskCreate(sensorTask, "sensor", 4096, NULL, 2, NULL);
  xTaskCreate(classifyTask, "classify", 2048, NULL, 1, NULL);
  xTaskCreate(telemetryTask, "telemetry", 4096, NULL, 1, NULL);
  xTaskCreate(buttonTask, "button", 2048, NULL, 2, NULL);
  xTaskCreate(ledTask, "led", 2048, NULL, 1, NULL);
}

void loop() {
  vTaskDelay(portMAX_DELAY);  // all work lives in FreeRTOS tasks
}
