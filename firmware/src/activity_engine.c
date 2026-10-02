/*
 * KINETIQ firmware - activity_engine ring-buffer variance classifier.
 *
 * O(1) variance via running sum / sum-of-squares; the sums use double
 * precision and the result is floored at zero to absorb float
 * cancellation. Intensity is EMA-smoothed (0.9 / 0.1) like the legacy
 * energy path it replaces. No RTOS dependencies.
 */
#include "activity_engine.h"

#include <math.h>
#include <stddef.h>
#include <string.h>

#define ACTIVITY_G_NORM 9.81f
#define ACTIVITY_WINDOW_MASK (ACTIVITY_WINDOW_LEN - 1)

static bool packet_has_accel(const sensor_packet_t *pkt)
{
	return (pkt->accel_x != 0.0f) || (pkt->accel_y != 0.0f) ||
	       (pkt->accel_z != 0.0f) || (pkt->gyro_x != 0.0f) ||
	       (pkt->gyro_y != 0.0f) || (pkt->gyro_z != 0.0f);
}

void activity_engine_init(activity_engine_t *eng)
{
	activity_engine_reset(eng);
}

void activity_engine_reset(activity_engine_t *eng)
{
	uint32_t i;

	if (eng == NULL) {
		return;
	}

	for (i = 0U; i < (uint32_t)ACTIVITY_WINDOW_LEN; i++) {
		eng->window[i] = 0.0f;
	}

	eng->head = 0U;
	eng->count = 0U;
	eng->sum = 0.0;
	eng->sumsq = 0.0;
	eng->last_hr = 0U;
	eng->intensity_ema = 0.0f;
}

float activity_engine_variance(const activity_engine_t *eng)
{
	double mean;
	double var;

	if ((eng == NULL) || (eng->count < 2U)) {
		return 0.0f;
	}

	mean = eng->sum / (double)eng->count;
	var = (eng->sumsq / (double)eng->count) - (mean * mean);

	if (var < 0.0) {
		var = 0.0;
	}

	return (float)var;
}

const char *activity_engine_update(activity_engine_t *eng, const sensor_packet_t *pkt)
{
	float mag;
	float raw;
	float var;

	if ((eng == NULL) || (pkt == NULL)) {
		return "REST";
	}

	if ((pkt->ppg_ir != 0U) && (pkt->heart_rate != 0U)) {
		eng->last_hr = pkt->heart_rate;
	}

	if (packet_has_accel(pkt)) {
		double dmag;

		mag = sqrtf((pkt->accel_x * pkt->accel_x) +
			    (pkt->accel_y * pkt->accel_y) +
			    (pkt->accel_z * pkt->accel_z)) / ACTIVITY_G_NORM;
		dmag = (double)mag;

		if (eng->count >= (uint32_t)ACTIVITY_WINDOW_LEN) {
			double evicted = (double)eng->window[eng->head];

			eng->sum -= evicted;
			eng->sumsq -= evicted * evicted;
		} else {
			eng->count++;
		}

		eng->window[eng->head] = mag;
		eng->head = (eng->head + 1U) & ACTIVITY_WINDOW_MASK;
		eng->sum += dmag;
		eng->sumsq += dmag * dmag;

		raw = sqrtf(activity_engine_variance(eng)) / ACTIVITY_INTENSITY_FS;
		if (raw < 0.0f) {
			raw = 0.0f;
		}
		if (raw > 1.0f) {
			raw = 1.0f;
		}
		eng->intensity_ema = (0.9f * eng->intensity_ema) + (0.1f * raw);
	}

	var = activity_engine_variance(eng);

	if (var < ACTIVITY_REST_VAR_MAX) {
		return "REST";
	}
	if (var < ACTIVITY_WALK_VAR_MAX) {
		return "WALK";
	}

	return "RUN";
}

float activity_engine_motion_intensity(const activity_engine_t *eng)
{
	float out;

	if (eng == NULL) {
		return 0.0f;
	}

	out = eng->intensity_ema;
	if (out < 0.0f) {
		out = 0.0f;
	}
	if (out > 1.0f) {
		out = 1.0f;
	}

	return out;
}

const char *activity_engine_exertion(const activity_engine_t *eng, uint16_t heart_rate,
				     const char *activity)
{
	float intensity = 0.0f;

	if (eng != NULL) {
		intensity = eng->intensity_ema;
	}

	if ((activity != NULL) && (strcmp(activity, "REST") == 0) &&
	    (heart_rate < (uint16_t)HR_REST_MAX)) {
		return "REST";
	}
	if ((intensity < ACTIVITY_LOW_BAND_MAX) && (heart_rate < (uint16_t)HR_LIGHT_MAX)) {
		return "LIGHT";
	}
	if (heart_rate < (uint16_t)HR_MODERATE_MAX) {
		return "MODERATE";
	}

	return "HIGH";
}
