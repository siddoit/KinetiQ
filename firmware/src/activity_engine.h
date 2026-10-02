/*
 * KINETIQ firmware - activity and exertion classifier interface.
 *
 * Sliding 1 s window of accelerometer magnitudes. The infer path runs
 * at the ~100 Hz IMU rate, so the 128-sample ring (power of two for
 * cheap masking) covers at least one second of IMU-rate samples.
 *
 * Unit convention: input arrives in m/s^2 and is normalized to g
 * inside (mag_g = |a| / 9.81); variance is computed on the g-normalized
 * magnitude, so thresholds are in g^2: variance < 0.05 -> REST,
 * 0.05 <= variance < 0.35 -> WALK, variance >= 0.35 -> RUN.
 *
 * Intensity maps sqrt(variance) through a 1.0 full-scale with clamping,
 * giving REST < 0.23, WALK 0.23-0.59, RUN > 0.59: distinct bands that
 * keep the exertion LOW-band gate (intensity < 0.35) inside WALK.
 *
 * IMU-vs-PPG discriminator: main.c memsets every packet, leaving all
 * accel/gyro fields zeroed on PPG packets, so any nonzero accel/gyro
 * component marks a packet as carrying accelerometer data.
 */
#ifndef ACTIVITY_ENGINE_H
#define ACTIVITY_ENGINE_H

#include <stdint.h>
#include <stdbool.h>

#include "sensor_packet.h"

#define ACTIVITY_WINDOW_LEN 128

#define ACTIVITY_REST_VAR_MAX 0.05f
#define ACTIVITY_WALK_VAR_MAX 0.35f

#define ACTIVITY_INTENSITY_FS 1.0f
#define ACTIVITY_LOW_BAND_MAX 0.35f

#define HR_REST_MAX 80
#define HR_LIGHT_MAX 100
#define HR_MODERATE_MAX 130

typedef struct {
	float window[ACTIVITY_WINDOW_LEN];
	uint32_t head;
	uint32_t count;
	double sum;
	double sumsq;
	uint16_t last_hr;
	float intensity_ema;
} activity_engine_t;

void activity_engine_init(activity_engine_t *eng);
void activity_engine_reset(activity_engine_t *eng);
const char *activity_engine_update(activity_engine_t *eng, const sensor_packet_t *pkt);
float activity_engine_variance(const activity_engine_t *eng);
float activity_engine_motion_intensity(const activity_engine_t *eng);
const char *activity_engine_exertion(const activity_engine_t *eng, uint16_t heart_rate,
				     const char *activity);

#endif /* ACTIVITY_ENGINE_H */
