/*
 * KINETIQ firmware - shared sensor packet definition.
 *
 * Single source of truth for sensor_packet_t, included by both the
 * application (main.c) and the activity engine. Field layout is fixed
 * by the infer/comm telemetry path and must not change.
 */
#ifndef SENSOR_PACKET_H
#define SENSOR_PACKET_H

#include <stdint.h>

typedef struct {
	int64_t timestamp_ms;
	float accel_x, accel_y, accel_z;
	float gyro_x, gyro_y, gyro_z;
	uint16_t ppg_red;
	uint32_t ppg_ir;
	uint16_t heart_rate;
	uint32_t steps;
	uint8_t quality;   /* 0 = POOR, 1 = GOOD */
	const char *activity;   /* REST / WALK / RUN */
	const char *exertion;   /* REST / LIGHT / MODERATE / HIGH */
} sensor_packet_t;

#endif /* SENSOR_PACKET_H */
