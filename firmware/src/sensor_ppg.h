/*
 * KINETIQ firmware - PPG sensor wrapper (MAX30102 on I2C0, dual-mode).
 *
 * Hardware mode: Maxim MAX30102 via the Zephyr sensor API (polled fetch
 * at 50 Hz, DT node label "max30102"). HR is a lightweight IR peak
 * estimate, SpO2 a still-only estimate; quality gates on IR amplitude.
 * Fallback/host mode: 72 BPM resting baseline, SpO2 97, quality GOOD,
 * raw counts from the byte-equivalent main.c pleth generator, libc-only.
 */

#ifndef SENSOR_PPG_H
#define SENSOR_PPG_H

#include <stdbool.h>
#include <stdint.h>

#define SENSOR_PPG_SAMPLE_HZ 50

int sensor_ppg_init(void);
int sensor_ppg_fetch(uint32_t *raw_red, uint32_t *raw_ir, uint8_t *heart_rate,
		     uint8_t *spo2, bool *quality_good);
bool sensor_ppg_is_hardware_active(void);
bool sensor_ppg_fallback_active(void);

#endif /* SENSOR_PPG_H */
