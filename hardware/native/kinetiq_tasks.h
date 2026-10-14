/*
 * KINETIQ native device node — task declarations + shared config.
 *
 * Real FreeRTOS tasks running on the MSVC-MingW simulator port:
 *   Console  — virtual trimpot / key controls (low priority, ~50 ms poll)
 *   Sensor   — virtual IMU + DHT22 sampling at 100 ms, feeds the queue
 *   Classify — 1 Hz hysteresis: rest | walk | run (schema-safe only)
 *   Telemetry— 1 Hz physiology model, the ONLY task that prints (funnel)
 */

#ifndef KINETIQ_TASKS_H
#define KINETIQ_TASKS_H

#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "semphr.h"

/* ---- Task configuration block: names, priorities, stacks (words) ---- */
#define KINETIQ_PRIO_CONSOLE    ( tskIDLE_PRIORITY + 1 )
#define KINETIQ_PRIO_SENSOR     ( tskIDLE_PRIORITY + 2 )
#define KINETIQ_PRIO_CLASSIFY   ( tskIDLE_PRIORITY + 2 )
#define KINETIQ_PRIO_TELEMETRY  ( tskIDLE_PRIORITY + 3 )

#define KINETIQ_STACK_CONSOLE   ( 512 )
#define KINETIQ_STACK_SENSOR    ( 512 )
#define KINETIQ_STACK_CLASSIFY  ( 512 )
#define KINETIQ_STACK_TELEMETRY ( 1024 )

#define KINETIQ_NAME_CONSOLE    "Console"
#define KINETIQ_NAME_SENSOR     "Sensor"
#define KINETIQ_NAME_CLASSIFY   "Classify"
#define KINETIQ_NAME_TELEMETRY  "Telemetry"

/* One sensor sample travelling sensor -> classify through the queue. */
typedef struct
{
    float    motion;    /* normalised virtual-IMU intensity, 0.00 - 1.00 */
    float    temp_c;    /* virtual DHT22 temperature, degrees C */
    uint32_t seq;       /* monotonically increasing sample counter */
} SensorSample_t;

/* app_main()-style init: creates the queue, mutex and all four tasks. */
void Kinetiq_CreateTasks( void );

/* Prints the key-control banner (called pre-scheduler from main). */
void Kinetiq_PrintHelp( void );

#endif /* KINETIQ_TASKS_H */
