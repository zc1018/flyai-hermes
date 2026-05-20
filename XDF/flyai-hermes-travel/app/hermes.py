from __future__ import annotations

import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Iterator, List, Literal, Optional

from .config import ROOT_DIR, Settings


@dataclass(frozen=True)
class HermesResult:
    stdout: str
    stderr: str
    return_code: int
    duration_ms: int
    timed_out: bool = False


@dataclass(frozen=True)
class HermesStreamEvent:
    type: Literal["start", "chunk", "heartbeat", "done", "error"]
    message: str = ""
    result: Optional[HermesResult] = None
    elapsed_ms: int = 0


def build_prompt(user_query: str, flyai_bin: str = "flyai") -> str:
    today = date.today().isoformat()
    return f"""
你是旅行查询 agent。今天是 {today}。

任务：
1. 必须优先使用已加载的 flyai skill 查询用户的实时旅行需求。
2. 不要调用 skill_view，也不要读取 references 文档；你已经知道本次应直接执行：
   {flyai_bin} ai-search --query "<用户原始请求>"
3. 执行 terminal 命令时 timeout 至少设置为 150 秒，并保留现有 PATH。
4. 查询完成后，优先输出一个纯 JSON 对象，不要输出代码围栏，不要解释执行过程。结构如下：
   {{"summary":"一句话结论","blocks":[{{"type":"flight_card|train_card|hotel_card|poi_card|destination_card|guide_section|notice","title":"...","price":"...","number":"...","segments":[{{"label":"去程/返程","depCity":"...","depStation":"...","depTime":"...","arrCity":"...","arrStation":"...","arrTime":"...","carrier":"...","number":"...","price":"..."}}],"items":["..."]}}]}}
5. 如果用户要往返机票，必须在同一个 flight_card 里同时给出去程和返程两个 segment；每个 segment 都要尽量保留航班号、航司、日期、起降时间、机场/城市、单段价格。若 flyai 没返回某个价格，写“未返回票价”，不要省略字段。
6. 如果用户要酒店、火车或景点，也要保留酒店名称、列车班次号、价格、地址、评分、链接等 flyai 已返回的核心字段。
7. 如果 flyai 返回 jumpUrl/detailUrl/picUrl/mainPic，保留这些链接和图片字段或 Markdown 链接。
8. 如果无法稳定转换成上述 JSON，才直接输出 flyai 返回的 Markdown；但仍不要输出代码围栏。
9. 如果没有查到结果，输出一个简短中文说明，并给出下一步可尝试的更具体条件。

用户请求：
{user_query}
""".strip()


class HermesClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def command(self, prompt: str) -> List[str]:
        command = [
            self.settings.hermes_bin,
            "chat",
            "-q",
            prompt,
            "--accept-hooks",
            "--skills",
            self.settings.hermes_skill,
            "--source",
            "flyai-web",
            "--max-turns",
            "8",
            "-Q",
        ]
        if self.settings.hermes_provider:
            command.extend(["--provider", self.settings.hermes_provider])
        if self.settings.hermes_model:
            command.extend(["-m", self.settings.hermes_model])
        return command

    def stream_command(self, prompt: str) -> List[str]:
        command = [
            self.settings.hermes_bin,
            "chat",
            "-q",
            prompt,
            "--accept-hooks",
            "--skills",
            self.settings.hermes_skill,
            "--source",
            "flyai-web",
            "--max-turns",
            "8",
            "-Q",
        ]
        if self.settings.hermes_provider:
            command.extend(["--provider", self.settings.hermes_provider])
        if self.settings.hermes_model:
            command.extend(["-m", self.settings.hermes_model])
        return command

    def run_stream(self, user_query: str, timeout_seconds: Optional[int] = None) -> Iterator[HermesStreamEvent]:
        if not Path(self.settings.hermes_bin).exists():
            yield HermesStreamEvent(
                type="error",
                message=f"Hermes 不可用：{self.settings.hermes_bin}",
                elapsed_ms=0,
            )
            return

        prompt = build_prompt(user_query, flyai_bin=_flyai_bin())
        command = self.stream_command(prompt)
        env = runtime_env()
        env["PYTHONUNBUFFERED"] = "1"
        effective_timeout = timeout_seconds or self.settings.hermes_timeout_seconds
        started = time.monotonic()
        output_queue: queue.Queue[Optional[str]] = queue.Queue()

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0,
                env=env,
            )
        except OSError as exc:
            yield HermesStreamEvent(type="error", message=str(exc), elapsed_ms=0)
            return

        def reader() -> None:
            assert process.stdout is not None
            try:
                while True:
                    chunk = process.stdout.read(1)
                    if chunk == "":
                        break
                    output_queue.put(chunk)
            finally:
                output_queue.put(None)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        chunks: List[str] = []
        pending: List[str] = []
        last_flush = time.monotonic()
        try:
            yield HermesStreamEvent(type="start", message="Hermes 已启动，正在理解请求并调用 flyai skill。", elapsed_ms=0)

            stream_done = False
            while not stream_done:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if elapsed_ms > effective_timeout * 1000:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    result = HermesResult(
                        stdout="".join(chunks).strip(),
                        stderr="Hermes streaming execution timed out.",
                        return_code=124,
                        duration_ms=elapsed_ms,
                        timed_out=True,
                    )
                    yield HermesStreamEvent(type="done", result=result, elapsed_ms=elapsed_ms)
                    return

                try:
                    item = output_queue.get(timeout=1)
                except queue.Empty:
                    if pending:
                        yield HermesStreamEvent(
                            type="chunk",
                            message="".join(pending),
                            elapsed_ms=elapsed_ms,
                        )
                        pending = []
                        last_flush = time.monotonic()
                    yield HermesStreamEvent(
                        type="heartbeat",
                        message="Hermes 正在运行，连接保持中。",
                        elapsed_ms=elapsed_ms,
                    )
                    continue

                if item is None:
                    stream_done = True
                    continue
                chunks.append(item)
                clean = _clean_stream_chunk(item)
                if clean:
                    pending.append(clean)
                should_flush = (
                    item == "\n"
                    or sum(len(part) for part in pending) >= 180
                    or (pending and time.monotonic() - last_flush >= 0.35)
                )
                if should_flush:
                    yield HermesStreamEvent(type="chunk", message="".join(pending), elapsed_ms=elapsed_ms)
                    pending = []
                    last_flush = time.monotonic()

            if pending:
                yield HermesStreamEvent(
                    type="chunk",
                    message="".join(pending),
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            return_code = process.wait()
            duration_ms = int((time.monotonic() - started) * 1000)
            stdout = "".join(chunks).strip()
            yield HermesStreamEvent(
                type="done",
                result=HermesResult(
                    stdout=stdout,
                    stderr="" if return_code == 0 else stdout,
                    return_code=return_code,
                    duration_ms=duration_ms,
                ),
                elapsed_ms=duration_ms,
            )
        finally:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

    def run(self, user_query: str, timeout_seconds: Optional[int] = None) -> HermesResult:
        if not Path(self.settings.hermes_bin).exists():
            return HermesResult(
                stdout="",
                stderr=f"Hermes 不可用：{self.settings.hermes_bin}",
                return_code=127,
                duration_ms=0,
            )

        prompt = build_prompt(user_query, flyai_bin=_flyai_bin())
        effective_timeout = timeout_seconds or self.settings.hermes_timeout_seconds
        started = time.monotonic()
        try:
            completed = subprocess.run(
                self.command(prompt),
                check=False,
                capture_output=True,
                text=True,
                env=runtime_env(),
                timeout=effective_timeout,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            result = HermesResult(
                stdout=completed.stdout.strip(),
                stderr=completed.stderr.strip(),
                return_code=completed.returncode,
                duration_ms=duration_ms,
            )
            return result
        except OSError as exc:
            return HermesResult(
                stdout="",
                stderr=str(exc),
                return_code=127,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return HermesResult(
                stdout=stdout.strip(),
                stderr=stderr.strip() or "Hermes execution timed out.",
                return_code=124,
                duration_ms=duration_ms,
                timed_out=True,
            )


def _clean_stream_chunk(chunk: str) -> str:
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", chunk)
    text = text.replace("\r", "\n")
    return text


def _flyai_bin() -> str:
    return shutil.which("flyai", path=runtime_env().get("PATH")) or "flyai"


def runtime_env() -> dict:
    env = os.environ.copy()
    local_bin = str(ROOT_DIR / "node_modules" / ".bin")
    current_path = env.get("PATH", "")
    env["PATH"] = local_bin + os.pathsep + current_path if current_path else local_bin
    env.setdefault("HOME", str(Path.home()))
    env.setdefault("HERMES_HOME", str(Path(env["HOME"]) / ".hermes"))
    env.setdefault("HERMES_ACCEPT_HOOKS", "1")
    env.setdefault("HERMES_INFERENCE_PROVIDER", "kimi-coding")
    env.setdefault("HERMES_INFERENCE_MODEL", "kimi-k2.6")
    return env
