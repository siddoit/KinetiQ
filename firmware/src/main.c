/*
 * KINETIQ firmware - Zephyr application for the ESP32-S3 wearable.
 *
 * Sense     : imu_thread (100 Hz, BMI270) + ppg_thread (50 Hz, MAX30102)
 * Process   : k_msgq sample bus feeding infer_thread
 * Infer     : activity_engine classifier (REST/WALK/RUN) + exertion + PPG quality gate
 * Communicate: comm_thread (1 Hz) emitting the locked telemetry JSON
 *             over BLE NUS notify (newline-framed) with the UART LOG_INF
 *             line kept as the debug fallback when no peer is connected
 * UI        : button_thread (100 Hz) polling button_fsm for PTT events;
 *             display_engine owns the canonical screen state plus the
 *             240x240 round GC9A01 ASCII-grid preview (short-press cycles it,
 *             infer_thread feeds it telemetry, button long-press drives PTT)
 *
 * Sense HAL  : sensor_imu / sensor_ppg dual-mode wrappers: real BMI270 /
 *             MAX30102 hardware when the devicetree nodes probe, otherwise
 *             the synthetic fallback generators (same waveforms as before).
 */

#include <zephyr/kernel.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/printk.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/util.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "sensor_packet.h"
#include "sensor_imu.h"
#include "sensor_ppg.h"
#include "button_fsm.h"
#include "activity_engine.h"
#include "display_engine.h"
#include "ble_transport.h"

LOG_MODULE_REGISTER(kinetiq, LOG_LEVEL_INF);

#define PI_F 3.14159265358979323846f

/* Fixed epoch base so the emitted "timestamp" is a unix-like wall clock value. */
#define KINETIQ_EPOCH_BASE_S 1750000000LL

/* Sample rates (ms). */
#define IMU_PERIOD_MS 10
#define PPG_PERIOD_MS 20
#define COMM_PERIOD_MS 1000

/* Thread priorities - distinct, sensor capture highest, comm lowest. */
#define IMU_PRIO 5
#define PPG_PRIO 6
#define INFER_PRIO 7
#define BUTTON_PRIO 8
#define COMM_PRIO 9

#define THREAD_STACK_SIZE 2048
#define BUTTON_STACK_SIZE 1024
#define BUTTON_PERIOD_MS 10

/* Synthetic PPG waveform operating point (raw MAX30102-ish ADC counts). */
#define PPG_RED_DC 40000.0f
#define PPG_RED_AC 9000.0f
#define PPG_IR_DC 38000.0f
#define PPG_IR_AC 6500.0f
#define PPG_NOMINAL_BPM 72.0f

/* PPG quality gate thresholds (photodiode signal floor). */
#define PPG_RED_FLOOR 39500.0f
#define PPG_IR_FLOOR 37500.0f

/* Step detector refractory window. */
#define STEP_REFRACTORY_MS 250

struct shared_state {
	int64_t timestamp_ms;
	uint16_t heart_rate;
	uint16_t spo2;
	uint32_t steps;
	uint8_t quality;
	char activity[12];
	char exertion[12];
	float motion_intensity;
};

K_MSGQ_DEFINE(sensor_msgq, sizeof(sensor_packet_t), 10, 4);
K_MUTEX_DEFINE(state_mutex);

static struct shared_state current_state = {
	.timestamp_ms = 0,
	.heart_rate = 72,
	.spo2 = 97,
	.steps = 0,
	.quality = 0,
	.activity = "REST",
	.exertion = "REST",
	.motion_intensity = 0.0f,
};

static atomic_t g_steps = ATOMIC_INIT(0);
static atomic_t g_dropped_packets = ATOMIC_INIT(0);
static atomic_t g_processed_packets = ATOMIC_INIT(0);

/* Cadence detector state (imu_thread only, plus read-only snapshots). */
static float g_vert_filtered;
static float g_prev_slope;
static int64_t g_last_step_ms;

static void imu_thread(void *a, void *b, void *c);
static void ppg_thread(void *a, void *b, void *c);
static void infer_thread(void *a, void *b, void *c);
static void comm_thread(void *a, void *b, void *c);
static void button_thread(void *a, void *b, void *c);

K_THREAD_STACK_DEFINE(imu_stack, THREAD_STACK_SIZE);
K_THREAD_STACK_DEFINE(ppg_stack, THREAD_STACK_SIZE);
K_THREAD_STACK_DEFINE(infer_stack, THREAD_STACK_SIZE);
K_THREAD_STACK_DEFINE(comm_stack, THREAD_STACK_SIZE);
K_THREAD_STACK_DEFINE(button_stack, BUTTON_STACK_SIZE);

static struct k_thread imu_tid;
static struct k_thread ppg_tid;
static struct k_thread infer_tid;
static struct k_thread comm_tid;
static struct k_thread button_tid;

static button_fsm_t g_button;
static bool s_ptt_active = false;

static void on_laptop_rx(const uint8_t *data, size_t len)
{
	char excerpt[65];
	size_t shown = len > 64U ? 64U : len;
	size_t i;

	for (i = 0; i < shown; i++) {
		uint8_t ch = data[i];

		excerpt[i] = (ch >= 32U && ch < 127U) ? (char)ch : '.';
	}
	excerpt[shown] = '\0';
	LOG_INF("laptop rx %u byte(s): %s%s (assistant audio/commands landing point, PTT downlink wired in W4)",
		(unsigned int)len, excerpt, len > 64U ? "..." : "");
}

static void queue_packet(sensor_packet_t *pkt)
{
	int rc = k_msgq_put(&sensor_msgq, pkt, K_NO_WAIT);

	if (rc != 0) {
		int dropped = atomic_inc(&g_dropped_packets);

		if ((dropped % 25) == 0) {
			LOG_WRN("sensor_msgq full, dropped %d packet(s) total", dropped);
		}
	}
}

/* Zero-crossing cadence stub: counts one step per upward swing of the
 * vertical acceleration axis with a refractory window and an amplitude gate.
 */
static int cadence_detect(float accel_z, int64_t now_ms)
{
	float slope = accel_z - 9.81f;
	float prev = g_prev_slope;

	g_vert_filtered = (0.85f * g_vert_filtered) + (0.15f * slope);
	g_prev_slope = g_vert_filtered;

	if (g_last_step_ms == 0) {
		g_last_step_ms = now_ms;
		return 0;
	}

	if (prev <= 0.0f && g_vert_filtered > 0.80f &&
	    (now_ms - g_last_step_ms) >= STEP_REFRACTORY_MS) {
		g_last_step_ms = now_ms;
		return 1;
	}

	return 0;
}

static void imu_thread(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	sensor_packet_t pkt;
	float accel[3];
	float gyro[3];

	LOG_INF("imu_thread up: 100 Hz BMI270 stream (%s)",
		sensor_imu_is_hardware_active() ? "hardware" : "synthetic fallback");

	while (1) {
		sensor_imu_fetch(accel, gyro);

		int64_t now_ms = k_uptime_get();

		if (cadence_detect(accel[2], now_ms) != 0) {
			atomic_inc(&g_steps);
		}

		memset(&pkt, 0, sizeof(pkt));
		pkt.timestamp_ms = now_ms;
		pkt.accel_x = accel[0];
		pkt.accel_y = accel[1];
		pkt.accel_z = accel[2];
		pkt.gyro_x = gyro[0];
		pkt.gyro_y = gyro[1];
		pkt.gyro_z = gyro[2];
		pkt.steps = (uint32_t)atomic_get(&g_steps);
		pkt.quality = 0;
		pkt.activity = NULL;
		pkt.exertion = NULL;

		queue_packet(&pkt);

		k_msleep(IMU_PERIOD_MS);
	}
}

static void ppg_thread(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	sensor_packet_t pkt;

	LOG_INF("ppg_thread up: 50 Hz MAX30102 stream (%s)",
		sensor_ppg_is_hardware_active() ? "hardware" : "synthetic fallback");

	while (1) {
		uint32_t raw_red = 0;
		uint32_t raw_ir = 0;
		uint8_t hr = 72;
		uint8_t spo2 = 97;
		bool quality = true;

		sensor_ppg_fetch(&raw_red, &raw_ir, &hr, &spo2, &quality);
		/* Telemetry schema carries no SpO2-from-PPG field yet; the
		 * infer/comm path keeps the constant 97. Drop is explicit. */
		(void)spo2;

		/* ppg_red is uint16_t: raw counts can exceed 65535, clamp. */
		uint16_t pkt_red = raw_red > 65535U ? 65535U : (uint16_t)raw_red;

		memset(&pkt, 0, sizeof(pkt));
		pkt.timestamp_ms = k_uptime_get();
		pkt.ppg_red = pkt_red;
		pkt.ppg_ir = raw_ir;
		pkt.heart_rate = (uint16_t)hr;
		pkt.steps = (uint32_t)atomic_get(&g_steps);
		pkt.quality = quality ? 1 : 0;
		pkt.activity = NULL;
		pkt.exertion = NULL;

		queue_packet(&pkt);

		k_msleep(PPG_PERIOD_MS);
	}
}

static char s_display_grid[DISPLAY_GRID_MIN_LEN];

/* Compact preview policy: one LOG_INF summary per event plus the full
 * 240-row grid at LOG_DBG in 30-row chunks, so frequent telemetry updates
 * stay readable while debug sessions still get every cell. The grid buffer
 * is file-scope BSS (57 KB), never thread stack. Chunk logging borrows a
 * save/restore NUL trick on the grid itself to avoid a second big buffer.
 */
static void log_display_preview(const char *why)
{
	struct shared_state snap;

	display_engine_render_ascii(s_display_grid, sizeof(s_display_grid));

	k_mutex_lock(&state_mutex, K_FOREVER);
	snap = current_state;
	k_mutex_unlock(&state_mutex);

	LOG_INF("display preview (%s): screen=%s ptt=%d hr=%u steps=%u act=%s ex=%s qual=%s",
		why,
		display_engine_screen_name(display_engine_current_screen()),
		(int)s_ptt_active,
		(unsigned int)snap.heart_rate,
		(unsigned int)snap.steps,
		snap.activity,
		snap.exertion,
		(snap.quality != 0U) ? "good" : "poor");

	for (int base = 0; base < DISPLAY_HEIGHT; base += 30) {
		size_t off = (size_t)base * ((size_t)DISPLAY_WIDTH + 1U);
		size_t len = (size_t)30 * ((size_t)DISPLAY_WIDTH + 1U);
		char saved;

		if (off + len > sizeof(s_display_grid) - 1U) {
			len = sizeof(s_display_grid) - 1U - off;
		}

		saved = s_display_grid[off + len];
		s_display_grid[off + len] = '\0';
		LOG_DBG("%s", &s_display_grid[off]);
		s_display_grid[off + len] = saved;
	}
}

static void infer_thread(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	sensor_packet_t pkt;
	activity_engine_t eng;
	uint16_t last_hr = 72;
	uint8_t last_quality = 0;
	uint32_t last_steps = 0;
	static char prev_activity[12] = "";
	static char prev_exertion[12] = "";

	activity_engine_init(&eng);

	LOG_INF("infer_thread up: rule-based activity/exertion inference");

	while (1) {
		k_msgq_get(&sensor_msgq, &pkt, K_FOREVER);

		if (pkt.ppg_ir != 0U) {
			last_hr = pkt.heart_rate;
			last_quality = (uint8_t)((pkt.ppg_red >= (uint16_t)PPG_RED_FLOOR) &&
						 (pkt.ppg_ir >= (uint32_t)PPG_IR_FLOOR) ? 1 : 0);
		}

		const char *activity = activity_engine_update(&eng, &pkt);
		float intensity = activity_engine_motion_intensity(&eng);
		const char *exertion = activity_engine_exertion(&eng, last_hr, activity);

		last_steps = pkt.steps;
		atomic_inc(&g_processed_packets);

		k_mutex_lock(&state_mutex, K_FOREVER);
		current_state.timestamp_ms = pkt.timestamp_ms;
		current_state.heart_rate = last_hr;
		current_state.spo2 = 97;
		current_state.steps = last_steps;
		current_state.quality = last_quality;
		current_state.motion_intensity = intensity;
		snprintk(current_state.activity, sizeof(current_state.activity), "%s", activity);
		snprintk(current_state.exertion, sizeof(current_state.exertion), "%s", exertion);
		k_mutex_unlock(&state_mutex);

		/* Feed the display engine outside the mutex (it owns its state).
		 * Called for every packet: PPG packets carry hr/quality, IMU and
		 * PPG packets both carry the running step count. SpO2 is the
		 * constant 97 per the current schema; the setter call keeps the
		 * wiring real for when the schema gains a SpO2 field. */
		display_engine_update_telemetry(&pkt, activity, exertion);
		display_engine_set_spo2(97);

		{
			int64_t up_ms = k_uptime_get();
			long sod = (long)((KINETIQ_EPOCH_BASE_S + (up_ms / 1000)) % 86400LL);

			display_engine_set_time((int)(sod / 3600), (int)((sod / 60) % 60));
		}

		if (strcmp(prev_activity, activity) != 0 ||
		    strcmp(prev_exertion, exertion) != 0) {
			snprintk(prev_activity, sizeof(prev_activity), "%s", activity);
			snprintk(prev_exertion, sizeof(prev_exertion), "%s", exertion);
			log_display_preview("infer_change");
		}
	}
}

#if DT_NODE_HAS_STATUS(DT_NODELABEL(kinetiq_button), okay)
#define KINETIQ_HAS_BUTTON_NODE 1
static const struct gpio_dt_spec kinetiq_btn_spec =
	GPIO_DT_SPEC_GET(DT_NODELABEL(kinetiq_button), gpios);
#else
#define KINETIQ_HAS_BUTTON_NODE 0
#endif

static void button_thread(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	LOG_INF("button_thread up: 100 Hz polled button FSM (%s)",
#if KINETIQ_HAS_BUTTON_NODE
		"GPIO1 gpio-keys"
#else
		"simulated stub"
#endif
		);

	while (1) {
		bool raw_level = true;

#if KINETIQ_HAS_BUTTON_NODE
		/* gpio_pin_get_dt returns the LOGICAL level: 1 = pressed for
		 * an ACTIVE_LOW button, 0 = released. button_fsm wants the
		 * RAW electrical level: true = idle/high, false = pressed.
		 * Hence raw_level = (logical == 0). Pull-up idle rests high.
		 */
		if (gpio_is_ready_dt(&kinetiq_btn_spec)) {
			int v = gpio_pin_get_dt(&kinetiq_btn_spec);

			if (v >= 0) {
				raw_level = (v == 0);
			}
		}
		/* Node missing or GPIO not ready falls through to the
		 * simulated released level (true), same as the old stub. */
#else
		/* Raw GPIO read replaces this constant when the devicetree button binding lands. */
#endif
		button_event_t ev = button_fsm_tick(&g_button, raw_level, k_uptime_get());

		if (ev == BUTTON_EVENT_SHORT_PRESS) {
			/* button_fsm keeps its own internal screen index for FSM
			 * unit-test parity, but display_engine is the canonical UI
			 * state, so short-press cycles the display engine here and
			 * button_fsm_on_short_press() is intentionally not called. */
			display_engine_cycle_screen();

			LOG_INF("UI screen -> %s",
				display_engine_screen_name(display_engine_current_screen()));
			log_display_preview("short_press");
		} else if (ev == BUTTON_EVENT_LONG_PRESS_START) {
			display_engine_set_ptt_active(true);
			s_ptt_active = true;
			LOG_INF("PTT capture armed");
			log_display_preview("ptt_start");
		} else if (ev == BUTTON_EVENT_LONG_PRESS_END) {
			display_engine_set_ptt_active(false);
			s_ptt_active = false;
			LOG_INF("PTT capture released");
			log_display_preview("ptt_end");
		}

		k_msleep(BUTTON_PERIOD_MS);
	}
}

static void comm_thread(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	char json_buf[224];
	struct shared_state snap;

	LOG_INF("comm_thread up: 1 Hz telemetry JSON");

	while (1) {
		k_mutex_lock(&state_mutex, K_FOREVER);
		snap = current_state;
		k_mutex_unlock(&state_mutex);

		long timestamp_s = (long)(KINETIQ_EPOCH_BASE_S +
					 (k_uptime_get() / 1000));

		snprintf(json_buf, sizeof(json_buf),
			 "{\"timestamp\":%ld,\"heart_rate\":%u,\"spo2\":%u,"
			 "\"activity\":\"%s\",\"motion_intensity\":%.2f,"
			 "\"exertion\":\"%s\",\"steps\":%u,\"ppg_quality\":\"%s\"}",
			 timestamp_s,
			 (unsigned int)snap.heart_rate,
			 (unsigned int)snap.spo2,
			 snap.activity,
			 (double)snap.motion_intensity,
			 snap.exertion,
			 (unsigned int)snap.steps,
			 (snap.quality != 0U) ? "good" : "poor");

		LOG_INF("%s", json_buf);

		if (ble_transport_is_connected()) {
			int brc = ble_transport_send_telemetry(json_buf, strlen(json_buf));

			if (brc < 0) {
				LOG_WRN("BLE tx failed: %d", brc);
			}
		} else {
			LOG_DBG("BLE not connected, UART log only");
		}

		k_msleep(COMM_PERIOD_MS);
	}
}

int main(void)
{
	LOG_INF("KINETIQ firmware boot - Zephyr on ESP32-S3 (kernel %s)", KERNEL_VERSION_STRING);
	LOG_INF("I2C0 400 kHz: SDA=GPIO4 SCL=GPIO5, BMI270@0x68 (imu_thread 100 Hz), MAX30102@0x57 (ppg_thread 50 Hz)");
	LOG_INF("SPI2 40 MHz: GC9A01 (SCK=GPIO12, MOSI=GPIO11, CS=GPIO10, DC=GPIO9, RST=GPIO14, BL=GPIO13)");
	LOG_INF("I2S0 reserved W4 audio: BCK=GPIO8 WS=GPIO7 RX_SD=GPIO6 TX_SD=GPIO17 (no SW use yet)");
#if KINETIQ_HAS_BUTTON_NODE
	LOG_INF("Button: KINETIQ PTT on GPIO1 (gpio-keys, active-low pull-up)");
#else
	LOG_INF("Button: simulated stub (no gpio-keys node)");
#endif
	LOG_INF("BLE NUS telemetry: name KINETIQ, newline-framed JSON notify, UART log fallback");
	LOG_INF("Starting sensor, inference, display, button and communication threads");

	sensor_imu_init();
	sensor_ppg_init();
	LOG_INF("IMU: %s",
		sensor_imu_is_hardware_active() ? "BMI270 hardware" : "synthetic fallback");
	LOG_INF("PPG: %s",
		sensor_ppg_is_hardware_active() ? "MAX30102 hardware" : "synthetic fallback");

#if KINETIQ_HAS_BUTTON_NODE
	if (gpio_is_ready_dt(&kinetiq_btn_spec)) {
		int brc = gpio_pin_configure_dt(&kinetiq_btn_spec, GPIO_INPUT);

		if (brc != 0) {
			LOG_WRN("PTT button GPIO config failed (%d), simulated reads", brc);
		}
	}
#endif

	button_fsm_init(&g_button, true, k_uptime_get());
	display_engine_init();
	LOG_INF("display_engine up: HOME screen, 240x240 round GC9A01 grid");

	ble_transport_register_rx_callback(on_laptop_rx);
	{
		int rc = ble_transport_init();

		if (rc != 0) {
			LOG_WRN("BLE init failed (%d), UART telemetry only", rc);
		} else {
			LOG_INF("BLE up: name KINETIQ, NUS advertising");
		}
	}

	k_thread_create(&imu_tid, imu_stack, THREAD_STACK_SIZE,
			imu_thread, NULL, NULL, NULL, IMU_PRIO, 0, K_NO_WAIT);
	k_thread_create(&ppg_tid, ppg_stack, THREAD_STACK_SIZE,
			ppg_thread, NULL, NULL, NULL, PPG_PRIO, 0, K_NO_WAIT);
	k_thread_create(&infer_tid, infer_stack, THREAD_STACK_SIZE,
			infer_thread, NULL, NULL, NULL, INFER_PRIO, 0, K_NO_WAIT);
	k_thread_create(&comm_tid, comm_stack, THREAD_STACK_SIZE,
			comm_thread, NULL, NULL, NULL, COMM_PRIO, 0, K_NO_WAIT);
	k_thread_create(&button_tid, button_stack, BUTTON_STACK_SIZE,
			button_thread, NULL, NULL, NULL, BUTTON_PRIO, 0, K_NO_WAIT);

	k_thread_name_set(&imu_tid, "imu_thread");
	k_thread_name_set(&ppg_tid, "ppg_thread");
	k_thread_name_set(&infer_tid, "infer_thread");
	k_thread_name_set(&comm_tid, "comm_thread");
	k_thread_name_set(&button_tid, "button_thread");

	return 0;
}
