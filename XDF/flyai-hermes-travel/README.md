# Hermes FlyAI Travel

一个受控的旅行查询 Web 应用。用户在浏览器里输入自然语言，服务端通过 Hermes 加载 `flyai` skill 查询实时旅行信息，并把返回内容整理成更易读的航班、火车、酒店、景点、目的地和攻略卡片。

项目适合小范围给朋友使用：owner 使用管理员口令登录，可以在后台创建朋友口令、设置每日额度和并发限制；普通用户只能看到自己的查询历史。

## 功能

- 自然语言查询旅行信息：机票、火车、酒店、景点、目的地推荐和行程攻略。
- Hermes 流式执行：前端可以看到实时进度，服务端不再走 direct-flyai 降级路径。
- 卡片化渲染：后端 normalizer 会把 fly.ai/Hermes 的 JSON 或 Markdown 输出转换成结构化卡片。
- 往返机票保护：如果用户问的是往返机票，后端会校验卡片是否包含去程和返程；能从原文补齐时自动修复，不能补齐时显示明确提示。
- 多口令访问控制：owner 不限每日次数，朋友口令可单独配置启用状态、每日额度、并发数、超时时间和历史权限。
- 历史隔离：普通用户只看自己的历史；owner 后台可查看全局最近查询和运行状态。
- 安全健康检查：公共 `/api/health` 只返回可用状态，详细路径和模型信息只在 owner 后台可见。

## 本地运行

前置依赖：

- Python 3.9+
- Node.js/npm
- 已安装 Hermes，并且 Hermes 中已配置 `flyai` skill
- 能在服务端 PATH 中找到 `flyai`，或已通过 `npm install` 安装本项目的 `@fly-ai/flyai-cli`

启动：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
npm install
cp .env.example .env
OWNER_PASSWORD=change-me uvicorn app.main:app --host 0.0.0.0 --port 8787
```

打开 `http://localhost:8787`，输入 `OWNER_PASSWORD` 登录。owner 可访问 `http://localhost:8787/admin` 创建朋友口令并配置权限。

## 配置项

主要配置来自环境变量或 `.env`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OWNER_PASSWORD` | 继承 `APP_PASSWORD` 或空 | owner 访问口令，启动时会初始化或更新 owner 用户 |
| `APP_PASSWORD` | 空 | 旧单口令兼容项，不建议新部署使用 |
| `SESSION_SECRET` | 随机生成 | Cookie 签名密钥，生产环境必须固定为长随机字符串 |
| `SECURE_COOKIES` | `false` | HTTPS 部署时设为 `true` |
| `COOKIE_NAME` | `flyai_travel_session` | 登录 Cookie 名；同域名并行部署 v2 时应改成独立名称 |
| `HERMES_BIN` | `/Users/xdf/.local/bin/hermes` | Hermes 可执行文件路径 |
| `HERMES_HOME` | `~/.hermes` | Hermes home，用于检查 skill |
| `HERMES_SKILL` | `flyai` | Hermes 加载的 skill 名称 |
| `HERMES_PROVIDER` | `kimi-coding` | Hermes provider |
| `HERMES_MODEL` | `kimi-k2.6` | Hermes model |
| `HERMES_TIMEOUT_SECONDS` | `900` | 服务端硬超时上限 |
| `DATABASE_PATH` | `data/travel.db` | SQLite 数据库路径 |

`.env.example` 是本地开发模板。生产环境至少应显式配置 `OWNER_PASSWORD`、`SESSION_SECRET`、`HERMES_BIN`、`HERMES_HOME`、`SECURE_COOKIES=true`。

## 使用方式

1. owner 登录首页。
2. 进入 `/admin` 创建朋友口令。
3. 给朋友配置：
   - `daily_limit`：每日查询次数，默认 `10`
   - `max_concurrent`：单用户并发，默认 `1`
   - `timeout_seconds`：单次查询超时，普通用户最大 `300`
   - `can_view_history`：是否允许查看自己的历史
   - `enabled`：是否启用口令
4. 朋友只需要在登录页输入自己的口令，不需要用户名。
5. owner 可以在后台重置朋友口令、停用口令、查看今日使用量和最近查询。

默认资源控制：

- 全站 Hermes 查询并发：`2`
- 普通用户队列等待：`60s`
- 普通用户单次超时：最多 `300s`
- owner 每日不限次数，但仍受 `HERMES_TIMEOUT_SECONDS` 保护
- 普通用户查询文本超过 `500` 字会被拒绝，避免过度消耗

## API 概览

常用接口：

- `POST /api/login`：输入访问口令登录
- `POST /api/logout`：退出登录
- `GET /api/me`：当前用户和额度
- `POST /api/query/stream`：流式旅行查询
- `GET /api/history`：查询历史，普通用户按用户隔离
- `GET /api/health`：公共健康检查，只返回是否可用

owner 后台接口：

- `GET /api/admin/users`：用户列表
- `POST /api/admin/users`：创建朋友口令
- `PATCH /api/admin/users/{user_id}`：更新额度、启用状态等
- `POST /api/admin/users/{user_id}/reset-password`：重置朋友口令
- `GET /api/admin/usage`：运行状态、用户和最近查询
- `GET /api/admin/health`：详细健康检查

## 结果渲染

后端 `normalizer` 支持两类输入：

- fly.ai 原始 JSON：如 `itemList`、`journeys`、酒店/景点字段
- Hermes 整理后的 JSON 或 Markdown：包含 `blocks`、表格、段落、航班组合等

支持的卡片类型包括：

- `flight_card`
- `train_card`
- `hotel_card`
- `poi_card`
- `destination_card`
- `guide_section`
- `comparison_table`
- `notice`
- `booking_link`

Hermes 偶尔会输出 `flightcard`、`flight-card` 这类别名，normalizer 会兼容并转成标准类型。价格、航班号、列车班次号、酒店名等核心信息会尽量保留；如果 fly.ai 没返回价格，会显示“未返回票价”，而不是静默丢失。

## 部署到 AWS / Nginx

项目包含 `deploy/aws-install.sh`，用于把 zip 包部署到 EC2，并写入 systemd 和 Nginx 反向代理。

默认不传额外参数时，会部署/更新老服务：

- 目录：`/home/ec2-user/flyai-hermes-travel`
- systemd：`flyai-hermes-travel`
- 端口：`8787`
- 访问路径：`http://100zhang.top/flyai-travel/`

示例：

```bash
OWNER_PASSWORD='your-owner-password' \
SESSION_SECRET='your-long-random-secret' \
SECURE_COOKIES=true \
bash deploy/aws-install.sh 'https://example.com/flyai-hermes-travel.zip'
```

脚本会：

- 安装 Python 依赖和 Node 运行依赖
- 写入 `.env`
- 创建并启动 `flyai-hermes-travel` systemd 服务
- 在已有 `100zhang.top` Nginx server block 中加入 `/flyai-travel/` 反向代理
- 设置 Nginx 长超时和关闭代理缓冲，支持 Hermes 流式输出

常用运维命令：

```bash
sudo systemctl status flyai-hermes-travel
sudo systemctl restart flyai-hermes-travel
sudo journalctl -u flyai-hermes-travel -n 200 --no-pager
curl -fsS http://127.0.0.1:8787/api/health
```

HTTPS 证书建议使用 certbot，并启用自动续期。HTTPS 部署后应设置 `SECURE_COOKIES=true`。

### 并行发布 v2 子域名

如果要把新分支发布给小范围试用，不影响老业务，使用独立服务名、目录、端口、数据库和子域名：

```bash
APP_NAME=flyai-hermes-travel-v2 \
SERVICE_NAME=flyai-hermes-travel-v2 \
APP_DIR=/home/ec2-user/flyai-hermes-travel-v2 \
PORT=8791 \
SERVER_NAME=travel-v2.100zhang.top \
PUBLIC_PATH=/ \
DATABASE_PATH=data/travel-v2.db \
COOKIE_NAME=flyai_travel_v2_session \
OWNER_PASSWORD='your-owner-password' \
SESSION_SECRET='your-long-random-secret' \
SECURE_COOKIES=true \
bash deploy/aws-install.sh 'https://github.com/zc1018/flyai-hermes/archive/refs/heads/codex/product-ux-v2.zip'
```

这会创建独立的 `flyai-hermes-travel-v2` systemd 服务，不会覆盖老版目录、数据库或端口。子域名上线前需要确保 DNS 已指向服务器；首次启用 HTTPS 后执行：

```bash
sudo certbot --nginx -d travel-v2.100zhang.top
sudo systemctl reload nginx
```

v2 常用运维命令：

```bash
sudo systemctl status flyai-hermes-travel-v2
sudo systemctl restart flyai-hermes-travel-v2
sudo journalctl -u flyai-hermes-travel-v2 -n 200 --no-pager
curl -fsS http://127.0.0.1:8791/api/health
```

## 测试

```bash
. .venv/bin/activate
pytest -q
python3 -m compileall -q app
node --check static/app.js
node --check static/admin.js
```

当前测试重点覆盖：

- owner 初始化和登录
- 普通用户登录、禁用、额度和并发限制
- 普通用户历史隔离和后台权限拒绝
- Hermes 流式查询不走 direct-flyai fallback
- 公共健康检查不泄露部署细节
- 航班/酒店/景点/火车/目的地卡片渲染
- 往返机票缺返程时的自动修复和提示
- Hermes JSON 类型别名兼容，如 `flightcard`

## 故障排查

`访问口令不正确或该口令已停用`

- 确认部署环境的 `OWNER_PASSWORD` 是否和输入一致。
- 服务启动时会用 `OWNER_PASSWORD` 初始化或更新 owner 用户；改完 `.env` 后需要重启服务。
- 普通用户口令可能被 owner 禁用或重置。

`Hermes 查询失败或超时`

- 确认 `HERMES_BIN` 路径存在。
- 确认 `HERMES_HOME/skills` 下能找到 `flyai` skill。
- 确认服务进程 PATH 中能找到 `flyai`。
- 如果 fly.ai 上游 504，应用不会切 direct-flyai 降级；建议缩小日期范围或稍后重试。

`结果完整但没有卡片`

- 优先查看原始输出是否含有非标准 type、被截断 JSON 或缺少关键字段。
- normalizer 已兼容常见别名；新格式应加测试后扩展，避免只修单条样例。

`线上 HTTPS 提示不安全`

- 检查证书是否过期。
- 检查 Nginx 是否加载了最新证书。
- certbot 自动续期后需要 reload Nginx。

## 安全说明

- 不提供公开注册，只适合熟人小范围使用。
- 口令以 PBKDF2 hash 存储在 SQLite 中。
- Cookie 使用服务端签名，默认 7 天有效。
- 生产环境必须使用 HTTPS、稳定 `SESSION_SECRET` 和 `SECURE_COOKIES=true`。
- 不要把 `.env`、数据库文件或真实口令提交到 Git。
