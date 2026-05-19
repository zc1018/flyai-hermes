# Hermes FlyAI Travel

一个受控旅行查询 Web 应用：浏览器输入自然语言，后端调用 Hermes 并加载 `flyai` skill，最后把结果渲染成航班、酒店、景点、火车、目的地和攻略卡片。支持 owner 后台、朋友独立口令、配额和历史隔离。

## 运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
npm install
cp .env.example .env
OWNER_PASSWORD=change-me uvicorn app.main:app --host 0.0.0.0 --port 8787
```

打开 `http://localhost:8787`，使用 `OWNER_PASSWORD` 登录。owner 可访问 `/admin` 创建朋友口令并配置额度。

## 部署依赖

- Hermes 已安装并配置好模型和 key。
- Hermes 中 `flyai` skill 已启用。
- `npm install` 已安装本项目的 `@fly-ai/flyai-cli`，或部署环境的 `PATH` 中已有 `flyai` 命令。
- 服务端配置 `OWNER_PASSWORD`，生产环境同时配置稳定的 `SESSION_SECRET`；HTTPS 部署时设置 `SECURE_COOKIES=true`。
- 朋友用户默认每日 10 次、单用户并发 1、单次 300 秒；owner 不限每日次数，但仍受服务端硬超时保护。

## 健康检查

`GET /api/health` 只返回公共可用状态，不暴露部署路径。详细检查在 owner 后台接口 `GET /api/admin/health`：

- Hermes 二进制是否存在。
- `flyai` CLI 是否可用。
- SQLite 是否可写。
