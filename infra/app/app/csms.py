"""CSMS 미구현 단계용 대체 서버. SQLite는 데모용이며 emptyDir 재생성 시 소실."""
from contextlib import asynccontextmanager
import hmac
import os
import sqlite3
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


def database_path():
    return os.environ.get("CSMS_DB_PATH", "/data/csms.sqlite3")


def conn():
    connection = sqlite3.connect(database_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with conn() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS incidents (
            incident_id TEXT PRIMARY KEY, device TEXT NOT NULL,
            title TEXT NOT NULL, detail TEXT NOT NULL,
            severity TEXT NOT NULL, status TEXT NOT NULL,
            approved_by TEXT, created_at TEXT NOT NULL, approved_at TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL, event TEXT NOT NULL,
            actor TEXT NOT NULL, at TEXT NOT NULL)""")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CSMS Mock", lifespan=lifespan)


class IncidentCreate(BaseModel):
    incident_id: str = Field(min_length=1, max_length=80)
    device: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=100)
    detail: str = Field(min_length=1, max_length=500)
    severity: str = Field(pattern="^(info|warning|critical)$")


class Approval(BaseModel):
    slack_user_id: str = Field(min_length=2, max_length=40)
    team_id: str = Field(min_length=2, max_length=40)


def require_token(token: str):
    if not hmac.compare_digest(token, os.environ["INTERNAL_TOKEN"]):
        raise HTTPException(status_code=401, detail="Unauthorized backend caller")


def now():
    return datetime.now(timezone.utc).isoformat()


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.post("/api/incidents", status_code=201)
def create_incident(payload: IncidentCreate, x_internal_token: str = Header(default="")):
    require_token(x_internal_token)
    with conn() as db:
        try:
            db.execute("""INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (payload.incident_id, payload.device, payload.title, payload.detail,
                        payload.severity, "PENDING", None, now(), None))
            db.execute("INSERT INTO audit(incident_id, event, actor, at) VALUES (?, ?, ?, ?)",
                       (payload.incident_id, "CREATED", "alert-router", now()))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Duplicate incident") from exc
    return {"incident_id": payload.incident_id, "status": "PENDING"}


def select_incident(db, incident_id: str):
    row = db.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return dict(row)


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str, x_internal_token: str = Header(default="")):
    require_token(x_internal_token)
    with conn() as db:
        return select_incident(db, incident_id)


@app.post("/api/incidents/{incident_id}/approve")
def approve(incident_id: str, payload: Approval, x_internal_token: str = Header(default="")):
    require_token(x_internal_token)
    approved_ids = {s.strip() for s in os.environ.get("ALLOWED_APPROVER_IDS", "").split(",") if s.strip()}
    if payload.slack_user_id not in approved_ids:
        raise HTTPException(status_code=403, detail="Approver not allowlisted")
    expected_team = os.environ["SLACK_TEAM_ID"]
    if not hmac.compare_digest(payload.team_id, expected_team):
        raise HTTPException(status_code=403, detail="Wrong Slack workspace")
    with conn() as db:
        db.execute("BEGIN IMMEDIATE")
        row = select_incident(db, incident_id)
        if row["status"] == "APPROVED":
            return {"incident_id": incident_id, "status": "APPROVED", "approved_by": row["approved_by"], "idempotent": True}
        db.execute("""UPDATE incidents SET status = 'APPROVED', approved_by = ?, approved_at = ?
                      WHERE incident_id = ? AND status = 'PENDING'""",
                   (payload.slack_user_id, now(), incident_id))
        db.execute("INSERT INTO audit(incident_id, event, actor, at) VALUES (?, ?, ?, ?)",
                   (incident_id, "APPROVED", payload.slack_user_id, now()))
    return {"incident_id": incident_id, "status": "APPROVED", "approved_by": payload.slack_user_id, "idempotent": False}


@app.get("/api/audit")
def audit(x_internal_token: str = Header(default="")):
    require_token(x_internal_token)
    with conn() as db:
        return [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY audit_id DESC LIMIT 50")]
