/*
 * KINETIQ native device node — Windows port entry point.
 *
 * Real FreeRTOS v11 kernel (heap_4, MSVC-MingW simulator port) running
 * natively on Windows: heap_4 needs no special init, so we print the
 * startup banner, create the device tasks and hand over to the scheduler.
 * vTaskStartScheduler() only returns on failure.
 *
 * Test hook:  kinetiq_native.exe --run-seconds N
 * exits the process after N seconds (a plain Win32 watchdog thread) so
 * scripted captures and the laptop pipe test terminate deterministically.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <windows.h>

#include "FreeRTOS.h"
#include "task.h"

#include "kinetiq_tasks.h"

static int g_runSeconds = 0;

static DWORD WINAPI WatchdogThread( LPVOID arg )
{
    ( void ) arg;
    Sleep( ( DWORD ) g_runSeconds * 1000UL );
    fflush( stdout );
    fflush( stderr );
    fprintf( stderr, "[kinetiq] --run-seconds %d elapsed, exiting\n", g_runSeconds );
    fflush( stderr );
    ExitProcess( 0 );
    return 0;
}

/* configUSE_MALLOC_FAILED_HOOK = 1 requires this symbol. */
void vApplicationMallocFailedHook( void )
{
    fprintf( stderr, "[kinetiq] FATAL: pvPortMalloc failed (heap exhausted)\n" );
    fflush( stderr );
    taskDISABLE_INTERRUPTS();
    for( ;; )
    {
    }
}

int main( int argc, char * argv[] )
{
    int i;

    for( i = 1; i < argc; i++ )
    {
        if( strcmp( argv[ i ], "--run-seconds" ) == 0 && i + 1 < argc )
        {
            g_runSeconds = atoi( argv[ ++i ] );
            if( g_runSeconds < 0 )
            {
                g_runSeconds = 0;
            }
        }
        else if( strcmp( argv[ i ], "--help" ) == 0 || strcmp( argv[ i ], "-h" ) == 0 )
        {
            printf( "usage: kinetiq_native.exe [--run-seconds N]\n" );
            return 0;
        }
    }

    /* Banner is diagnostics: stderr keeps stdout 100% schema JSON so the
     * laptop pipe (dashboard.py --source stdin) counts zero dropped frames. */
    fprintf( stderr, "==============================================================\n" );
    fprintf( stderr, " KINETIQ wearable node - real FreeRTOS kernel on Windows\n" );
    fprintf( stderr, " (simulator port; virtual IMU + DHT22, no hardware needed)\n" );
    fprintf( stderr, "--------------------------------------------------------------\n" );
    fprintf( stderr, " stdout = schema JSON telemetry (1 Hz) -> pipe into dashboard\n" );
    fprintf( stderr, " stderr = RTOS status + key notices (human diagnostics)\n" );
    fprintf( stderr, " No keys = autonomous scenario loop: rest-walk-run-walk-rest\n" );
    fflush( stderr );
    Kinetiq_PrintHelp();

    if( g_runSeconds > 0 )
    {
        HANDLE h = CreateThread( NULL, 0, WatchdogThread, NULL, 0, NULL );
        if( h != NULL )
        {
            CloseHandle( h );
        }
    }

    Kinetiq_CreateTasks();

    /* heap_4 needs no platform init — start the preemptive scheduler. */
    vTaskStartScheduler();

    /* Only reached if the scheduler failed to start (e.g. no heap). */
    fprintf( stderr, "[kinetiq] FATAL: vTaskStartScheduler returned\n" );
    return 1;
}
