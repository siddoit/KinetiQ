/*
 * KINETIQ firmware - display engine implementation.
 *
 * Pure C: only this header plus string.h/stdio.h. All render state lives
 * in one static file-scope instance (single-display assumption). No
 * malloc, no floats (integer circle math), every string/grid write is
 * bounds-checked against the destination size and NUL-terminated.
 */
#include "display_engine.h"

#include <string.h>
#include <stdio.h>

#define DISPLAY_LINE_BUF 32
#define DISPLAY_R_SQ (DISPLAY_CIRCLE_R * DISPLAY_CIRCLE_R)
#define DISPLAY_R_INNER_SQ ((DISPLAY_CIRCLE_R - 1) * (DISPLAY_CIRCLE_R - 1))

static display_engine_state_t s_disp;

static void disp_copy_str(char *dst, size_t dst_sz, const char *src)
{
	size_t i = 0;

	if (dst == NULL || dst_sz == 0) {
		return;
	}
	if (src == NULL) {
		dst[0] = '\0';
		return;
	}
	while (i + 1U < dst_sz && src[i] != '\0') {
		dst[i] = src[i];
		i++;
	}
	dst[i] = '\0';
}

static void disp_copy_upper(char *dst, size_t dst_sz, const char *src)
{
	size_t i = 0;

	if (dst == NULL || dst_sz == 0) {
		return;
	}
	if (src == NULL) {
		dst[0] = '\0';
		return;
	}
	while (i + 1U < dst_sz && src[i] != '\0') {
		char c = src[i];

		if (c >= 'a' && c <= 'z') {
			c = (char)(c - ('a' - 'A'));
		}
		dst[i] = c;
		i++;
	}
	dst[i] = '\0';
}

static bool disp_quality_is_good(const char *quality)
{
	static const char good[] = "good";
	size_t i = 0;

	if (quality == NULL) {
		return false;
	}
	while (good[i] != '\0') {
		char a = quality[i];
		char b = good[i];

		if (a == '\0') {
			return false;
		}
		if (a >= 'A' && a <= 'Z') {
			a = (char)(a + ('a' - 'A'));
		}
		if (a != b) {
			return false;
		}
		i++;
	}
	return quality[i] == '\0';
}

void display_engine_init(void)
{
	s_disp.screen = UI_SCREEN_HOME;
	s_disp.ptt_active = false;
	s_disp.heart_rate = 72;
	s_disp.spo2 = 97;
	s_disp.steps = 0;
	disp_copy_str(s_disp.quality, sizeof(s_disp.quality), "poor");
	disp_copy_str(s_disp.activity, sizeof(s_disp.activity), "REST");
	disp_copy_str(s_disp.exertion, sizeof(s_disp.exertion), "REST");
	s_disp.clock_hours = 0;
	s_disp.clock_minutes = 0;
	s_disp.update_count = 0;
}

void display_engine_cycle_screen(void)
{
	s_disp.screen = (ui_screen_t)((s_disp.screen + 1) % UI_SCREEN_COUNT);
}

void display_engine_set_ptt_active(bool active)
{
	s_disp.ptt_active = active;
}

void display_engine_update_telemetry(const sensor_packet_t *packet,
				     const char *activity,
				     const char *exertion)
{
	if (packet == NULL) {
		return;
	}

	s_disp.heart_rate = packet->heart_rate;
	s_disp.steps = packet->steps;
	disp_copy_str(s_disp.quality, sizeof(s_disp.quality),
		      (packet->quality != 0U) ? "good" : "poor");
	if (activity != NULL) {
		disp_copy_str(s_disp.activity, sizeof(s_disp.activity), activity);
	}
	if (exertion != NULL) {
		disp_copy_str(s_disp.exertion, sizeof(s_disp.exertion), exertion);
	}
	s_disp.update_count++;
}

void display_engine_set_spo2(uint8_t spo2)
{
	s_disp.spo2 = spo2;
}

void display_engine_set_time(int hours, int minutes)
{
	hours %= 24;
	if (hours < 0) {
		hours += 24;
	}
	minutes %= 60;
	if (minutes < 0) {
		minutes += 60;
	}
	s_disp.clock_hours = hours;
	s_disp.clock_minutes = minutes;
}

ui_screen_t display_engine_current_screen(void)
{
	return s_disp.screen;
}

const char *display_engine_screen_name(ui_screen_t screen)
{
	switch (screen) {
	case UI_SCREEN_HOME:
		return "HOME";
	case UI_SCREEN_ACTIVITY:
		return "ACTIVITY";
	case UI_SCREEN_BODY:
		return "BODY";
	case UI_SCREEN_ASSISTANT:
		return "ASSISTANT";
	default:
		return "UNKNOWN";
	}
}

void display_engine_render_ascii(char *out_buffer, size_t max_len)
{
	char line0[DISPLAY_LINE_BUF];
	char line1[DISPLAY_LINE_BUF];
	char line2[DISPLAY_LINE_BUF];
	const char *lines[3];
	int rows[3];
	int nlines = 0;
	int r;
	int c;

	line0[0] = '\0';
	line1[0] = '\0';
	line2[0] = '\0';

	switch (s_disp.screen) {
	case UI_SCREEN_ACTIVITY: {
		char act_up[DISPLAY_LABEL_LEN];
		char ex_up[DISPLAY_LABEL_LEN];

		disp_copy_upper(act_up, sizeof(act_up), s_disp.activity);
		disp_copy_upper(ex_up, sizeof(ex_up), s_disp.exertion);
		snprintf(line0, sizeof(line0), "%s", act_up);
		snprintf(line1, sizeof(line1), "%u BPM", (unsigned int)s_disp.heart_rate);
		snprintf(line2, sizeof(line2), "%s", ex_up);
		lines[0] = line0;
		lines[1] = line1;
		lines[2] = line2;
		rows[0] = 70;
		rows[1] = 110;
		rows[2] = 150;
		nlines = 3;
		break;
	}
	case UI_SCREEN_BODY: {
		char spo2_txt[8];
		char qual_up[DISPLAY_QUALITY_LEN];

		if (disp_quality_is_good(s_disp.quality)) {
			snprintf(spo2_txt, sizeof(spo2_txt), "%u",
				 (unsigned int)s_disp.spo2);
		} else {
			snprintf(spo2_txt, sizeof(spo2_txt), "--");
		}
		disp_copy_upper(qual_up, sizeof(qual_up), s_disp.quality);
		snprintf(line0, sizeof(line0), "HR %u", (unsigned int)s_disp.heart_rate);
		snprintf(line1, sizeof(line1), "SpO2 %s", spo2_txt);
		snprintf(line2, sizeof(line2), "QUALITY %s", qual_up);
		lines[0] = line0;
		lines[1] = line1;
		lines[2] = line2;
		rows[0] = 70;
		rows[1] = 110;
		rows[2] = 150;
		nlines = 3;
		break;
	}
	case UI_SCREEN_ASSISTANT:
		if (s_disp.ptt_active) {
			snprintf(line0, sizeof(line0), "RECORDING...");
			lines[0] = line0;
			rows[0] = 110;
			nlines = 1;
		} else {
			snprintf(line0, sizeof(line0), "READY");
			snprintf(line1, sizeof(line1), "- hold button to talk");
			lines[0] = line0;
			lines[1] = line1;
			rows[0] = 100;
			rows[1] = 120;
			nlines = 2;
		}
		break;
	case UI_SCREEN_HOME:
	default:
		snprintf(line0, sizeof(line0), "%u BPM", (unsigned int)s_disp.heart_rate);
		snprintf(line1, sizeof(line1), "%u STEPS", (unsigned int)s_disp.steps);
		snprintf(line2, sizeof(line2), "%02d:%02d",
			 s_disp.clock_hours, s_disp.clock_minutes);
		lines[0] = line0;
		lines[1] = line1;
		lines[2] = line2;
		rows[0] = 70;
		rows[1] = 110;
		rows[2] = 150;
		nlines = 3;
		break;
	}

	if (out_buffer == NULL || max_len == 0) {
		return;
	}

	{
		size_t pos = 0;

		for (r = 0; r < DISPLAY_HEIGHT; r++) {
			size_t len;
			int i;

			if (pos + (size_t)DISPLAY_WIDTH + 1U + 1U > max_len) {
				break;
			}
			for (c = 0; c < DISPLAY_WIDTH; c++) {
				int dx = c - DISPLAY_CIRCLE_CX;
				int dy = r - DISPLAY_CIRCLE_CY;
				int d2 = (dx * dx) + (dy * dy);
				char ch;

				if (d2 > DISPLAY_R_SQ) {
					ch = ' ';
				} else if (d2 > DISPLAY_R_INNER_SQ) {
					ch = '#';
				} else {
					ch = '.';
				}
				out_buffer[pos + (size_t)c] = ch;
			}
			for (i = 0; i < nlines; i++) {
				if (rows[i] == r) {
					int start;

					len = strlen(lines[i]);
					start = (DISPLAY_WIDTH - (int)len) / 2;
					for (c = 0; c < (int)len; c++) {
						int col = start + c;

						if (col >= 0 && col < DISPLAY_WIDTH) {
							out_buffer[pos + (size_t)col] =
								lines[i][c];
						}
					}
				}
			}
			out_buffer[pos + (size_t)DISPLAY_WIDTH] = '\n';
			pos += (size_t)DISPLAY_WIDTH + 1U;
		}
		out_buffer[pos] = '\0';
	}
}
