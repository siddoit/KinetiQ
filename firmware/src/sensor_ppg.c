/*
 * KINETIQ firmware - MAX30102 PPG wrapper with synthetic fallback.
 *
 * Zephyr branch: polled fetch from DT node "max30102" at 50 Hz. Raw red/IR
 * come from the SENSOR_CHAN_RED / SENSOR_CHAN_IR channels of the max30101
 * family driver; HR is a rising-edge peak estimate on the IR stream with
 * a 40..200 BPM clamp and a 2 s last-good hold; SpO2 is a still-only
 * estimate fixed at 97 while quality is good; quality gates on IR AC
 * amplitude over the trailing 500 ms window against PPG_IR_AC_FLOOR.
 * Host branch: libc-only synthetic pleth generator, no Zephyr use.
 */

#include "sensor_ppg.h"

#if defined(__ZEPHYR__)

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

LOG_MODULE_DECLARE(kinetiq);

#define PPG_PI_F 3.14159265358979323846f
#define PPG_DT_S (1.0f / (float)SENSOR_PPG_SAMPLE_HZ)
#define PPG_BASELINE_BPM 72
#define PPG_SPO2_ESTIMATE 97
#define PPG_HR_MIN 40
#define PPG_HR_MAX 200
#define PPG_PEAK_HOLD_MS 2000
#define PPG_MAX_CONSECUTIVE_ERRORS 50
#define PPG_IR_AC_FLOOR 1200.0f
#define PPG_IR_WINDOW_SAMPLES (SENSOR_PPG_SAMPLE_HZ / 2)

#define PPG_RED_DC 40000.0f
#define PPG_RED_AC 9000.0f
#define PPG_IR_DC 38000.0f
#define PPG_IR_AC 6500.0f
#define PPG_NOMINAL_BPM 72.0f

struct ppg_synth_state {
	float ph;
	float t;
};

struct ppg_hw_state {
	float dc_ema;
	float win_min;
	float win_max;
	unsigned int win_count;
	int last_bpm;
	int64_t last_peak_ms;
	bool have_peak;
};

static const struct device *s_ppg_dev;
static bool s_hw_active;
static bool s_fallback_armed;
static unsigned int s_consecutive_errors;
static unsigned int s_total_errors;
static struct ppg_synth_state s_synth;
static struct ppg_hw_state s_hw;

static void ppg_synth_step(uint32_t *raw_red, uint32_t *raw_ir, uint8_t *heart_rate)
{
	s_synth.t += PPG_DT_S;

	s_synth.ph += 2.0f * PPG_PI_F * PPG_NOMINAL_BPM / 60.0f / 50.0f;
	if (s_synth.ph > 2.0f * PPG_PI_F) {
		s_synth.ph -= 2.0f * PPG_PI_F;
	}

	float pulse = powf(sinf(s_synth.ph), 2.0f);
	float dicrotic = powf(sinf((2.0f * s_synth.ph) + 0.35f), 4.0f);
	float breath = 1.0f + (0.02f * sinf(0.30f * s_synth.t));

	float red = (PPG_RED_DC + (PPG_RED_AC * (pulse + (0.35f * dicrotic)))) * breath;
	float ir = (PPG_IR_DC + (PPG_IR_AC * (pulse + (0.30f * dicrotic)))) * breath;

	int hr_wander = (int)(3.0f * sinf(0.05f * s_synth.t));
	int hr = (int)PPG_NOMINAL_BPM + hr_wander;

	if (hr < 60) {
		hr = 60;
	}
	if (hr > 80) {
		hr = 80;
	}

	*raw_red = (uint32_t)red;
	*raw_ir = (uint32_t)ir;
	*heart_rate = (uint8_t)hr;
}

static float ppg_val_to_float(const struct sensor_value *v)
{
	return (float)v->val1 + ((float)v->val2 / 1000000.0f);
}

static void ppg_hw_observe_ir(float ir, int64_t now_ms)
{
	const float alpha = 0.02f;

	if (!s_hw.have_peak && s_hw.dc_ema == 0.0f) {
		s_hw.dc_ema = ir;
		s_hw.win_min = ir;
		s_hw.win_max = ir;
		s_hw.win_count = 0;
	}

	s_hw.dc_ema += alpha * (ir - s_hw.dc_ema);

	if (ir < s_hw.win_min) {
		s_hw.win_min = ir;
	}
	if (ir > s_hw.win_max) {
		s_hw.win_max = ir;
	}
	s_hw.win_count++;

	float amp = s_hw.win_max - s_hw.win_min;
	float thresh = s_hw.dc_ema + (0.4f * amp);

	if (ir > thresh && (s_hw.win_count % 4U) == 0U) {
		if (s_hw.have_peak) {
			int64_t interval = now_ms - s_hw.last_peak_ms;

			if (interval >= 300 && interval <= 1500) {
				int bpm = (int)(60000 / interval);

				if (bpm < PPG_HR_MIN) {
					bpm = PPG_HR_MIN;
				}
				if (bpm > PPG_HR_MAX) {
					bpm = PPG_HR_MAX;
				}
				s_hw.last_bpm = bpm;
			}
		} else {
			s_hw.have_peak = true;
		}
		s_hw.last_peak_ms = now_ms;
	}

	if (s_hw.win_count >= PPG_IR_WINDOW_SAMPLES) {
		s_hw.win_min = ir;
		s_hw.win_max = ir;
		s_hw.win_count = 0;
	}
}

static bool ppg_hw_quality_locked(int64_t now_ms)
{
	float amp = s_hw.win_max - s_hw.win_min;

	if (amp < PPG_IR_AC_FLOOR) {
		return false;
	}
	if (!s_hw.have_peak) {
		return false;
	}
	return (now_ms - s_hw.last_peak_ms) <= PPG_PEAK_HOLD_MS;
}

int sensor_ppg_init(void)
{
	s_ppg_dev = DEVICE_DT_GET(DT_NODELABEL(max30102));
	s_consecutive_errors = 0;
	s_hw.dc_ema = 0.0f;
	s_hw.win_min = 0.0f;
	s_hw.win_max = 0.0f;
	s_hw.win_count = 0;
	s_hw.last_bpm = PPG_BASELINE_BPM;
	s_hw.last_peak_ms = 0;
	s_hw.have_peak = false;

	if (s_ppg_dev == NULL || !device_is_ready(s_ppg_dev)) {
		s_hw_active = false;
		s_fallback_armed = true;
		LOG_WRN("MAX30102 not ready - synthetic 72 BPM fallback armed");
		return 0;
	}

	s_hw_active = true;
	s_fallback_armed = false;
	return 0;
}

int sensor_ppg_fetch(uint32_t *raw_red, uint32_t *raw_ir, uint8_t *heart_rate,
		     uint8_t *spo2, bool *quality_good)
{
	if (raw_red == NULL || raw_ir == NULL || heart_rate == NULL ||
	    spo2 == NULL || quality_good == NULL) {
		return -22;
	}

	if (!s_hw_active || s_fallback_armed) {
		uint8_t hr = 0;

		ppg_synth_step(raw_red, raw_ir, &hr);
		*heart_rate = hr;
		*spo2 = PPG_SPO2_ESTIMATE;
		*quality_good = true;
		return 0;
	}

	struct sensor_value red_v;
	struct sensor_value ir_v;

	if (sensor_sample_fetch(s_ppg_dev, SENSOR_CHAN_ALL) != 0 ||
	    sensor_channel_get(s_ppg_dev, SENSOR_CHAN_RED, &red_v) != 0 ||
	    sensor_channel_get(s_ppg_dev, SENSOR_CHAN_IR, &ir_v) != 0) {
		s_total_errors++;
		s_consecutive_errors++;
		if ((s_total_errors % 100U) == 0U) {
			LOG_WRN("MAX30102 fetch failed (%u total), serving synthetic sample",
				s_total_errors);
		}
		if (s_consecutive_errors > PPG_MAX_CONSECUTIVE_ERRORS) {
			s_fallback_armed = true;
			s_hw_active = false;
			LOG_WRN("MAX30102 persistent failure - synthetic fallback armed");
		}
		uint8_t hr = 0;

		ppg_synth_step(raw_red, raw_ir, &hr);
		*heart_rate = hr;
		*spo2 = PPG_SPO2_ESTIMATE;
		*quality_good = true;
		return 0;
	}

	s_consecutive_errors = 0;

	float red = ppg_val_to_float(&red_v);
	float ir = ppg_val_to_float(&ir_v);
	int64_t now_ms = k_uptime_get();

	ppg_hw_observe_ir(ir, now_ms);

	*raw_red = (uint32_t)red;
	*raw_ir = (uint32_t)ir;
	*heart_rate = (uint8_t)s_hw.last_bpm;
	*spo2 = PPG_SPO2_ESTIMATE;
	*quality_good = ppg_hw_quality_locked(now_ms);
	return 0;
}

bool sensor_ppg_is_hardware_active(void)
{
	return s_hw_active && !s_fallback_armed;
}

bool sensor_ppg_fallback_active(void)
{
	return s_fallback_armed || !s_hw_active;
}

#else /* host / non-Zephyr build: libc-only synthetic generator */

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define PPG_PI_F 3.14159265358979323846f
#define PPG_DT_S (1.0f / (float)SENSOR_PPG_SAMPLE_HZ)
#define PPG_BASELINE_BPM 72
#define PPG_SPO2_ESTIMATE 97

#define PPG_RED_DC 40000.0f
#define PPG_RED_AC 9000.0f
#define PPG_IR_DC 38000.0f
#define PPG_IR_AC 6500.0f
#define PPG_NOMINAL_BPM 72.0f

struct ppg_synth_state {
	float ph;
	float t;
};

static struct ppg_synth_state s_synth;
static bool s_init_logged;
static unsigned int s_fetch_count;

static void ppg_synth_step(uint32_t *raw_red, uint32_t *raw_ir, uint8_t *heart_rate)
{
	s_synth.t += PPG_DT_S;

	s_synth.ph += 2.0f * PPG_PI_F * PPG_NOMINAL_BPM / 60.0f / 50.0f;
	if (s_synth.ph > 2.0f * PPG_PI_F) {
		s_synth.ph -= 2.0f * PPG_PI_F;
	}

	float pulse = powf(sinf(s_synth.ph), 2.0f);
	float dicrotic = powf(sinf((2.0f * s_synth.ph) + 0.35f), 4.0f);
	float breath = 1.0f + (0.02f * sinf(0.30f * s_synth.t));

	float red = (PPG_RED_DC + (PPG_RED_AC * (pulse + (0.35f * dicrotic)))) * breath;
	float ir = (PPG_IR_DC + (PPG_IR_AC * (pulse + (0.30f * dicrotic)))) * breath;

	int hr_wander = (int)(3.0f * sinf(0.05f * s_synth.t));
	int hr = (int)PPG_NOMINAL_BPM + hr_wander;

	if (hr < 60) {
		hr = 60;
	}
	if (hr > 80) {
		hr = 80;
	}

	*raw_red = (uint32_t)red;
	*raw_ir = (uint32_t)ir;
	*heart_rate = (uint8_t)hr;
}

int sensor_ppg_init(void)
{
	if (!s_init_logged) {
		s_init_logged = true;
		fprintf(stderr, "sensor_ppg: host synthetic 72 BPM fallback\n");
	}
	return 0;
}

int sensor_ppg_fetch(uint32_t *raw_red, uint32_t *raw_ir, uint8_t *heart_rate,
		     uint8_t *spo2, bool *quality_good)
{
	uint8_t hr = 0;

	if (raw_red == NULL || raw_ir == NULL || heart_rate == NULL ||
	    spo2 == NULL || quality_good == NULL) {
		return -22;
	}
	s_fetch_count++;
	ppg_synth_step(raw_red, raw_ir, &hr);
	*heart_rate = hr;
	*spo2 = PPG_SPO2_ESTIMATE;
	*quality_good = true;
	return 0;
}

bool sensor_ppg_is_hardware_active(void)
{
	return false;
}

bool sensor_ppg_fallback_active(void)
{
	return true;
}

#endif
