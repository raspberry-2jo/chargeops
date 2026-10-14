"""kubectl exec deploy/chatops-bot -- python -m app.demo_emit"""
import os
import httpx

payload = {
    "device": "esp32-lab-01",
    "title": "장비 온도 임계치 초과 (훈련)",
    "detail": "시뮬레이션: 센서 87°C > 임계치 75°C. 실제 장비 제어 없음.",
    "severity": "critical",
}
url = os.environ.get("ROUTER_URL", "http://alert-router:8080")
with httpx.Client(timeout=15.0) as client:
    response = client.post(f"{url}/alerts", json=payload, headers={"X-Trigger-Key": os.environ["TRIGGER_KEY"]})
    response.raise_for_status()
    print("TEST ALERT SENT:", response.json())
