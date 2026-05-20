import os
import sqlite3
from pathlib import Path

os.environ["APP_PASSWORD"] = "secret"
os.environ["OWNER_PASSWORD"] = "secret"
os.environ["SESSION_SECRET"] = "test-secret"
os.environ["DATABASE_PATH"] = "data/test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app, query_runtime, settings, store  # noqa: E402


class FakeHermesClient:
    def run(self, user_query, timeout_seconds=None):
        from app.hermes import HermesResult

        return HermesResult(
            stdout='{"summary":"完成","blocks":[{"type":"notice","title":"结果","items":["ok"]}]}',
            stderr="",
            return_code=0,
            duration_ms=12,
        )

    def run_stream(self, user_query, timeout_seconds=None):
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


class FakeRoundTripHermesClient:
    def run(self, user_query, timeout_seconds=None):
        from app.hermes import HermesResult

        return HermesResult(
            stdout=(
                "推荐组合\n"
                "- 去程：6月19日 IJ018 春秋日本航空 10:45-15:30 ¥1,474\n"
                "- 回程：6月25日 IJ017 春秋日本航空 17:55-21:15 ¥1,354"
            ),
            stderr="",
            return_code=0,
            duration_ms=12,
        )

    def run_stream(self, user_query, timeout_seconds=None):
        from app.hermes import HermesResult, HermesStreamEvent

        yield HermesStreamEvent(type="done", result=self.run(user_query, timeout_seconds), elapsed_ms=12)


class FakeBrokenHermesClient:
    failure = "Failed to initialize agent: AIAgent.__init__() got an unexpected keyword argument 'openrouter_min_coding_score'"

    def run(self, user_query, timeout_seconds=None):
        from app.hermes import HermesResult

        return HermesResult(stdout=f"{self.failure}\n\nGoodbye! ⚕", stderr="", return_code=0, duration_ms=9)

    def run_stream(self, user_query, timeout_seconds=None):
        from app.hermes import HermesStreamEvent

        yield HermesStreamEvent(type="done", result=self.run(user_query, timeout_seconds), elapsed_ms=9)


class FakeXhsClient:
    def should_attempt(self, user):
        return True

    def search(self, user_query, user):
        import time

        from app.xhs import XhsSearchResult

        time.sleep(0.02)
        return XhsSearchResult(
            status="ok",
            keyword="东京 旅行攻略 避坑",
            blocks=[
                {
                    "type": "xhs_post_card",
                    "title": "东京高互动攻略",
                    "summary": "住宿区域、交通和餐厅避坑。",
                    "postUrl": "https://www.xiaohongshu.com/explore/test",
                    "likedCount": 1000,
                    "collectedCount": 500,
                    "commentCount": 80,
                    "source": "小红书",
                    "supplement": "xhs",
                }
            ],
        )

    def stats(self):
        return {"enabled": True, "running": 0, "today_calls": 1, "cache": {"entries": 0}}

    def health(self):
        return {"enabled": True}


class FakeTimeoutXhsClient(FakeXhsClient):
    def search(self, user_query, user):
        from app.xhs import XhsSearchResult

        return XhsSearchResult(
            status="timeout",
            keyword="杭州 旅行攻略 避坑",
            blocks=[
                {
                    "type": "notice",
                    "title": "小红书灵感超时",
                    "items": ["小红书补充查询超过时间上限，主查询结果已先展示。"],
                }
            ],
        )


def _reset_app(tmp_path, monkeypatch):
    test_db = tmp_path / "travel.db"
    store.database_path = Path(test_db)
    store.init(settings.owner_password)
    settings.xhs_enabled = False
    settings.xhs_cookies = ""
    settings.xhs_timeout_seconds = 45
    settings.xhs_max_results = 6
    settings.xhs_max_daily_per_user = 10
    query_runtime._active_by_user.clear()
    monkeypatch.setattr("app.main.hermes_client", FakeHermesClient())
    monkeypatch.setattr("app.main.xhs_client", type("DisabledXhsClient", (), {"should_attempt": lambda self, user: False, "stats": lambda self: {}, "health": lambda self: {}})())
    return TestClient(app)


def test_owner_auth_query_admin_and_health(tmp_path, monkeypatch):
    client = _reset_app(tmp_path, monkeypatch)

    unauthorized = client.post("/api/query", json={"query": "北京到上海航班"})
    assert unauthorized.status_code == 401

    login = client.post("/api/login", json={"password": "secret"})
    assert login.status_code == 200

    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["role"] == "owner"
    assert me.json()["quota"]["unlimited"] is True

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

    users = client.get("/api/admin/users")
    assert users.status_code == 200
    assert users.json()[0]["role"] == "owner"

    health = client.get("/api/health")
    assert health.status_code == 200
    assert "hermes_bin" not in health.json()


def test_query_and_history_use_query_aware_round_trip_normalization(tmp_path, monkeypatch):
    client = _reset_app(tmp_path, monkeypatch)
    monkeypatch.setattr("app.main.hermes_client", FakeRoundTripHermesClient())
    assert client.post("/api/login", json={"password": "secret"}).status_code == 200

    response = client.post("/api/query", json={"query": "北京东京往返机票"})

    assert response.status_code == 200
    data = response.json()
    assert data["blocks"][0]["type"] == "flight_card"
    assert data["blocks"][0]["number"] == "IJ018 / IJ017"
    assert len(data["blocks"][0]["segments"]) == 2

    history = client.get("/api/history").json()
    assert history[0]["blocks"][0]["number"] == "IJ018 / IJ017"
    assert len(history[0]["blocks"][0]["segments"]) == 2


def test_hermes_initialization_failure_is_not_stored_as_success(tmp_path, monkeypatch):
    client = _reset_app(tmp_path, monkeypatch)
    monkeypatch.setattr("app.main.hermes_client", FakeBrokenHermesClient())
    assert client.post("/api/login", json={"password": "secret"}).status_code == 200

    response = client.post("/api/query", json={"query": "北京东京往返机票"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["blocks"][0]["title"] == "查询失败"
    assert "Hermes Agent 启动失败" in data["blocks"][0]["items"][0]

    history = client.get("/api/history").json()
    assert history[0]["status"] == "error"
    assert history[0]["blocks"][0]["title"] == "查询失败"


def test_stream_can_append_xhs_supplement_and_preserve_history(tmp_path, monkeypatch):
    client = _reset_app(tmp_path, monkeypatch)
    monkeypatch.setattr("app.main.xhs_client", FakeXhsClient())
    assert client.post("/api/login", json={"password": "secret"}).status_code == 200

    stream = client.post("/api/query/stream", json={"query": "北京东京往返机票，顺便看看东京攻略"})

    assert stream.status_code == 200
    assert "event: result" in stream.text
    assert "event: supplement" in stream.text
    assert "东京高互动攻略" in stream.text

    history = client.get("/api/history").json()
    assert any(block["type"] == "xhs_post_card" for block in history[0]["blocks"])


def test_stream_suppresses_failed_xhs_supplement_noise(tmp_path, monkeypatch):
    client = _reset_app(tmp_path, monkeypatch)
    monkeypatch.setattr("app.main.xhs_client", FakeTimeoutXhsClient())
    assert client.post("/api/login", json={"password": "secret"}).status_code == 200

    stream = client.post("/api/query/stream", json={"query": "杭州西湖门票，顺便看看攻略"})

    assert stream.status_code == 200
    assert "event: result" in stream.text
    assert "event: supplement" not in stream.text
    assert "小红书灵感超时" not in stream.text
    history = client.get("/api/history").json()
    assert all(block["type"] != "xhs_post_card" for block in history[0]["blocks"])


def test_conversation_clarifies_confirms_and_runs_search(tmp_path, monkeypatch):
    client = _reset_app(tmp_path, monkeypatch)
    assert client.post("/api/login", json={"password": "secret"}).status_code == 200

    created = client.post("/api/conversations", json={"title": "测试旅行"})
    assert created.status_code == 200
    conversation_id = created.json()["id"]

    clarify = client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        json={"message": "想暑假出国玩几天"},
    )
    assert clarify.status_code == 200
    assert "event: message" in clarify.text
    assert "还差" in clarify.text
    assert store.count_queries_today(created.json()["user_id"]) == 0

    confirm = client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        json={"message": "上海出发，暑假夫妻两个人出国游 7 天，希望安全、直飞、价格友好"},
    )
    assert confirm.status_code == 200
    assert "search_confirmation" in confirm.text
    assert "search_query" in confirm.text
    assert store.count_queries_today(created.json()["user_id"]) == 0

    search = client.post(f"/api/conversations/{conversation_id}/search/stream", json={})
    assert search.status_code == 200
    assert "event: result" in search.text

    detail = client.get(f"/api/conversations/{conversation_id}").json()
    assert detail["status"] == "result"
    assert any(message["message_type"] == "search_result" for message in detail["messages"])
    assert client.get("/api/history").json()[0]["query"].startswith("请基于以下多轮旅行需求")


def test_conversation_running_search_is_rejected(tmp_path, monkeypatch):
    client = _reset_app(tmp_path, monkeypatch)
    assert client.post("/api/login", json={"password": "secret"}).status_code == 200
    created = client.post("/api/conversations", json={"title": "测试旅行"}).json()
    store.update_conversation(created["id"], {"status": "running"}, include_all=True)

    response = client.post(f"/api/conversations/{created['id']}/search/stream", json={"query": "北京东京往返机票"})

    assert response.status_code == 409
    assert "正在查询中" in response.text


def test_conversation_history_is_user_scoped(tmp_path, monkeypatch):
    owner = _reset_app(tmp_path, monkeypatch)
    assert owner.post("/api/login", json={"password": "secret"}).status_code == 200
    conversation = owner.post("/api/conversations", json={"title": "Owner 私有旅行"}).json()
    created = owner.post("/api/admin/users", json={"label": "朋友", "password": "friend-secret"})
    assert created.status_code == 200

    friend = TestClient(app)
    assert friend.post("/api/login", json={"password": "friend-secret"}).status_code == 200
    assert friend.get(f"/api/conversations/{conversation['id']}").status_code == 404
    assert all(item["title"] != "Owner 私有旅行" for item in friend.get("/api/conversations").json())


def test_owner_can_update_xhs_config_without_exposing_cookie(tmp_path, monkeypatch):
    client = _reset_app(tmp_path, monkeypatch)
    monkeypatch.setattr("app.main.ENV_FILE", tmp_path / ".env")
    assert client.post("/api/login", json={"password": "secret"}).status_code == 200

    rejected = client.post(
        "/api/admin/xhs-config",
        json={"enabled": True, "timeout_seconds": 45, "max_results": 6, "max_daily_per_user": 10},
    )
    assert rejected.status_code == 400

    invalid_cookie = client.post(
        "/api/admin/xhs-config",
        json={
            "enabled": True,
            "cookies": "a1=abc; id_token=def",
            "timeout_seconds": 45,
            "max_results": 6,
            "max_daily_per_user": 10,
        },
    )
    assert invalid_cookie.status_code == 400
    assert "web_session" in invalid_cookie.text

    response = client.post(
        "/api/admin/xhs-config",
        json={
            "enabled": True,
            "cookies": "a=b; web_session=secret",
            "timeout_seconds": 30,
            "max_results": 4,
            "max_daily_per_user": 3,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["cookie_configured"] is True
    assert "web_session=secret" not in response.text

    config = client.get("/api/admin/xhs-config")
    assert config.status_code == 200
    assert "web_session=secret" not in config.text
    env_text = (tmp_path / ".env").read_text()
    assert "XHS_ENABLED=true" in env_text


def test_friend_user_quota_history_and_admin_forbidden(tmp_path, monkeypatch):
    owner = _reset_app(tmp_path, monkeypatch)
    assert owner.post("/api/login", json={"password": "secret"}).status_code == 200

    created = owner.post(
        "/api/admin/users",
        json={
            "label": "朋友A",
            "password": "friend-secret",
            "daily_limit": 1,
            "max_concurrent": 1,
            "timeout_seconds": 120,
            "can_view_history": True,
        },
    )
    assert created.status_code == 200

    friend = TestClient(app)
    assert friend.post("/api/login", json={"password": "friend-secret"}).status_code == 200
    assert friend.get("/admin").status_code == 200
    assert friend.get("/api/admin/users").status_code == 403

    response = friend.post("/api/query/stream", json={"query": "东京七日游"})
    assert response.status_code == 200

    exhausted = friend.post("/api/query/stream", json={"query": "大阪七日游"})
    assert exhausted.status_code == 429
    assert "额度已用完" in exhausted.text

    friend_history = friend.get("/api/history").json()
    assert len(friend_history) == 1
    assert friend_history[0]["query"] == "东京七日游"

    owner_history = owner.get("/api/history").json()
    assert any(item["query"] == "东京七日游" for item in owner_history)

    assert friend.post("/api/logout").status_code == 200
    assert friend.get("/api/me").status_code == 401


def test_disabled_user_cannot_login_and_long_query_rejected(tmp_path, monkeypatch):
    owner = _reset_app(tmp_path, monkeypatch)
    assert owner.post("/api/login", json={"password": "secret"}).status_code == 200
    created = owner.post(
        "/api/admin/users",
        json={"label": "朋友B", "password": "friend-secret", "enabled": False},
    )
    assert created.status_code == 200

    disabled = TestClient(app)
    assert disabled.post("/api/login", json={"password": "friend-secret"}).status_code == 401

    owner.patch(f"/api/admin/users/{created.json()['id']}", json={"enabled": True})
    assert disabled.post("/api/login", json={"password": "friend-secret"}).status_code == 200
    too_long = disabled.post("/api/query/stream", json={"query": "旅行" * 260})
    assert too_long.status_code == 400
    assert "500 字以内" in too_long.text


def test_admin_cannot_create_duplicate_password(tmp_path, monkeypatch):
    owner = _reset_app(tmp_path, monkeypatch)
    assert owner.post("/api/login", json={"password": "secret"}).status_code == 200
    first = owner.post("/api/admin/users", json={"label": "朋友C", "password": "friend-secret"})
    assert first.status_code == 200
    duplicate = owner.post("/api/admin/users", json={"label": "朋友D", "password": "friend-secret"})
    assert duplicate.status_code == 400
    assert "已经被使用" in duplicate.text


def test_migrates_legacy_queries_without_user_id(tmp_path):
    legacy_db = tmp_path / "legacy.db"
    with sqlite3.connect(legacy_db) as conn:
        conn.execute(
            """
            CREATE TABLE queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                status TEXT NOT NULL,
                blocks_json TEXT NOT NULL,
                raw_output TEXT NOT NULL,
                stderr TEXT NOT NULL DEFAULT '',
                duration_ms INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )

    from app.storage import QueryStore

    legacy_store = QueryStore(legacy_db)
    legacy_store.init("owner-secret")

    with sqlite3.connect(legacy_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(queries)").fetchall()}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(queries)").fetchall()}

    assert "user_id" in columns
    assert "idx_queries_user_created_at" in indexes
