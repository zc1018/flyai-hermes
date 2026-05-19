import os
from pathlib import Path

os.environ["APP_PASSWORD"] = "secret"
os.environ["SESSION_SECRET"] = "test-secret"
os.environ["DATABASE_PATH"] = "data/test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app, store  # noqa: E402


class FakeHermesClient:
    def run(self, user_query):
        from app.hermes import HermesResult

        return HermesResult(
            stdout='{"summary":"完成","blocks":[{"type":"notice","title":"结果","items":["ok"]}]}',
            stderr="",
            return_code=0,
            duration_ms=12,
        )

    def run_stream(self, user_query):
        from app.hermes import HermesResult, HermesStreamEvent

        yield HermesStreamEvent(type="start", message="started", elapsed_ms=0)
        yield HermesStreamEvent(type="chunk", message="working", elapsed_ms=5)
        yield HermesStreamEvent(
            type="done",
            result=HermesResult(
                stdout='{"summary":"完成","blocks":[{"type":"notice","title":"流式结果","items":["ok"]}]}',
                stderr="",
                return_code=0,
                duration_ms=18,
            ),
            elapsed_ms=18,
        )


def test_auth_and_query(tmp_path, monkeypatch):
    test_db = tmp_path / "travel.db"
    store.database_path = Path(test_db)
    store.init()
    monkeypatch.setattr("app.main.hermes_client", FakeHermesClient())

    client = TestClient(app)

    unauthorized = client.post("/api/query", json={"query": "北京到上海航班"})
    assert unauthorized.status_code == 401

    login = client.post("/api/login", json={"password": "secret"})
    assert login.status_code == 200

    response = client.post("/api/query", json={"query": "北京到上海航班"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["blocks"][0]["title"] == "查询结论"

    stream = client.post("/api/query/stream", json={"query": "杭州西湖门票"})
    assert stream.status_code == 200
    assert "event: progress" in stream.text
    assert "event: result" in stream.text
    assert "direct-flyai fallback" not in stream.text

    history = client.get("/api/history")
    assert history.status_code == 200
    assert history.json()[0]["query"] == "杭州西湖门票"
