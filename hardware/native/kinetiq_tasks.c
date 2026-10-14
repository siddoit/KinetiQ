/*
 * KINETIQ native device node — real FreeRTOS tasks (MSVC-MingW port).
 *
 * Mirrors the Wokwi sketch architecture with a virtual sensor front-end:
 *   Console   (prio 1, 50 ms): _kbhit()/_getch() virtual trimpot.
 *   Sensor    (prio 2, 100 ms): virtual IMU band + noise, virtual DHT22
 *             drift; pushes SensorSample_t into the queue.
 *   Classify  (prio 2, 1 Hz): drains the queue, hysteresis to rest|walk|run.
 *   Telemetry (prio 3, 1 Hz): physiology model (HR lag, steps, exertion,
 *             SpO2 dip, PPG quality). ONLY task that prints — everything
 *             else funnels one-shot notices through it, so stdout/stderr
 *             lines never interleave mid-line.
 *
 * Stream split (deliberate, for the laptop pipe):
 *   stdout = schema JSON only  -> dashboard.py --source stdin sees dropped=0
 *   stderr = RTOS status + notices (human diagnostics, console bell)
 *
 * Physiology constants mirror laptop/receiver/mock_streamer.py so the
 * native node and the Python mock tell the same believable story.
 */

#include <conio.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "semphr.h"

#include "kinetiq_tasks.h"

/* ---- Tunables (same spirit as mock_streamer.py) ---- */
#define EPOCH_BASE_S        ( 1750000000L )

#define HR_BASELINE         ( 68.0f )
#define HR_OFFSET_WALK      ( 26.0f )   /* walk target  94 BPM */
#define HR_OFFSET_RUN       ( 84.0f )   /* run target  152 BPM */
#define HR_TAU_RISE_S       ( 7.0f )
#define HR_TAU_FALL_S       ( 15.0f )
#define HR_NOISE_BPM        ( 2.5f )

#define STEP_RATE_WALK      ( 1.6f )
#define STEP_RATE_RUN       ( 2.8f )

#define EXERTION_WINDOW_N   ( 30 )      /* trailing ~30 s mean at 1 Hz */
#define SPO2_DIP_S          ( 4UL )     /* transient dip after run onset */
#define SPO2_DIP_VALUE      ( 95 )

#define FALL_SPIKE          ( 0.95f )   /* motion spike at fall trigger */
#define FALL_DECAY_S        ( 2.5f )    /* exp decay: ~gone after ~8 s */
#define FALL_DURATION_MS    ( 8000UL )

#define SAMPLE_QUEUE_LEN    ( 8 )

/* Autonomous scenario loop: (band, dwell s). "recover" reuses the rest
 * band — the story is told by the still-elevated, falling heart rate,
 * exactly like the Python mock. Bands: 0 = rest, 1 = walk, 2 = run. */
typedef struct { int band; uint32_t dwell_s; } PhaseDef_t;
static const PhaseDef_t PHASES[] =
{
    { 0, 15 }, { 1, 12 }, { 2, 15 }, { 1, 10 }, { 0, 15 }
};
#define N_PHASES ( sizeof( PHASES ) / sizeof( PHASES[ 0 ] ) )
#define LOOP_S   ( 15 + 12 + 15 + 10 + 15 )

static const float BAND_BASE[] = { 0.05f, 0.45f, 0.88f };

/* ---- Shared state (guarded by g_mutex) ---- */
typedef struct
{
    int      forced_band;               /* -1 = autonomous loop */
    TickType_t fall_start;              /* tick of last 'b' press */
    TickType_t fall_until;              /* spike active while now < this */
    char     activity[ 8 ];             /* classify output */
    float    latest_motion;             /* most recent sample (telemetry) */
    char     notice[ 128 ];             /* one-shot console notice */
    int      notice_bell;               /* prefix notice with double bell */
    int      notice_pending;
    int      help_pending;
} SharedState_t;

static SharedState_t g_shared;
static QueueHandle_t g_sampleQ = NULL;
static SemaphoreHandle_t g_mutex = NULL;

/* ---- Telemetry history (owned solely by the telemetry task) ---- */
static float g_hr = HR_BASELINE;
static float g_steps = 4218.0f;
static float g_intensityWin[ EXERTION_WINDOW_N ];
static int   g_winCount = 0;
static int   g_winPos = 0;
static TickType_t g_runOnset = 0;
static int   g_wasRun = 0;

/* Uniform float in [lo, hi). rand() is called from sensor/telemetry/classify
 * tasks; on this port each task is a Windows thread, and MSVCRT rand() is
 * thread-safe enough for synthetic noise. */
static float frand( float lo, float hi )
{
    return lo + ( hi - lo ) * ( (float) rand() / (float) RAND_MAX );
}

static float clamp01( float v )
{
    if( v < 0.0f ) return 0.0f;
    if( v > 1.0f ) return 1.0f;
    return v;
}

/* Autonomous band for uptime second t (0/1/2). Recover phase emits rest. */
static int auto_band( uint32_t t )
{
    uint32_t tt = t % LOOP_S;
    uint32_t acc = 0;
    size_t i;
    for( i = 0; i < N_PHASES; i++ )
    {
        acc += PHASES[ i ].dwell_s;
        if( tt < acc )
        {
            return PHASES[ i ].band;
        }
    }
    return 0;
}

/* Hysteresis: enter walk @0.25 / run @0.65, leave run @0.50, leave walk @0.15.
 * Output is ever only "rest", "walk" or "run" — schema-safe by construction. */
static void hysteresis( float m, char * out )
{
    if( strcmp( out, "run" ) == 0 )
    {
        if( m < 0.50f )
        {
            strcpy( out, ( m < 0.15f ) ? "rest" : "walk" );
        }
    }
    else if( strcmp( out, "walk" ) == 0 )
    {
        if( m >= 0.65f )
        {
            strcpy( out, "run" );
        }
        else if( m < 0.15f )
        {
            strcpy( out, "rest" );
        }
    }
    else
    {
        if( m >= 0.65f )
        {
            strcpy( out, "run" );
        }
        else if( m >= 0.25f )
        {
            strcpy( out, "walk" );
        }
    }
}

/* Trailing-mean intensity -> locked exertion vocab (same cut points as the
 * Python mock's exertion_from_trailing_intensity). */
static const char * exertion_of( float mean )
{
    if( mean < 0.12f ) return "rest";
    if( mean < 0.30f ) return "light";
    if( mean < 0.55f ) return "moderate";
    return "high";
}

/* ---------------- Console / virtual-trimpot task ---------------- */
static void vConsoleTask( void * pv )
{
    ( void ) pv;
    for( ;; )
    {
        if( _kbhit() )
        {
            int c = _getch();
            if( xSemaphoreTake( g_mutex, portMAX_DELAY ) == pdTRUE )
            {
                switch( c )
                {
                    case '1':
                        g_shared.forced_band = 0;
                        snprintf( g_shared.notice, sizeof( g_shared.notice ),
                                  "MODE forced=rest (autonomous loop paused; 0=auto)" );
                        g_shared.notice_bell = 0;
                        g_shared.notice_pending = 1;
                        break;
                    case '2':
                        g_shared.forced_band = 1;
                        snprintf( g_shared.notice, sizeof( g_shared.notice ),
                                  "MODE forced=walk (autonomous loop paused; 0=auto)" );
                        g_shared.notice_bell = 0;
                        g_shared.notice_pending = 1;
                        break;
                    case '3':
                        g_shared.forced_band = 2;
                        snprintf( g_shared.notice, sizeof( g_shared.notice ),
                                  "MODE forced=run (autonomous loop paused; 0=auto)" );
                        g_shared.notice_bell = 0;
                        g_shared.notice_pending = 1;
                        break;
                    case '0':
                    case 'a':
                    case 'A':
                        g_shared.forced_band = -1;
                        snprintf( g_shared.notice, sizeof( g_shared.notice ),
                                  "MODE autonomous scenario loop (rest-walk-run-walk-recover)" );
                        g_shared.notice_bell = 0;
                        g_shared.notice_pending = 1;
                        break;
                    case 'b':
                    case 'B':
                        g_shared.fall_start = xTaskGetTickCount();
                        g_shared.fall_until = g_shared.fall_start + pdMS_TO_TICKS( FALL_DURATION_MS );
                        snprintf( g_shared.notice, sizeof( g_shared.notice ),
                                  "FALL event: motion spike 0.95 decaying ~8 s" );
                        g_shared.notice_bell = 1;   /* double console bell */
                        g_shared.notice_pending = 1;
                        break;
                    case 'h':
                    case 'H':
                    case '?':
                        g_shared.help_pending = 1;
                        break;
                    default:
                        break;
                }
                xSemaphoreGive( g_mutex );
            }
        }
        vTaskDelay( pdMS_TO_TICKS( 50 ) );
    }
}

/* ---------------- Sensor task (100 ms) ---------------- */
static void vSensorTask( void * pv )
{
    TickType_t wake = xTaskGetTickCount();
    SensorSample_t s;
    uint32_t seq = 0;
    ( void ) pv;

    for( ;; )
    {
        TickType_t now = xTaskGetTickCount();
        uint32_t t = ( uint32_t ) ( now / configTICK_RATE_HZ );
        int band;
        float m, heat;

        if( xSemaphoreTake( g_mutex, portMAX_DELAY ) == pdTRUE )
        {
            band = ( g_shared.forced_band >= 0 ) ? g_shared.forced_band : auto_band( t );
            if( now < g_shared.fall_until )
            {
                float dt = ( float ) ( now - g_shared.fall_start ) / ( float ) configTICK_RATE_HZ;
                float spike = FALL_SPIKE * expf( -dt / FALL_DECAY_S );
                float base = BAND_BASE[ band ];
                m = base + frand( -0.03f, 0.03f );
                if( spike > m )
                {
                    m = spike;
                }
            }
            else
            {
                m = BAND_BASE[ band ] + frand( -0.03f, 0.03f );
                if( band == 0 )
                {
                    m += 0.015f * sinf( 0.7f * (float) t );  /* tiny fidget */
                }
            }
            m = clamp01( m );
            heat = ( band == 2 ) ? 1.5f : ( ( band == 1 ) ? 0.6f : 0.0f );
            s.motion = m;
            s.temp_c = 24.0f + 0.4f * sinf( (float) t / 60.0f ) + heat + frand( -0.05f, 0.05f );
            s.seq = seq++;
            g_shared.latest_motion = m;
            xSemaphoreGive( g_mutex );
        }
        else
        {
            s.motion = 0.05f;
            s.temp_c = 24.0f;
            s.seq = seq++;
        }

        /* Small queue, newest wins: drop the oldest sample if full. */
        if( xQueueSend( g_sampleQ, &s, 0 ) != pdPASS )
        {
            SensorSample_t drop;
            ( void ) xQueueReceive( g_sampleQ, &drop, 0 );
            ( void ) xQueueSend( g_sampleQ, &s, 0 );
        }

        vTaskDelayUntil( &wake, pdMS_TO_TICKS( 100 ) );
    }
}

/* ---------------- Classify task (1 Hz) ---------------- */
static void vClassifyTask( void * pv )
{
    TickType_t wake = xTaskGetTickCount();
    SensorSample_t s;
    int have = 0;
    ( void ) pv;

    if( xSemaphoreTake( g_mutex, portMAX_DELAY ) == pdTRUE )
    {
        strcpy( g_shared.activity, "rest" );
        xSemaphoreGive( g_mutex );
    }

    for( ;; )
    {
        /* Drain to the newest sample; stale ones only cost lag. */
        while( xQueueReceive( g_sampleQ, &s, 0 ) == pdPASS )
        {
            have = 1;
        }
        if( have )
        {
            if( xSemaphoreTake( g_mutex, portMAX_DELAY ) == pdTRUE )
            {
                hysteresis( s.motion, g_shared.activity );
                xSemaphoreGive( g_mutex );
            }
        }
        vTaskDelayUntil( &wake, pdMS_TO_TICKS( 1000 ) );
    }
}

/* ---------------- Telemetry task (1 Hz, sole printer) ---------------- */
static void vTelemetryTask( void * pv )
{
    TickType_t wake = xTaskGetTickCount();
    ( void ) pv;

    /* Let sensor + classify settle so the first packet already has a story. */
    vTaskDelayUntil( &wake, pdMS_TO_TICKS( 1000 ) );

    for( ;; )
    {
        TickType_t now = xTaskGetTickCount();
        uint32_t uptime_s = ( uint32_t ) ( now / configTICK_RATE_HZ );
        char activity[ 8 ];
        float motion;
        char notice[ 128 ];
        int bell = 0, haveNotice = 0, wantHelp = 0;

        if( xSemaphoreTake( g_mutex, portMAX_DELAY ) == pdTRUE )
        {
            strcpy( activity, g_shared.activity );
            motion = g_shared.latest_motion;
            if( g_shared.notice_pending )
            {
                strcpy( notice, g_shared.notice );
                bell = g_shared.notice_bell;
                haveNotice = 1;
                g_shared.notice_pending = 0;
            }
            wantHelp = g_shared.help_pending;
            g_shared.help_pending = 0;
            xSemaphoreGive( g_mutex );
        }
        else
        {
            strcpy( activity, "rest" );
            motion = 0.05f;
        }

        /* ---- Physiology model (mirrors mock_streamer.py) ---- */
        float target = HR_BASELINE;
        if( strcmp( activity, "walk" ) == 0 )
        {
            target = HR_BASELINE + HR_OFFSET_WALK;
        }
        else if( strcmp( activity, "run" ) == 0 )
        {
            target = HR_BASELINE + HR_OFFSET_RUN;
        }
        {
            float tau = ( target > g_hr ) ? HR_TAU_RISE_S : HR_TAU_FALL_S;
            float alpha = 1.0f - expf( -1.0f / tau );   /* dt = 1 s */
            g_hr += ( target - g_hr ) * alpha;
        }
        int hr_out = ( int ) ( g_hr + frand( -HR_NOISE_BPM, HR_NOISE_BPM ) + 0.5f );
        if( hr_out < 30 ) hr_out = 30;
        if( hr_out > 220 ) hr_out = 220;

        if( strcmp( activity, "walk" ) == 0 )
        {
            g_steps += STEP_RATE_WALK;
        }
        else if( strcmp( activity, "run" ) == 0 )
        {
            g_steps += STEP_RATE_RUN;
        }

        g_intensityWin[ g_winPos ] = motion;
        g_winPos = ( g_winPos + 1 ) % EXERTION_WINDOW_N;
        if( g_winCount < EXERTION_WINDOW_N )
        {
            g_winCount++;
        }
        float mean = 0.0f;
        {
            float sum = 0.0f;
            int i;
            for( i = 0; i < g_winCount; i++ )
            {
                sum += g_intensityWin[ i ];
            }
            mean = ( g_winCount > 0 ) ? sum / (float) g_winCount : 0.0f;
        }
        const char * exertion = exertion_of( mean );

        int isRun = ( strcmp( activity, "run" ) == 0 );
        if( isRun && !g_wasRun )
        {
            g_runOnset = now;
        }
        g_wasRun = isRun;

        int spo2;
        if( isRun && ( ( now - g_runOnset ) / configTICK_RATE_HZ ) < SPO2_DIP_S )
        {
            spo2 = SPO2_DIP_VALUE;
        }
        else if( isRun )
        {
            spo2 = 97;
        }
        else if( strcmp( activity, "walk" ) == 0 )
        {
            spo2 = ( frand( 0.0f, 1.0f ) < 0.7f ) ? 98 : 97;
        }
        else
        {
            spo2 = ( frand( 0.0f, 1.0f ) < 0.8f ) ? 98 : 97;
        }

        const char * ppg = "good";
        {
            float r = frand( 0.0f, 1.0f );
            if( isRun && r < 0.30f )
            {
                ppg = "poor";
            }
            else if( ( strcmp( activity, "walk" ) == 0 ) && r < 0.05f )
            {
                ppg = "poor";
            }
        }

        /* ---- Funnel: every human-readable line goes out from HERE ---- */
        if( wantHelp )
        {
            Kinetiq_PrintHelp();
        }
        if( haveNotice )
        {
            fprintf( stderr, "%s[kinetiq] %s\n", bell ? "\a\a" : "", notice );
        }
        fprintf( stderr, "RTOS tick=%lu tasks=%lu uptime_s=%lu heap=%u\n",
                 ( unsigned long ) now,
                 ( unsigned long ) uxTaskGetNumberOfTasks(),
                 ( unsigned long ) uptime_s,
                 ( unsigned ) xPortGetFreeHeapSize() );
        printf( "{\"timestamp\":%ld,\"heart_rate\":%d,\"spo2\":%d,"
                "\"activity\":\"%s\",\"motion_intensity\":%.2f,"
                "\"exertion\":\"%s\",\"steps\":%d,\"ppg_quality\":\"%s\"}\n",
                EPOCH_BASE_S + (long) uptime_s, hr_out, spo2,
                activity, (double) motion,
                exertion, ( int ) g_steps, ppg );
        fflush( stdout );
        fflush( stderr );

        vTaskDelayUntil( &wake, pdMS_TO_TICKS( 1000 ) );
    }
}

/* ---------------- Public API ---------------- */

void Kinetiq_PrintHelp( void )
{
    /* Called pre-scheduler from main, or funneled via telemetry at runtime. */
    fprintf( stderr,
        "[kinetiq] keys: 1=rest band  2=walk  3=run  0/a=autonomous loop\n"
        "[kinetiq]       b=fall event (double bell + motion spike ~8 s)  h=help\n" );
    fflush( stderr );
}

void Kinetiq_CreateTasks( void )
{
    g_shared.forced_band = -1;
    g_shared.fall_start = 0;
    g_shared.fall_until = 0;
    strcpy( g_shared.activity, "rest" );
    g_shared.latest_motion = 0.05f;
    g_shared.notice_pending = 0;
    g_shared.help_pending = 0;

    srand( 42 );

    g_sampleQ = xQueueCreate( SAMPLE_QUEUE_LEN, sizeof( SensorSample_t ) );
    configASSERT( g_sampleQ != NULL );

    g_mutex = xSemaphoreCreateMutex();
    configASSERT( g_mutex != NULL );

    configASSERT( xTaskCreate( vConsoleTask, KINETIQ_NAME_CONSOLE,
                               KINETIQ_STACK_CONSOLE, NULL,
                               KINETIQ_PRIO_CONSOLE, NULL ) == pdPASS );
    configASSERT( xTaskCreate( vSensorTask, KINETIQ_NAME_SENSOR,
                               KINETIQ_STACK_SENSOR, NULL,
                               KINETIQ_PRIO_SENSOR, NULL ) == pdPASS );
    configASSERT( xTaskCreate( vClassifyTask, KINETIQ_NAME_CLASSIFY,
                               KINETIQ_STACK_CLASSIFY, NULL,
                               KINETIQ_PRIO_CLASSIFY, NULL ) == pdPASS );
    configASSERT( xTaskCreate( vTelemetryTask, KINETIQ_NAME_TELEMETRY,
                               KINETIQ_STACK_TELEMETRY, NULL,
                               KINETIQ_PRIO_TELEMETRY, NULL ) == pdPASS );
}
