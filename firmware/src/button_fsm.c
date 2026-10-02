/*
 * KINETIQ firmware - button_fsm tick/polling implementation.
 *
 * Pure C: no dynamic allocation, all state lives in the caller-owned
 * button_fsm_t, no dependencies beyond stdint/stdbool. Press edge and
 * release edge both pass through the same 25 ms debounce window.
 */
#include "button_fsm.h"

#include <stddef.h>

void button_fsm_init(button_fsm_t *btn, bool initial_raw_level, int64_t now_ms)
{
	if (btn == NULL) {
		return;
	}

	btn->state = BUTTON_STATE_IDLE;
	btn->raw_last = initial_raw_level;
	btn->stable_level = initial_raw_level;
	btn->t_change_ms = now_ms;
	btn->press_start_ms = now_ms;
	btn->long_press_fired = false;
	btn->screen = UI_SCREEN_HOME;
}

button_event_t button_fsm_tick(button_fsm_t *btn, bool raw_level, int64_t now_ms)
{
	if (btn == NULL) {
		return BUTTON_EVENT_NONE;
	}

	switch (btn->state) {
	case BUTTON_STATE_IDLE:
		if (raw_level != btn->stable_level) {
			btn->state = BUTTON_STATE_DEBOUNCING;
			btn->t_change_ms = now_ms;
			btn->raw_last = raw_level;
		}
		break;

	case BUTTON_STATE_DEBOUNCING:
		if (raw_level == btn->stable_level) {
			btn->raw_last = raw_level;
			btn->state = (!btn->stable_level) ?
				BUTTON_STATE_PRESSED : BUTTON_STATE_IDLE;
		} else if (raw_level != btn->raw_last) {
			btn->t_change_ms = now_ms;
			btn->raw_last = raw_level;
		} else if ((now_ms - btn->t_change_ms) >= BUTTON_DEBOUNCE_MS) {
			btn->stable_level = raw_level;
			btn->raw_last = raw_level;
			if (!btn->stable_level) {
				btn->state = BUTTON_STATE_PRESSED;
				btn->press_start_ms = now_ms;
				btn->long_press_fired = false;
			} else {
				btn->state = BUTTON_STATE_IDLE;
				if (btn->long_press_fired) {
					return BUTTON_EVENT_LONG_PRESS_END;
				}
				if ((now_ms - btn->press_start_ms) >= BUTTON_LONG_PRESS_MS) {
					btn->long_press_fired = true;
					return BUTTON_EVENT_LONG_PRESS_END;
				}
				return BUTTON_EVENT_SHORT_PRESS;
			}
		}
		break;

	case BUTTON_STATE_PRESSED:
		if (raw_level != btn->stable_level) {
			btn->state = BUTTON_STATE_DEBOUNCING;
			btn->t_change_ms = now_ms;
			btn->raw_last = raw_level;
		} else if (!btn->long_press_fired &&
			   ((now_ms - btn->press_start_ms) >= BUTTON_LONG_PRESS_MS)) {
			btn->long_press_fired = true;
			return BUTTON_EVENT_LONG_PRESS_START;
		}
		break;

	default:
		btn->state = BUTTON_STATE_IDLE;
		break;
	}

	return BUTTON_EVENT_NONE;
}

ui_screen_t button_fsm_on_short_press(button_fsm_t *btn)
{
	if (btn == NULL) {
		return UI_SCREEN_HOME;
	}

	btn->screen = (ui_screen_t)((btn->screen + 1) % UI_SCREEN_COUNT);

	return btn->screen;
}

button_state_t button_fsm_state(const button_fsm_t *btn)
{
	if (btn == NULL) {
		return BUTTON_STATE_IDLE;
	}

	return btn->state;
}

ui_screen_t button_fsm_screen(const button_fsm_t *btn)
{
	if (btn == NULL) {
		return UI_SCREEN_HOME;
	}

	return btn->screen;
}
