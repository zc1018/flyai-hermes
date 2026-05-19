#!/usr/bin/env bash
set -euo pipefail

ARCHIVE_URL="${1:-}"
APP_NAME="flyai-hermes-travel"
APP_DIR="/home/ec2-user/${APP_NAME}"
PORT="${PORT:-8787}"
PUBLIC_PATH="${PUBLIC_PATH:-/flyai-travel/}"
APP_PASSWORD="${APP_PASSWORD:-change-me}"
SESSION_SECRET="${SESSION_SECRET:-$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)}"

if [[ -z "${ARCHIVE_URL}" ]]; then
  echo "Usage: APP_PASSWORD=... $0 https://tmpfiles.org/dl/.../flyai-hermes-travel.zip"
  exit 2
fi

workdir="$(mktemp -d)"
cleanup() {
  rm -rf "${workdir}"
}
trap cleanup EXIT

echo "Downloading ${ARCHIVE_URL}"
curl -L "${ARCHIVE_URL}" -o "${workdir}/${APP_NAME}.zip"
rm -rf "${workdir}/src"
mkdir -p "${workdir}/src"
unzip -q "${workdir}/${APP_NAME}.zip" -d "${workdir}/src"

echo "Installing app files to ${APP_DIR}"
mkdir -p "${APP_DIR}"
rsync -a --delete \
  --exclude ".venv" \
  --exclude "node_modules" \
  --exclude "data/*.db" \
  --exclude "data/*.db-*" \
  "${workdir}/src/${APP_NAME}/" "${APP_DIR}/"

cd "${APP_DIR}"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
npm install --omit=dev
mkdir -p data

cat > .env <<EOF
APP_PASSWORD=${APP_PASSWORD}
SESSION_SECRET=${SESSION_SECRET}
HERMES_BIN=/home/ec2-user/.local/bin/hermes
HERMES_HOME=/home/ec2-user/.hermes
HERMES_SKILL=flyai
HERMES_PROVIDER=kimi-coding
HERMES_MODEL=kimi-k2.6
HERMES_INFERENCE_PROVIDER=kimi-coding
HERMES_INFERENCE_MODEL=kimi-k2.6
HERMES_TIMEOUT_SECONDS=900
DATABASE_PATH=data/travel.db
EOF

echo "Writing systemd service"
sudo tee /etc/systemd/system/${APP_NAME}.service >/dev/null <<EOF
[Unit]
Description=Hermes FlyAI Travel
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
Environment=HOME=/home/ec2-user
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port ${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ${APP_NAME}
sudo systemctl restart ${APP_NAME}

echo "Adding nginx location to existing 100zhang.top server block"
nginx_patch="${workdir}/patch_nginx.py"
cat > "${nginx_patch}" <<'PY'
from pathlib import Path
import re
import shutil

app_name = "flyai-hermes-travel"
port = "8787"
marker = "# flyai-hermes-travel:start"
location = f"""
    {marker}
    location = /flyai-travel {{
        return 301 /flyai-travel/;
    }}

    location /flyai-travel/ {{
        proxy_pass http://127.0.0.1:{port}/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
        proxy_buffering off;
    }}
    # flyai-hermes-travel:end
"""

def server_blocks(text: str):
    for match in re.finditer(r"\bserver\s*\{", text):
        start = match.start()
        depth = 0
        for idx in range(match.end() - 1, len(text)):
            if text[idx] == "{":
                depth += 1
            elif text[idx] == "}":
                depth -= 1
                if depth == 0:
                    yield start, idx
                    break

changed = False
for path in sorted(Path("/etc/nginx/conf.d").glob("*.conf")):
    text = path.read_text()
    if marker in text:
        continue
    inserts = []
    for start, end in server_blocks(text):
        block = text[start : end + 1]
        if "server_name" in block and "100zhang.top" in block:
            inserts.append(end)
    if not inserts:
        continue
    shutil.copy2(path, f"{path}.bak.{app_name}")
    for end in reversed(inserts):
        text = text[:end] + location + "\n" + text[end:]
    path.write_text(text)
    changed = True

if not changed:
    fallback = Path("/etc/nginx/conf.d/flyai-hermes-travel.conf")
    fallback.write_text(f"""
server {{
    listen 80;
    server_name 100zhang.top;
{location}
}}
""")
PY
sudo python3 "${nginx_patch}"

sudo nginx -t
sudo systemctl reload nginx

echo "Local health check"
curl -fsS "http://127.0.0.1:${PORT}/api/health"
echo
echo "Done: http://100zhang.top${PUBLIC_PATH}"
