import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time

from app.storage import QueryStore
from app.xhs import (
    XhsClient,
    build_xhs_blocks,
    extract_xhs_keyword,
    normalize_xhs_cookie_input,
    parse_cookie_names,
    parse_mediacrawler_outputs,
    xhs_cookie_has_required_session,
)


def test_extract_xhs_keyword_prefers_destination_city():
    keyword = extract_xhs_keyword("北京东京往返机票，停留 5 晚，顺便找东京好逛的区域")

    assert keyword == "东京 旅行攻略 避坑"


def test_parse_mediacrawler_outputs_and_rank_cards(tmp_path):
    data_dir = tmp_path / "data" / "xhs"
    data_dir.mkdir(parents=True)
    records = [
        {
            "note_id": "low",
            "title": "普通攻略",
            "desc": "路线参考",
            "liked_count": "10",
            "collected_count": "5",
            "comment_count": "1",
        },
        {
            "note_id": "hot",
            "title": "东京亲测避坑",
            "desc": "交通、住宿和餐厅都整理好了",
            "liked_count": "100",
            "collected_count": "80",
            "comment_count": "20",
            "nickname": "旅行作者",
            "image_list": [{"url": "https://example.com/cover.jpg"}],
        },
    ]
    (data_dir / "xhs.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records), encoding="utf-8")

    posts = parse_mediacrawler_outputs(tmp_path)
    blocks = build_xhs_blocks(posts, max_results=1)

    assert blocks[0]["type"] == "xhs_post_card"
    assert blocks[0]["title"] == "东京亲测避坑"
    assert blocks[0]["author"] == "旅行作者"
    assert blocks[0]["likedCount"] == 100
    assert blocks[0]["postUrl"] == "https://www.xiaohongshu.com/explore/hot"
    assert blocks[0]["imageUrl"] == "https://example.com/cover.jpg"


def test_xhs_cache_roundtrip(tmp_path):
    store = QueryStore(tmp_path / "travel.db")
    store.init("owner-secret")
    blocks = [{"type": "xhs_post_card", "title": "东京攻略", "supplement": "xhs"}]

    store.upsert_xhs_cache("cache-key", "东京 旅行攻略", blocks)

    assert store.get_xhs_cache("cache-key", max_age_seconds=60) == blocks
    assert store.xhs_cache_stats()["entries"] == 1


def test_xhs_cookie_requires_web_session():
    cookie = "a1=abc; id_token=def; web_session=session-value"

    assert parse_cookie_names(cookie) == {"a1", "id_token", "web_session"}
    assert xhs_cookie_has_required_session(cookie) is True
    assert xhs_cookie_has_required_session("a1=abc; id_token=def") is False


def test_normalize_xhs_cookie_export_table():
    exported = "\n".join(
        [
            "a1\tabc\t.xiaohongshu.com\t/\t2027-01-01T00:00:00.000Z",
            "web_session\tsession-value\t.xiaohongshu.com\t/\tSession",
            "id_token=token-value; gid=gid-value",
        ]
    )

    normalized = normalize_xhs_cookie_input(exported)

    assert "a1=abc" in normalized
    assert "web_session=session-value" in normalized
    assert "id_token=token-value" in normalized
    assert xhs_cookie_has_required_session(exported) is True


def test_xhs_waits_for_running_task_instead_of_returning_busy(tmp_path, monkeypatch):
    store = QueryStore(tmp_path / "travel.db")
    store.init("owner-secret")
    client = XhsClient(
        SimpleNamespace(
            xhs_enabled=True,
            xhs_login_type="cookie",
            xhs_cookies="web_session=session-value",
            xhs_cache_ttl_hours=12,
            xhs_timeout_seconds=1,
            xhs_max_results=3,
            xhs_max_daily_per_user=10,
            mediacrawler_dir=tmp_path,
        ),
        store,
    )
    monkeypatch.setattr(client, "_readiness_error", lambda: None)
    monkeypatch.setattr(
        client,
        "_run_mediacrawler",
        lambda keyword: [
            {
                "note_id": "queued-ok",
                "title": f"{keyword} 排队后成功",
                "desc": "等待已有任务结束后继续查询。",
                "liked_count": 88,
            }
        ],
    )

    assert client._semaphore.acquire(timeout=0.1) is True
    results = []
    worker = threading.Thread(target=lambda: results.append(client.search("东京旅行攻略", {"id": 1, "role": "owner"})))
    worker.start()
    time.sleep(0.05)

    assert client.stats()["queued"] == 1
    client._semaphore.release()
    worker.join(timeout=2)

    assert len(results) == 1
    assert results[0].status == "ok"
    assert results[0].blocks[0]["type"] == "xhs_post_card"
