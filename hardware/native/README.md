# KINETIQ native device node — real FreeRTOS v11 kernel on Windows

The wearable node, simulated natively: the **real FreeRTOS v11 kernel**
(`freertos-kernel/`, heap_4, MSVC-MingW simulator port) runs as a Windows
process, no internet, no Wokwi, no accounts. Four device tasks mirror the
Wokwi sketch architecture — virtual IMU + DHT22, hysteresis classifier,
1 Hz physiology telemetry — with a console "virtual trimpot" for live demo
control.

## Build

```powershell
# from the repository root (or anywhere — the script finds itself + gcc)
powershell -ExecutionPolicy Bypass -File hardware\native\build.ps1
```

Needs `gcc` on PATH (WinLibs via winget); the script also searches the
WinGet `BrechtSanders.WinLibs` package dir and `C:\mingw64`. Output:
`hardware/native/kinetiq_native.exe`. The kernel source
(`freertos-kernel/`) is **not** committed — see `.gitignore`.

## Run

```powershell
cd hardware\native
.\kinetiq_native.exe
```

With no keys pressed the node runs the autonomous scenario loop
(rest → walk → run → walk → recover, ~67 s), same dwell spirit as the
Python mock (`laptop/receiver/mock_streamer.py`).

Stream split (deliberate): **stdout = schema JSON telemetry only** (1 Hz),
**stderr = `RTOS tick=…` status + key notices**. That keeps the laptop pipe
clean — the dashboard counts zero dropped frames.

## Key controls

`1` rest band · `2` walk · `3` run · `0`/`a` back to autonomous loop ·
`b` fall event (double console bell + 0.95 motion spike decaying ~8 s) ·
`h` help banner.

## Showpiece demo (live pipe into the laptop dashboard)

```powershell
# from hardware\native
.\kinetiq_native.exe | ..\..\laptop\.venv\Scripts\python.exe ..\..\laptop\receiver\dashboard.py --source stdin --headless
```

(Note: the venv lives at `laptop\.venv`, not the repo root.)
Press `3` to force a run — watch HR climb with lag, SpO2 dip, PPG go poor;
press `b` for the fall bell. Ctrl+C stops both ends.

## Files

| File | What |
|---|---|
| `FreeRTOSConfig.h` | Port config cribbed from the official WIN32-MingW demo (tick 1000 Hz, 8 priorities, heap_4, dynamic allocation) |
| `kinetiq_tasks.h` / `.c` | The four device tasks + queue + mutex + physiology model |
| `main.c` | Port entry: banner, task creation, `vTaskStartScheduler()` |
| `build.ps1` | gcc locator + one-line kernel+app build |
| `.gitignore` | Keeps `freertos-kernel/`, `*.exe`, `*.o` out of git |

## Honesty notes (for the stage)

- The simulator port runs each FreeRTOS task as a Windows thread; timing is
  best-effort — Windows is not an RTOS, so the 100 ms / 1 Hz cadences jitter
  by milliseconds under host load. The kernel semantics (preemption,
  queues, mutexes, tick count) are the real FreeRTOS implementation.
- Console keys are polled every ~50 ms (`_kbhit`), so a fast tap can be
  missed; hold the key half a beat if the notice does not appear.
- Physiology is synthetic-but-principled (HR lag τ≈7 s rise / 15 s fall,
  trailing-30 s exertion, SpO2 dip at run onset) — same model as the Python
  mock, not a medical measurement.
