import subprocess
import time
import urllib.request
import json
import sys

proc = subprocess.Popen([sys.executable, 'laptop/web/app.py'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    time.sleep(2)
    base = 'http://127.0.0.1:8777'

    # Check /
    with urllib.request.urlopen(f'{base}/', timeout=3) as r:
        html = r.read().decode('utf-8')
        assert 'KinetiQ — Smart Mirror / Home Edition' in html, 'Missing Home Edition title'
        print('PROBE PASS: GET / -> Home Edition UI loaded')

    # Check /classic
    with urllib.request.urlopen(f'{base}/classic', timeout=3) as r:
        html_classic = r.read().decode('utf-8')
        assert 'KINETIQ Mirror' in html_classic or 'KinetiQ' in html_classic, 'Missing Classic title'
        print('PROBE PASS: GET /classic -> Classic UI preserved')

    # Check /api/telemetry
    with urllib.request.urlopen(f'{base}/api/telemetry', timeout=3) as r:
        tdata = json.loads(r.read().decode('utf-8'))
        assert 'band_meta' in tdata, 'Missing band_meta in telemetry'
        assert 'rtos_diag' in tdata, 'Missing rtos_diag in telemetry'
        print('PROBE PASS: GET /api/telemetry -> band_meta & rtos_diag exposed')

    # Check /api/freshness_mode
    req = urllib.request.Request(f'{base}/api/freshness_mode', data=json.dumps({'fast': True}).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=3) as r:
        fm = json.loads(r.read().decode('utf-8'))
        assert fm.get('freshness', {}).get('fast_mode') is True, 'Fast freshness mode not enabled'
        print('PROBE PASS: POST /api/freshness_mode -> fast_mode: True')

    # Check /api/rtos/command
    req = urllib.request.Request(f'{base}/api/rtos/command', data=json.dumps({'cmd': 'walk'}).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=3) as r:
        rc = json.loads(r.read().decode('utf-8'))
        assert rc.get('override_mode') == 'walk', f'RTOS override failed: {rc}'
        print('PROBE PASS: POST /api/rtos/command -> override_mode: walk')

    # Check /api/ask with self_test_interlock
    req = urllib.request.Request(f'{base}/api/ask', data=json.dumps({'self_test_interlock': True, 'tts': False}).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        ask_res = json.loads(r.read().decode('utf-8'))
        meta = ask_res.get('meta', {})
        assert meta.get('self_healed') is True, 'Interlock self-healing did not trigger'
        assert meta.get('drift_intercepted') is True, 'Drift was not intercepted'
        print(f"PROBE PASS: POST /api/ask (Self-Test Interlock) -> Grounding: {ask_res.get('grounding')}, Self-Healed: {meta.get('self_healed')}")

    print('\nALL PROBES PASSED 100% PERFECTLY!')
finally:
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except Exception:
        proc.kill()
