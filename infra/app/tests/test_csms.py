from fastapi.testclient import TestClient
from app.csms import app


def test_mock_csm_approval_idempotency_and_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("CSMS_DB_PATH", str(tmp_path / "csms.sqlite3"))
    monkeypatch.setenv("INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setenv("ALLOWED_APPROVER_IDS", "UAPPROVER")
    monkeypatch.setenv("SLACK_TEAM_ID", "TTEST")
    headers = {"X-Internal-Token": "test-internal-token"}
    with TestClient(app) as api:
        data = {"incident_id": "test-001", "device": "esp32", "title": "overheat", "detail": "simulated", "severity": "critical"}
        assert api.post("/api/incidents", json=data, headers=headers).status_code == 201
        assert api.get("/api/incidents/test-001").status_code == 401
        assert api.get("/api/incidents/test-001", headers=headers).json()["status"] == "PENDING"
        denied = api.post("/api/incidents/test-001/approve", headers=headers, json={"slack_user_id": "UNAUTHORIZED", "team_id": "TTEST"})
        assert denied.status_code == 403
        wrong_team = api.post("/api/incidents/test-001/approve", headers=headers, json={"slack_user_id": "UAPPROVER", "team_id": "TOTHER"})
        assert wrong_team.status_code == 403
        ok = api.post("/api/incidents/test-001/approve", headers=headers, json={"slack_user_id": "UAPPROVER", "team_id": "TTEST"})
        assert ok.status_code == 200 and not ok.json()["idempotent"]
        again = api.post("/api/incidents/test-001/approve", headers=headers, json={"slack_user_id": "UAPPROVER", "team_id": "TTEST"})
        assert again.status_code == 200 and again.json()["idempotent"]
        audit = api.get("/api/audit", headers=headers).json()
        assert [a["event"] for a in audit] == ["APPROVED", "CREATED"]
