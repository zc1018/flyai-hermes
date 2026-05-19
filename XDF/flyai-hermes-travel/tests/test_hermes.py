from pathlib import Path
import subprocess

from app.config import Settings
from app.hermes import HermesClient, build_prompt


def test_prompt_uses_flyai_absolute_command():
    prompt = build_prompt("杭州西湖门票", flyai_bin="/opt/bin/flyai")

    assert "/opt/bin/flyai ai-search" in prompt
    assert "不要调用 skill_view" in prompt


def test_empty_hermes_stdout_does_not_fallback(monkeypatch, tmp_path):
    hermes_bin = tmp_path / "hermes"
    hermes_bin.write_text("#!/bin/sh\n", encoding="utf-8")

    settings = Settings(
        app_password="pw",
        session_secret="secret",
        hermes_bin=str(hermes_bin),
        hermes_home=str(tmp_path / ".hermes"),
        hermes_skill="flyai",
        hermes_provider="kimi-coding",
        hermes_model="kimi-k2.6",
        hermes_timeout_seconds=30,
        database_path=Path("data/travel.db"),
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("app.hermes.subprocess.run", fake_run)

    result = HermesClient(settings).run("杭州西湖门票")

    assert result.return_code == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_hermes_command_forces_kimi_provider(tmp_path):
    settings = Settings(
        app_password="pw",
        session_secret="secret",
        hermes_bin="/usr/local/bin/hermes",
        hermes_home=str(tmp_path / ".hermes"),
        hermes_skill="flyai",
        hermes_provider="kimi-coding",
        hermes_model="kimi-k2.6",
        hermes_timeout_seconds=30,
        database_path=Path("data/travel.db"),
    )

    command = HermesClient(settings).command("hello")

    assert "--provider" in command
    assert command[command.index("--provider") + 1] == "kimi-coding"
    assert "-m" in command
    assert command[command.index("-m") + 1] == "kimi-k2.6"
