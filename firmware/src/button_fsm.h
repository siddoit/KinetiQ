/*
 * KINETIQ firmware - polled software-debounce button state machine.
 *
 * Timing engine for ONE physical GPIO button. Hardware-independent:
 * the application polls button_fsm_tick() with the sampled raw level
 * and a monotonic millisecond timestamp; there are no GPIO driver
 * calls here, so timing transitions stay unit-testable without IRQs.
 *
 * Assumes an active-low button with pull-up: raw level LOW (false) =
 * physically pressed, raw level HIGH (true) = released / idle.
 *
 * One-event-per-tick priority: LONG_PRESS_START beats any release
 * event when both coincide on the same tick.
 */
#ifndef BUTTON_FSM_H
#define BUTTON_FSM_H

#include <stdint.h>
#include <stdbool.h>

typedef enum {
	BUTTON_STATE_IDLE,
	BUTTON_STATE_DEBOUNCING,
	BUTTON_STATE_PRESSED
} button_state_t;

typedef enum {
	BUTTON_EVENT_NONE,
	BUTTON_EVENT_SHORT_PRESS,
	BUTTON_EVENT_LONG_PRESS_START,
	BUTTON_EVENT_LONG_PRESS_END
} button_event_t;

typedef enum {
	UI_SCREEN_HOME,
	UI_SCREEN_ACTIVITY,
	UI_SCREEN_BODY,
	UI_SCREEN_ASSISTANT,
	UI_SCREEN_COUNT
} ui_screen_t;

#define BUTTON_DEBOUNCE_MS 25

#ifndef BUTTON_LONG_PRESS_MS
#define BUTTON_LONG_PRESS_MS 800
#endif

typedef struct {
	button_state_t state;
	bool raw_last;
	bool stable_level;
	int64_t t_change_ms;
	int64_t press_start_ms;
	bool long_press_fired;
	ui_screen_t screen;
} button_fsm_t;

void button_fsm_init(button_fsm_t *btn, bool initial_raw_level, int64_t now_ms);
button_event_t button_fsm_tick(button_fsm_t *btn, bool raw_level, int64_t now_ms);
ui_screen_t button_fsm_on_short_press(button_fsm_t *btn);
button_state_t button_fsm_state(const button_fsm_t *btn);
ui_screen_t button_fsm_screen(const button_fsm_t *btn);

#endif /* BUTTON_FSM_H */
