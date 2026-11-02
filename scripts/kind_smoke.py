import re
import subprocess
import sys
import time

import httpx

CONTEXT = "kind-aml"
LOCAL_PORT = 18000
BASE_URL = f"http://127.0.0.1:{LOCAL_PORT}"
MAX_ATTEMPTS = 20
UNINVESTIGATED = re.compile(r'<a href="/alerts/(\d+)">\d+</a></td>\s*(?:<td>[^<]*</td>\s*){3}<td>none</td>')
DECISION_FORM = re.compile(r'action="/investigations/(\d+)/decision"')


def wait_ready(client: httpx.Client) -> None:
    for _ in range(30):
        try:
            if client.get("/readyz").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(1)
    raise SystemExit("api never became ready through the port-forward")


def check(response: httpx.Response, status: int, step: str) -> httpx.Response:
    if response.status_code != status:
        raise SystemExit(f"{step}: expected {status}, got {response.status_code}")
    print(f"ok  {step}")
    return response


def smoke(client: httpx.Client) -> None:
    wait_ready(client)
    check(client.get("/healthz"), 200, "GET /healthz")
    check(client.get("/"), 200, "GET / (analyst queue)")
    alerts = check(client.get("/alerts"), 200, "GET /alerts").text
    for alert_id in UNINVESTIGATED.findall(alerts)[:MAX_ATTEMPTS]:
        check(client.post(f"/alerts/{alert_id}/investigate"), 303, f"POST /alerts/{alert_id}/investigate")
        page = check(client.get(f"/alerts/{alert_id}"), 200, f"GET /alerts/{alert_id}").text
        form = DECISION_FORM.search(page)
        if form is None:
            print(f"    alert {alert_id} was auto-closed; trying the next one for an analyst decision")
            continue
        decision = {"final_decision": "escalate", "comment": "kind smoke test"}
        check(client.post(f"/investigations/{form[1]}/decision", data=decision), 303, "POST decision")
        decided = check(client.get(f"/alerts/{alert_id}"), 200, f"GET /alerts/{alert_id} after decision").text
        if "Final decision</dt>" not in decided:
            raise SystemExit("the decision is not shown on the alert page")
        print(f"smoke test passed: alert {alert_id}, investigation {form[1]}")
        return
    raise SystemExit("no uninvestigated alert reached the analyst queue")


def main() -> None:
    forward = subprocess.Popen(  # noqa: S603
        ["kubectl", "--context", CONTEXT, "port-forward", "svc/aml-api", f"{LOCAL_PORT}:8000"],  # noqa: S607
        stdout=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(base_url=BASE_URL, timeout=120) as client:
            smoke(client)
    finally:
        forward.terminate()
        forward.wait()


if __name__ == "__main__":
    sys.exit(main())
