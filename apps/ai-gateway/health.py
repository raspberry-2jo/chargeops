from ai_gateway.app import app


@app.get("/cicd-healthz")
def cicd_healthz():
    return {
        "status": "ok",
        "service": "ai-gateway",
        "marker": "ci-cd-probe",
    }
