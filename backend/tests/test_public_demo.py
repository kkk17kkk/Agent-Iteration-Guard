from fastapi.testclient import TestClient

from agentguard.api import app


def test_public_demo_is_read_only_and_serves_lighttable_report(monkeypatch) -> None:
    monkeypatch.setenv("AIG_DEMO_READ_ONLY", "true")
    client = TestClient(app)

    report = client.get("/api/v1/demo/reports/lighttable")
    assert report.status_code == 200
    assert report.json()["report"]["subject"]["product_id"] == "lighttable-stage7"

    rendered = client.get("/api/v1/demo/reports/lighttable/export?format=html")
    assert rendered.status_code == 200
    assert "Agent Iteration Guard" in rendered.text

    blocked = client.post("/api/v1/products", json={"name": "public-demo-write", "description": ""})
    assert blocked.status_code == 403
