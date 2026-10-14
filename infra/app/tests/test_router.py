from app.router import AlertIn, alert_blocks


def test_blocks_have_query_approval_and_confirm():
    alert = AlertIn(device="esp32-lab", title="temp high", detail="simulated", severity="critical")
    blocks = alert_blocks("incident-1", alert)
    actions = [x for x in blocks if x["type"] == "actions"][0]["elements"]
    assert {a["action_id"] for a in actions} == {"incident_view", "incident_approve"}
    assert all(a["value"] == "incident-1" for a in actions)
    assert "confirm" in actions[1]
