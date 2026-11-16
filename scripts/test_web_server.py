import subprocess
import time
import urllib.request
import urllib.error
import json
import sys

def main():
    print("Starting app.py server on port 8777...")
    proc = subprocess.Popen([sys.executable, "laptop/web/app.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(2)

    base = "http://127.0.0.1:8777"
    tests_passed = 0

    def check_get(url, expected_status=200, content_substr=None, check_json=False):
        nonlocal tests_passed
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                body = resp.read().decode("utf-8")
                ct = resp.headers.get("Content-Type", "")
                assert status == expected_status, f"{url} status {status} != {expected_status}"
                if content_substr:
                    assert content_substr in body, f"{url} body missing {content_substr}"
                if check_json:
                    data = json.loads(body)
                print(f"PASS [GET]: {url} -> {status} ({ct})")
                tests_passed += 1
        except urllib.error.HTTPError as e:
            if e.code == expected_status:
                print(f"PASS [GET] (expected HTTP {e.code}): {url}")
                tests_passed += 1
            else:
                raise

    def check_post(url, payload, expected_status=200):
        nonlocal tests_passed
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                body = resp.read().decode("utf-8")
                ct = resp.headers.get("Content-Type", "")
                assert status == expected_status, f"{url} status {status} != {expected_status}"
                data_json = json.loads(body)
                print(f"PASS [POST]: {url} -> {status} ({ct})")
                tests_passed += 1
        except urllib.error.HTTPError as e:
            if e.code == expected_status:
                print(f"PASS [POST] (expected HTTP {e.code}): {url}")
                tests_passed += 1
            else:
                raise

    try:
        check_get(f"{base}/", 200, '<link rel="stylesheet" href="/static/css/style.css">')
        check_get(f"{base}/index.html", 200, '<script src="/static/js/app.js"></script>')
        check_get(f"{base}/static/css/style.css", 200, "--accent: #38bdf8")
        check_get(f"{base}/static/js/app.js", 200, "function sizeSpark")
        check_get(f"{base}/style.css", 200, "--accent: #38bdf8")
        check_get(f"{base}/app.js", 200, "function sizeSpark")
        check_get(f"{base}/api/telemetry", 200, check_json=True)
        check_get(f"{base}/api/mode", 200, check_json=True)
        check_get(f"{base}/api/emotion", 200, check_json=True)
        check_get(f"{base}/non_existent", 404)

        # POST tests
        check_post(f"{base}/api/mode", {"mode": "band"}, 200)
        check_post(f"{base}/api/emotion", {"person": False}, 200)
        check_post(f"{base}/api/camera_ingest", {"motion": 0.5}, 200)

        print(f"\nALL {tests_passed} TESTS PASSED SUCCESSFULLY!")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

if __name__ == "__main__":
    main()
