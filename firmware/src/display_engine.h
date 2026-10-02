/*
 * KINETIQ firmware - display engine: static render state for the 240x240
 * round GC9A01 over SPI (direct-draw, no LVGL, no framebuffer alloc).
 *
 * Pure C (stdint/stdbool/stddef/string.h/stdio.h only, no Zephyr symbols)
 * so the engine stays portable and unit-testable on the host. Single
 * file-scope instance: one display per wearable.
 *
 * Screen type is NOT redefined here: button_fsm.h owns ui_screen_t /
 * UI_SCREEN_* and this header reuses it, adding spec-friendly aliases
 * (SCREEN_HOME etc.) so only one screen enum exists per translation unit.
 * The display engine owns the canonical current-screen index; button_fsm
 * keeps its own internal index for FSM unit-test parity only.
 *
 * ASCII grid layout (display_engine_render_ascii): 240 rows of 240 cells,
 * LF-terminated, background '.', 1px circle boundary '#' (center 120,120,
 * radius 119, integer dx*dx+dy*dy math, no floats), cells outside the
 * circle blanked to ' ' so the grid looks round. Screen text lines are
 * drawn centered at fixed row bands (70/110/150 for 3-line screens,
 * 100+120 for 2-line ASSISTANT idle, 110 for 1-line RECORDING).
 *
 * Required buffer size is DISPLAY_GRID_MIN_LEN (57841). Render truncates
 * at the last complete row when max_len is smaller, always NUL-terminates,
 * never writes past max_len - 1, never mallocs.
 *
 * Clock: main feeds wall-clock hours/minutes via display_engine_set_time()
 * derived from KINETIQ_EPOCH_BASE_S + k_uptime_get()/1000.
 */
#ifndef DISPLAY_ENGINE_H
#define DISPLAY_ENGINE_H

#include "button_fsm.h"
#include "sensor_packet.h"

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define DISPLAY_WIDTH 240
#define DISPLAY_HEIGHT 240

#define DISPLAY_CIRCLE_CX 120
#define DISPLAY_CIRCLE_CY 120
#define DISPLAY_CIRCLE_R 119

#define DISPLAY_GRID_MIN_LEN (((DISPLAY_WIDTH) + 1) * (DISPLAY_HEIGHT) + 1)

#define SCREEN_HOME UI_SCREEN_HOME
#define SCREEN_ACTIVITY UI_SCREEN_ACTIVITY
#define SCREEN_BODY UI_SCREEN_BODY
#define SCREEN_ASSISTANT UI_SCREEN_ASSISTANT

#define DISPLAY_QUALITY_LEN 8
#define DISPLAY_LABEL_LEN 16

typedef struct {
	ui_screen_t screen;
	bool ptt_active;
	uint16_t heart_rate;
	uint8_t spo2;
	uint32_t steps;
	char quality[DISPLAY_QUALITY_LEN];
	char activity[DISPLAY_LABEL_LEN];
	char exertion[DISPLAY_LABEL_LEN];
	int clock_hours;
	int clock_minutes;
	uint32_t update_count;
} display_engine_state_t;

void display_engine_init(void);
void display_engine_cycle_screen(void);
void display_engine_set_ptt_active(bool active);
void display_engine_update_telemetry(const sensor_packet_t *packet,
				     const char *activity,
				     const char *exertion);
void display_engine_set_spo2(uint8_t spo2);
void display_engine_set_time(int hours, int minutes);
ui_screen_t display_engine_current_screen(void);
const char *display_engine_screen_name(ui_screen_t screen);
void display_engine_render_ascii(char *out_buffer, size_t max_len);

#endif /* DISPLAY_ENGINE_H */
