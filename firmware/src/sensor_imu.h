/*
 * KINETIQ firmware - IMU sensor wrapper (BMI270 on I2C0, dual-mode).
 *
 * Hardware mode: Bosch BMI270 via the Zephyr sensor API (polled fetch at
 * 100 Hz, DT node label "bmi270"). Any init/fetch failure arms the
 * synthetic fallback generator so telemetry never stalls.
 * Fallback/host mode: byte-equivalent port of the main.c synthetic gait
 * waveform (60 s rest-walk-run ramp), libc-only.
 */

#ifndef SENSOR_IMU_H
#define SENSOR_IMU_H

#include <stdbool.h>
#include <stdint.h>

#define SENSOR_IMU_SAMPLE_HZ 100

int sensor_imu_init(void);
int sensor_imu_fetch(float accel_m_s2[3], float gyro_rad_s[3]);
bool sensor_imu_is_hardware_active(void);
bool sensor_imu_fallback_active(void);

#endif /* SENSOR_IMU_H */
