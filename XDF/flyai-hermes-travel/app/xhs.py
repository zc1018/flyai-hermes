from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import Settings
from .storage import QueryStore


XHS_EVENT_TYPE = "xhs_search"
XHS_REQUIRED_COOKIE = "web_session"

CITY_HINTS = [
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "南京",
    "成都",
    "重庆",
    "武汉",
    "西安",
    "东京",
    "大阪",
    "京都",
    "首尔",
    "济州",
    "新加坡",
    "曼谷",
    "普吉",
    "清迈",
    "巴黎",
    "伦敦",
    "罗马",
    "纽约",
    "洛杉矶",
]


@dataclass
class XhsSearchResult:
    status: str
    blocks: List[Dict[str, Any]]
    keyword: str = ""
    message: Optional[str] = None
    from_cache: bool = False


class XhsClient:
    def __init__(self, settings: Settings, store: QueryStore):
        self.settings = settings
        self.store = store
        self._semaphore = threading.BoundedSemaphore(1)
        self._lock = threading.Lock()
        self._running = 0
        self._queued = 0
        self._last_error: Optional[str] = None

    def should_attempt(self, user: Dict[str, Any]) -> bool:
        if not self.settings.xhs_enabled:
            return False
        return bool(self.extract_keyword(""))

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            running = self._running
            queued = self._queued
            last_error = self._last_error
        return {
            "enabled": self.settings.xhs_enabled,
            "running": running,
            "queued": queued,
            "today_calls": self.store.count_usage_events_today(None, XHS_EVENT_TYPE),
            "cache": self.store.xhs_cache_stats(),
            "last_error": last_error,
        }

    def health(self) -> Dict[str, Any]:
        media_dir = self._media_dir()
        uv_path = self._uv_path()
        main_py = media_dir / "main.py"
        cookie_configured = bool(self.settings.xhs_cookies)
        required_cookie_ok = (
            True
            if self.settings.xhs_login_type != "cookie"
            else xhs_cookie_has_required_session(self.settings.xhs_cookies)
        )
        return {
            "enabled": self.settings.xhs_enabled,
            "mediacrawler_dir_ok": media_dir.exists() and main_py.exists(),
            "mediacrawler_dir": str(media_dir),
            "uv_ok": bool(uv_path),
            "uv_path": uv_path,
            "login_type": self.settings.xhs_login_type,
            "cookie_configured": cookie_configured,
            "required_cookie_ok": required_cookie_ok,
            "required_cookie_name": XHS_REQUIRED_COOKIE if self.settings.xhs_login_type == "cookie" else None,
            "timeout_seconds": self.settings.xhs_timeout_seconds,
            "max_results": self.settings.xhs_max_results,
            "cache_ttl_hours": self.settings.xhs_cache_ttl_hours,
            "last_error": self._last_error,
        }

    def search(self, user_query: str, user: Dict[str, Any]) -> XhsSearchResult:
        keyword = self.extract_keyword(user_query)
        if not self.settings.xhs_enabled:
            return XhsSearchResult(status="disabled", blocks=[], keyword=keyword)
        if not keyword:
            return XhsSearchResult(status="skipped", blocks=[], keyword=keyword)

        quota = self._quota_result(user, keyword)
        if quota:
            return quota

        cache_key = self.cache_key(keyword)
        cached = self.store.get_xhs_cache(cache_key, self.settings.xhs_cache_ttl_hours * 3600)
        if cached is not None:
            return XhsSearchResult(status="ok", blocks=cached, keyword=keyword, from_cache=True)

        ready_error = self._readiness_error()
        if ready_error:
            self._remember_error(ready_error)
            return XhsSearchResult(
                status="unavailable",
                blocks=[self._notice("小红书灵感暂时不可用", ready_error)],
                keyword=keyword,
                message=ready_error,
            )

        with self._lock:
            self._queued += 1
        acquired = False
        try:
            acquired = self._semaphore.acquire(timeout=self._queue_timeout_seconds())
        finally:
            with self._lock:
                self._queued = max(0, self._queued - 1)
        if not acquired:
            return XhsSearchResult(
                status="queue_timeout",
                blocks=[
                    self._notice(
                        "小红书灵感排队超时",
                        "小红书补充等待时间过长，本次先展示实时旅行查询结果；稍后重试通常可以恢复。",
                    )
                ],
                keyword=keyword,
            )

        with self._lock:
            self._running += 1
        try:
            self.store.log_usage_event(int(user["id"]), XHS_EVENT_TYPE, {"keyword": keyword})
            posts = self._run_mediacrawler(keyword)
            blocks = build_xhs_blocks(posts, self.settings.xhs_max_results)
            if not blocks:
                blocks = [self._notice("小红书灵感暂无结果", "这次没有拿到可展示的高互动笔记，主查询结果不受影响。")]
            self.store.upsert_xhs_cache(cache_key, keyword, blocks, {"count": len(posts)})
            self._remember_error(None)
            return XhsSearchResult(status="ok", blocks=blocks, keyword=keyword)
        except subprocess.TimeoutExpired:
            message = "小红书补充查询超过时间上限，主查询结果已先展示。"
            self._remember_error(message)
            return XhsSearchResult(status="timeout", blocks=[self._notice("小红书灵感超时", message)], keyword=keyword)
        except Exception as exc:
            message = f"小红书补充查询失败：{self._redact_secret(str(exc))}"
            self._remember_error(message)
            return XhsSearchResult(status="error", blocks=[self._notice("小红书灵感暂时不可用", message)], keyword=keyword)
        finally:
            with self._lock:
                self._running = max(0, self._running - 1)
            self._semaphore.release()

    def extract_keyword(self, user_query: str) -> str:
        return extract_xhs_keyword(user_query)

    def cache_key(self, keyword: str) -> str:
        return hashlib.sha256(keyword.encode("utf-8")).hexdigest()

    def _queue_timeout_seconds(self) -> int:
        return max(15, int(self.settings.xhs_timeout_seconds) + 10)

    def _quota_result(self, user: Dict[str, Any], keyword: str) -> Optional[XhsSearchResult]:
        if user.get("role") == "owner":
            return None
        limit = int(self.settings.xhs_max_daily_per_user)
        if limit < 0:
            return None
        used = self.store.count_usage_events_today(int(user["id"]), XHS_EVENT_TYPE)
        if used < limit:
            return None
        return XhsSearchResult(
            status="quota",
            blocks=[self._notice("小红书补充次数已用完", "今天的小红书灵感补充次数已用完，实时旅行查询仍可正常使用。")],
            keyword=keyword,
        )

    def _readiness_error(self) -> Optional[str]:
        media_dir = self._media_dir()
        if not media_dir.exists() or not (media_dir / "main.py").exists():
            return "服务器还没有配置 MediaCrawler 目录。"
        if not self._uv_path():
            return "服务器还没有安装 uv，无法启动 MediaCrawler。"
        if self.settings.xhs_login_type == "cookie" and not self.settings.xhs_cookies:
            return "小红书 cookie 还没有配置。"
        if self.settings.xhs_login_type == "cookie" and not xhs_cookie_has_required_session(self.settings.xhs_cookies):
            return "小红书 cookie 缺少 MediaCrawler 必需的 web_session。请从 www.xiaohongshu.com 的 Cookie 里复制 web_session 后再开启。"
        return None

    def _run_mediacrawler(self, keyword: str) -> List[Dict[str, Any]]:
        media_dir = self._media_dir()
        started_at = time.time()
        uv_path = self._uv_path() or "uv"
        command = [
            uv_path,
            "run",
            "main.py",
            "--platform",
            "xhs",
            "--lt",
            self.settings.xhs_login_type,
            "--type",
            "search",
            "--keywords",
            keyword,
            "--get_comment",
            "false",
            "--get_sub_comment",
            "false",
            "--save_data_option",
            "jsonl",
        ]
        if self.settings.xhs_cookies:
            command.extend(["--cookies", self.settings.xhs_cookies])

        completed = subprocess.run(
            command,
            cwd=str(media_dir),
            env=self._command_env(uv_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, int(self.settings.xhs_timeout_seconds)),
            check=False,
        )
        if completed.returncode != 0:
            stderr = self._redact_secret((completed.stderr or completed.stdout or "").strip())
            raise RuntimeError(_truncate(stderr, 300) or f"MediaCrawler exited with {completed.returncode}")
        return parse_mediacrawler_outputs(media_dir, since=started_at)

    def _media_dir(self) -> Path:
        path = self.settings.mediacrawler_dir
        return path if path.is_absolute() else path.resolve()

    def _uv_path(self) -> Optional[str]:
        path = shutil.which("uv")
        if path:
            return path
        for candidate in (Path.home() / ".local" / "bin" / "uv", Path.home() / ".cargo" / "bin" / "uv"):
            if candidate.exists() and candidate.is_file():
                return str(candidate)
        return None

    def _command_env(self, uv_path: str) -> Dict[str, str]:
        env = os.environ.copy()
        uv_dir = str(Path(uv_path).parent)
        path = env.get("PATH", "")
        if uv_dir and uv_dir not in path.split(os.pathsep):
            env["PATH"] = uv_dir + os.pathsep + path
        return env

    def _redact_secret(self, text: str) -> str:
        cookies = self.settings.xhs_cookies
        if cookies:
            return text.replace(cookies, "[redacted-cookie]")
        return text

    def _notice(self, title: str, message: str) -> Dict[str, Any]:
        return {
            "type": "notice",
            "title": title,
            "severity": "warning",
            "source": "小红书",
            "supplement": "xhs",
            "items": [message],
        }

    def _remember_error(self, message: Optional[str]) -> None:
        with self._lock:
            self._last_error = message


def extract_xhs_keyword(user_query: str) -> str:
    clean = re.sub(r"\s+", " ", user_query or "").strip()
    if not clean:
        return "旅行攻略"

    cities = []
    for city in CITY_HINTS:
        if city in clean and city not in cities:
            cities.append(city)
    if len(cities) >= 2:
        return f"{cities[-1]} 旅行攻略 避坑"
    if len(cities) == 1:
        return f"{cities[0]} 旅行攻略 避坑"

    clean = re.sub(r"\d{2,4}\s*年|\d{1,2}\s*月|\d{1,2}\s*日|\d+\s*晚|\d+\s*天", " ", clean)
    clean = re.sub(r"(机票|航班|酒店|价格|票价|最低价|航班号|高铁|火车|查询|推荐|有哪些|选择)", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return "旅行攻略 避坑"
    return _truncate(clean, 36)


def normalize_xhs_cookie_input(cookie_input: str) -> str:
    pairs: List[Tuple[str, str]] = []
    freeform_lines: List[str] = []
    for raw_line in (cookie_input or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "\t" in line:
            columns = [column.strip() for column in line.split("\t")]
            if len(columns) >= 2 and _looks_like_cookie_name(columns[0]) and columns[1]:
                pairs.append((columns[0], columns[1]))
                continue
        freeform_lines.append(line)

    freeform = "; ".join(freeform_lines)
    for part in freeform.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if _looks_like_cookie_name(name) and value:
            pairs.append((name, value))

    deduped: Dict[str, str] = {}
    for name, value in pairs:
        deduped[name] = value
    return "; ".join(f"{name}={value}" for name, value in deduped.items())


def parse_cookie_names(cookie_string: str) -> set[str]:
    names: set[str] = set()
    for part in normalize_xhs_cookie_input(cookie_string).split(";"):
        if "=" not in part:
            continue
        name, _ = part.split("=", 1)
        name = name.strip()
        if name:
            names.add(name)
    return names


def xhs_cookie_has_required_session(cookie_string: str) -> bool:
    return XHS_REQUIRED_COOKIE in parse_cookie_names(cookie_string)


def _looks_like_cookie_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.:-]+", value or ""))


def parse_mediacrawler_outputs(media_dir: Path, since: float = 0) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in _candidate_files(media_dir, since):
        records.extend(_records_from_file(path))
    notes = [_normalize_note(record) for record in _walk_dicts(records)]
    notes = [note for note in notes if note]
    return _dedupe_notes(notes)


def build_xhs_blocks(posts: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
    ranked = sorted(posts, key=_rank_score, reverse=True)
    blocks: List[Dict[str, Any]] = []
    for post in ranked[: max(0, max_results)]:
        title = post.get("title") or "小红书笔记"
        url = post.get("postUrl")
        block = {
            "type": "xhs_post_card",
            "title": title,
            "summary": post.get("summary", ""),
            "imageUrl": post.get("imageUrl", ""),
            "bookingUrl": url,
            "postUrl": url,
            "author": post.get("author", ""),
            "likedCount": post.get("likedCount", 0),
            "collectedCount": post.get("collectedCount", 0),
            "commentCount": post.get("commentCount", 0),
            "publishedAt": post.get("publishedAt", ""),
            "source": "小红书",
            "supplement": "xhs",
        }
        blocks.append(block)
    return blocks


def _candidate_files(media_dir: Path, since: float) -> List[Path]:
    data_roots = [media_dir / "data", media_dir]
    files: List[Path] = []
    for root in data_roots:
        if not root.exists():
            continue
        for pattern in ("**/*.jsonl", "**/*.json"):
            for path in root.glob(pattern):
                try:
                    if path.stat().st_mtime + 2 >= since:
                        files.append(path)
                except OSError:
                    continue
    return sorted(set(files), key=lambda item: item.stat().st_mtime, reverse=True)[:24]


def _records_from_file(path: Path) -> List[Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    if not text.strip():
        return []
    if path.suffix == ".jsonl":
        records = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def _walk_dicts(values: Iterable[Any]) -> Iterable[Dict[str, Any]]:
    for value in values:
        if isinstance(value, dict):
            yield value
            for child in value.values():
                if isinstance(child, (list, dict)):
                    yield from _walk_dicts([child])
        elif isinstance(value, list):
            yield from _walk_dicts(value)


def _normalize_note(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    title = _first_text(record, "title", "display_title", "note_title")
    summary = _first_text(record, "desc", "description", "content", "note_desc")
    note_id = _first_text(record, "note_id", "noteId", "id", "aweme_id")
    url = _first_text(record, "note_url", "noteUrl", "url", "share_url", "web_url")
    if not url and note_id:
        url = f"https://www.xiaohongshu.com/explore/{note_id}"
    if not title and not summary:
        return None
    if not url:
        return None
    return {
        "title": _truncate(title or summary or "小红书笔记", 80),
        "summary": _truncate(summary or "", 180),
        "postUrl": url,
        "imageUrl": _extract_image_url(record),
        "author": _extract_author(record),
        "likedCount": _first_int(record, "liked_count", "like_count", "likedCount", "likes"),
        "collectedCount": _first_int(record, "collected_count", "collect_count", "collectedCount", "collects"),
        "commentCount": _first_int(record, "comment_count", "comments_count", "commentCount", "comments"),
        "publishedAt": _first_text(record, "time", "publish_time", "publishTime", "last_modify_ts"),
    }


def _dedupe_notes(notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    output = []
    for note in sorted(notes, key=_rank_score, reverse=True):
        key = note.get("postUrl") or note.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(note)
    return output


def _rank_score(note: Dict[str, Any]) -> float:
    return (
        int(note.get("likedCount") or 0)
        + int(note.get("collectedCount") or 0) * 2
        + int(note.get("commentCount") or 0) * 1.5
    )


def _first_text(record: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _first_int(record: Dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = record.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            clean = value.replace(",", "").strip()
            if clean.isdigit():
                return int(clean)
    return 0


def _extract_author(record: Dict[str, Any]) -> str:
    for key in ("nickname", "user_nickname", "user_name", "author", "creator"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _first_text(value, "nickname", "name", "user_name")
            if nested:
                return nested
    user = record.get("user")
    if isinstance(user, dict):
        return _first_text(user, "nickname", "name", "user_name")
    return ""


def _extract_image_url(record: Dict[str, Any]) -> str:
    for key in ("imageUrl", "image_url", "cover", "cover_url", "pic_url", "thumbnail"):
        value = record.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    for key in ("image_list", "images", "imageList"):
        value = record.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.startswith(("http://", "https://")):
                    return item
                if isinstance(item, dict):
                    nested = _first_text(item, "url", "image_url", "trace_id")
                    if nested.startswith(("http://", "https://")):
                        return nested
    return ""


def _truncate(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"
