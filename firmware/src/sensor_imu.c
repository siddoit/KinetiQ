/*
 * KINETIQ firmware - BMI270 IMU wrapper with synthetic fallback.
 *
 * Zephyr branch: polled fetch from DT node "bmi270" at 100 Hz. The Zephyr
 * BMI270 driver reports accel channels in m/s^2 and gyro channels in
 * rad/s, so conversion is val1 + val2/1e6 with no extra scaling.
 * Host branch: libc-only synthetic generator, no Zephyr dependencies.
 */

#include "sensor_imu.h"

#if defined(__ZEPHYR__)

#include <math.h>
#include <stddef.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/logging/log.h>

LOG_MODULE_DECLARE(kinetiq);

#define IMU_PI_F 3.14159265358979323846f
#define IMU_DT_S (1.0f / (float)SENSOR_IMU_SAMPLE_HZ)
#define IMU_MAX_CONSECUTIVE_ERRORS 50

struct imu synth_state {
	float t;
	float phase;
	float cadence;
};

static const struct device *s_imu_dev;
static bool s_hw_active;
static bool s_fallback_armed;
static unsigned int s_consecutive_errors;
static unsigned int s_total_errors;
static struct imu synth_state s_synth;

static void imu_synth_step(float accel_m_s2[3], float gyro_rad_s[3])
{
	s_synth.t += IMU_DT_S;

	s_synth.cadence += IMU_DT_S / 60.0f;
	if (s_synth.cadence >= 1.0f) {
		s_synth.cadence -= 1.0f;
	}
	float ramp = (4.0f * fabsf(s_synth.cadence - 0.5f)) - 1.0f;
	float effort = 0.5f + (0.5f * ramp);
	float freq = 0.8f + (1.8f * effort);
	float accel_amp = 0.5f + (3.0f * effort);
	float gyro_amp = 0.5f + (6.0f * effort);

	s_synth.phase += 2.0f * IMU_PI_F * freq * IMU_DT_S;
	if (s_synth.phase > 2.0f * IMU_PI_F) {
		s_synth.phase -= 2.0f * IMU_PI_F;
	}

	accel_m_s2[0] = 0.20f * accel_amp * sinf(s_synth.phase * 0.5f);
	accel_m_s2[1] = 0.20f * accel_amp * cosf(s_synth.phase * 0.5f);
	accel_m_s2[2] = 9.81f + (accel_amp * sinf(s_synth.phase));
	gyro_rad_s[0] = gyro_amp * cosf(s_synth.phase);
	gyro_rad_s[1] = 0.35f * gyro_amp * sinf(s_synth.phase);
	gyro_rad_s[2] = 0.15f * gyro_amp * sinf(s_synth.phase + 0.6f);
}

static float imu_val_to_float(const struct sensor_value *v)
{
	return (float)v->val1 + ((float)v->val2 / 1000000.0f);
}

int sensor_imu_init(void)
{
	s_imu_dev = DEVICE_DT_GET(DT_NODELABEL(bmi270));
	s_consecutive_errors = 0;

	if (s_imu_dev == NULL || !device_is_ready(s_imu_dev)) {
		s_hw_active = false;
		s_fallback_armed = true;
		LOG_WRN("BMI270 not ready - synthetic 100 Hz fallback armed");
		return 0;
	}

	s_hw_active = true;
	s_fallback_armed = false;
	return 0;
}

int sensor_imu_fetch(float accel_m_s2[3], float gyro_rad_s[3])
{
	if (accel_m_s2 == NULL || gyro_rad_s == NULL) {
		return -22;
	}

	if (!s_hw_active || s_fallback_armed) {
		imu_synth_step(accel_m_s2, gyro_rad_s);
		return 0;
	}

	struct sensor_value accel[3];
	struct sensor_value gyro[3];

	if (sensor_sample_fetch(s_imu_dev) != 0 ||
	    sensor_channel_get(s_imu_dev, SENSOR_CHAN_ACCEL_XYZ, accel) != 0 ||
	    sensor_channel_get(s_imu_dev, SENSOR_CHAN_GYRO_XYZ, gyro) != 0) {
		s_total_errors++;
		s_consecutive_errors++;
		if ((s_total_errors % 100U) == 0U) {
			LOG_WRN("BMI270 fetch failed (%u total), serving synthetic sample",
				s_total_errors);
		}
		if (s_consecutive_errors > IMU_MAX_CONSECUTIVE_ERRORS) {
			s_fallback_armed = true;
			s_hw_active = false;
			LOG_WRN("BMI270 persistent failure - synthetic fallback armed");
		}
		imu_synth_step(accel_m_s2, gyro_rad_s);
		return 0;
	}

	s_consecutive_errors = 0;
	accel_m_s2[0] = imu_val_to_float(&accel[0]);
	accel_m_s2[1] = imu_val_to_float(&accel[1]);
	accel_m_s2[2] = imu_val_to_float(&accel[2]);
	gyro_rad_s[0] = imu_val_to_float(&gyro[0]);
	gyro_rad_s[1] = imu_val_to_float(&gyro[1]);
	gyro_rad_s[2] = imu_val_to_float(&gyro[2]);
	return 0;
}

bool sensor_imu_is_hardware_active(void)
{
	return s_hw_active && !s_fallback_armed;
}

bool sensor_imu_fallback_active(void)
{
	return s_fallback_armed || !s_hw_active;
}

#else /* host / non-Zephyr build: libc-only synthetic generator */

#include <math.h>
#include <stddef.h>
#include <stdio.h>

#define IMU_PI_F 3.14159265358979323846f
#define IMU_DT_S (1.0f / (float)SENSOR_IMU_SAMPLE_HZ)

struct imu synth_state {
	float t;
	float phase;
	float cadence;
};

static struct imu synth_state s_synth;
static bool s_init_logged;
static unsigned int s_fetch_count;

static void imu_synth_step(float accel_m_s2[3], float gyro_rad_s[3])
{
	s_synth.t += IMU_DT_S;

	s_synth.cadence += IMU_DT_S / 60.0f;
	if (s_synth.cadence >= 1.0f) {
		s_synth.cadence -= 1.0f;
	}
	float ramp = (4.0f * fabsf(s_synth.cadence - 0.5f)) - 1.0f;
	float effort = 0.5f + (0.5f * ramp);
	float freq = 0.8f + (1.8f * effort);
	float accel_amp = 0.5f + (3.0f * effort);
	float gyro_amp = 0.5f + (6.0f * effort);

	s_synth.phase += 2.0f * IMU_PI_F * freq * IMU_DT_S;
	if (s_synth.phase > 2.0f * IMU_PI_F) {
		s_synth.phase -= 2.0f * IMU_PI_F;
	}

	accel_m_s2[0] = 0.20f * accel_amp * sinf(s_synth.phase * 0.5f);
	accel_m_s2[1] = 0.20f * accel_amp * cosf(s_synth.phase * 0.5f);
	accel_m_s2[2] = 9.81f + (accel_amp * sinf(s_synth.phase));
	gyro_rad_s[0] = gyro_amp * cosf(s_synth.phase);
	gyro_rad_s[1] = 0.35f * gyro_amp * sinf(s_synth.phase);
	gyro_rad_s[2] = 0.15f * gyro_amp * sinf(s_synth.phase + 0.6f);
}

int sensor_imu_init(void)
{
	if (!s_init_logged) {
		s_init_logged = true;
		fprintf(stderr, "sensor_imu: host synthetic 100 Hz fallback\n");
	}
	return 0;
}

int sensor_imu_fetch(float accel_m_s2[3], float gyro_rad_s[3])
{
	if (accel_m_s2 == NULL || gyro_rad_s == NULL) {
		return -22;
	}
	s_fetch_count++;
	imu_synth_step(accel_m_s2, gyro_rad_s);
	return 0;
}

bool sensor_imu_is_hardware_active(void)
{
	return false;
}

bool sensor_imu_fallback_active(void)
{
	return true;
}

#endif
