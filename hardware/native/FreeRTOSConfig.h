/*
 * KINETIQ native simulator — FreeRTOSConfig.h
 *
 * Cribbed from the official WIN32-MingW demo
 * (FreeRTOS/Demo/WIN32-MingW/FreeRTOSConfig.h, FreeRTOS V202212.00)
 * and trimmed for this simulator: no trace recorder, no coverage hooks,
 * no run-time stats — just what the MSVC-MingW port + heap_4 need.
 *
 * Port requirements honoured:
 *   configTICK_RATE_HZ = 1000, configUSE_PREEMPTION = 1,
 *   configMAX_PRIORITIES = 8 (<= 32, port-optimised selection on),
 *   configSUPPORT_DYNAMIC_ALLOCATION = 1 with heap_4,
 *   configUSE_TRACE_FACILITY = 1 (for uxTaskGetNumberOfTasks),
 *   32-bit tick (simple %lu printing of xTaskGetTickCount()).
 */

#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

/*-----------------------------------------------------------
 * Application specific definitions.
 *----------------------------------------------------------*/

#define configUSE_PREEMPTION                    1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 1
#define configUSE_IDLE_HOOK                     0
#define configUSE_TICK_HOOK                     0
#define configUSE_DAEMON_TASK_STARTUP_HOOK      0
#define configTICK_RATE_HZ                      ( ( TickType_t ) 1000 )
#define configMINIMAL_STACK_SIZE                ( ( unsigned short ) 256 )
#define configTOTAL_HEAP_SIZE                   ( ( size_t ) ( 100 * 1024 ) )
#define configMAX_TASK_NAME_LEN                 ( 12 )
#define configUSE_TRACE_FACILITY                1
#define configUSE_STATS_FORMATTING_FUNCTIONS    0
#define configIDLE_SHOULD_YIELD                 1
#define configUSE_MUTEXES                       1
#define configCHECK_FOR_STACK_OVERFLOW          0
#define configUSE_RECURSIVE_MUTEXES             1
#define configQUEUE_REGISTRY_SIZE               8
#define configUSE_COUNTING_SEMAPHORES           1
#define configUSE_QUEUE_SETS                    0
#define configUSE_TASK_NOTIFICATIONS            1
#define configUSE_TIME_SLICING                  1
#define configUSE_NEWLIB_REENTRANT              0
#define configUSE_APPLICATION_TASK_TAG          0
#define configSUPPORT_STATIC_ALLOCATION         0
#define configSUPPORT_DYNAMIC_ALLOCATION        1

/* 32-bit tick keeps the RTOS status line printable with a plain %lu. */
#define configTICK_TYPE_WIDTH_IN_BITS           TICK_TYPE_WIDTH_32_BITS

/* Software timer support (timer service task is created by the kernel). */
#define configUSE_TIMERS                        1
#define configTIMER_TASK_PRIORITY               ( configMAX_PRIORITIES - 1 )
#define configTIMER_QUEUE_LENGTH                10
#define configTIMER_TASK_STACK_DEPTH            ( configMINIMAL_STACK_SIZE * 2 )

#define configMAX_PRIORITIES                    ( 8 )

/* Co-routine support (unused — croutine.c is not compiled). */
#define configUSE_CO_ROUTINES                   0
#define configMAX_CO_ROUTINE_PRIORITIES         ( 2 )

#define configSTACK_DEPTH_TYPE                  uint32_t

/* API inclusion set. xTaskGetTickCount() is always provided by the
 * kernel; the two below must be explicitly enabled for our RTOS
 * status line (vTaskDelay paces every task). */
#define INCLUDE_vTaskPrioritySet                1
#define INCLUDE_uxTaskPriorityGet               1
#define INCLUDE_vTaskDelete                     1
#define INCLUDE_vTaskSuspend                    1
#define INCLUDE_vTaskDelayUntil                 1
#define INCLUDE_vTaskDelay                      1
#define INCLUDE_xTaskGetSchedulerState          1
#define INCLUDE_xTaskGetHandle                  1
#define INCLUDE_eTaskGetState                   1
#define INCLUDE_uxTaskGetStackHighWaterMark     0
#define INCLUDE_xTaskAbortDelay                 0

#define configUSE_MALLOC_FAILED_HOOK            1

/* Halt (with interrupts off) on a kernel assertion — same spirit as the
 * official demo's vAssertCalled(), without the trace-recorder dependency. */
#define configASSERT( x )                       if( ( x ) == 0 ) { taskDISABLE_INTERRUPTS(); for( ;; ); }

#endif /* FREERTOS_CONFIG_H */
