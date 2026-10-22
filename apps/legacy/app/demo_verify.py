"""kubectl exec ... -- python -m app.demo_verify INCIDENT_ID 로 상태/감사 기록 확인."""
import os
import sys
import httpx

if len(sys.argv) != 2:
    raise SystemExit("Usage: python -m app.demo_verify INCIDENT_ID")
incident_id = sys.argv[1]
base = os.environ.get("CSMS_URL", "http://mock-csms:8000")
headers = {"X-Internal-Token": os.environ["INTERNAL_TOKEN"]}
with httpx.Client(timeout=8) as client:
    state = client.get(f"{base}/api/incidents/{incident_id}", headers=headers)
    state.raise_for_status()
    print("INCIDENT:", state.json())
    audit = client.get(f"{base}/api/audit", headers=headers)
    audit.raise_for_status()
    print("AUDIT (related):", [a for a in audit.json() if a["incident_id"] == incident_id])
