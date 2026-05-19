# Hermes FlyAI Travel

一个内部旅行查询 Web 应用：浏览器输入自然语言，后端调用 Hermes 并加载 `flyai` skill，最后把结果渲染成航班、酒店、景点、火车和攻略卡片。

## 运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
npm install
cp .env.example .env
APP_PASSWORD=change-me uvicorn app.main:app --host 0.0.0.0 --port 8787
```

打开 `http://localhost:8787`，使用 `APP_PASSWORD` 登录。

## 部署依赖

- Hermes 已安装并配置好模型和 key。
- Hermes 中 `flyai` skill 已启用。
- `npm install` 已安装本项目的 `@fly-ai/flyai-cli`，或部署环境的 `PATH` 中已有 `flyai` 命令。
- 服务端配置 `APP_PASSWORD`，生产环境同时配置稳定的 `SESSION_SECRET`。

如果部署环境暂时没有 Hermes，`DIRECT_FLYAI_FALLBACK=true` 会让后端退回到 `flyai ai-search` 直连模式。Hermes 安装好后会自动优先使用 Hermes。

## 健康检查

`GET /api/health` 会检查：

- Hermes 二进制是否存在。
- `flyai` CLI 是否可用。
- SQLite 是否可写。
