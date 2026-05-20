#!/usr/bin/env bash
set -euo pipefail

ARCHIVE_URL="${1:-}"
APP_NAME="${APP_NAME:-flyai-hermes-travel}"
SERVICE_NAME="${SERVICE_NAME:-${APP_NAME}}"
APP_DIR="${APP_DIR:-/home/ec2-user/${APP_NAME}}"
PORT="${PORT:-8787}"
PUBLIC_PATH="${PUBLIC_PATH:-/flyai-travel/}"
SERVER_NAME="${SERVER_NAME:-100zhang.top}"
DATABASE_PATH="${DATABASE_PATH:-data/travel.db}"
COOKIE_NAME="${COOKIE_NAME:-flyai_travel_session}"
OWNER_PASSWORD="${OWNER_PASSWORD:-${APP_PASSWORD:-change-me}}"
SESSION_SECRET="${SESSION_SECRET:-$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)}"

if [[ -z "${ARCHIVE_URL}" ]]; then
  echo "Usage: OWNER_PASSWORD=... $0 https://tmpfiles.org/dl/.../flyai-hermes-travel.zip"
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
source_root="${workdir}/src/${SOURCE_SUBDIR:-${APP_NAME}}"
if [[ ! -d "${source_root}" ]]; then
  source_root="$(find "${workdir}/src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
fi
if [[ -n "${source_root}" && -d "${source_root}" && ! -f "${source_root}/pyproject.toml" ]]; then
  detected_source="$(find "${source_root}" -maxdepth 4 -type f -name pyproject.toml -path "*/flyai-hermes-travel/pyproject.toml" | head -n 1 || true)"
  if [[ -z "${detected_source}" ]]; then
    detected_source="$(find "${source_root}" -maxdepth 4 -type f -name pyproject.toml | head -n 1 || true)"
  fi
  if [[ -n "${detected_source}" ]]; then
    source_root="$(dirname "${detected_source}")"
  fi
fi
if [[ -z "${source_root}" || ! -d "${source_root}" || ! -f "${source_root}/pyproject.toml" ]]; then
  echo "Could not locate extracted source directory" >&2
  exit 2
fi
rsync -a --delete \
  --exclude ".venv" \
  --exclude "node_modules" \
  --exclude "data/*.db" \
  --exclude "data/*.db-*" \
  "${source_root}/" "${APP_DIR}/"

cd "${APP_DIR}"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
npm install --omit=dev
mkdir -p data

cat > .env <<EOF
OWNER_PASSWORD=${OWNER_PASSWORD}
SESSION_SECRET=${SESSION_SECRET}
SECURE_COOKIES=${SECURE_COOKIES:-false}
COOKIE_NAME=${COOKIE_NAME}
HERMES_BIN=/home/ec2-user/.local/bin/hermes
HERMES_HOME=/home/ec2-user/.hermes
HERMES_SKILL=flyai
HERMES_PROVIDER=kimi-coding
HERMES_MODEL=kimi-k2.6
HERMES_INFERENCE_PROVIDER=kimi-coding
HERMES_INFERENCE_MODEL=kimi-k2.6
HERMES_TIMEOUT_SECONDS=900
DATABASE_PATH=${DATABASE_PATH}
EOF

echo "Writing systemd service"
sudo tee /etc/systemd/system/${SERVICE_NAME}.service >/dev/null <<EOF
[Unit]
Description=Hermes FlyAI Travel (${SERVICE_NAME})
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
sudo systemctl enable --now ${SERVICE_NAME}
sudo systemctl restart ${SERVICE_NAME}

echo "Adding nginx proxy for ${SERVER_NAME}${PUBLIC_PATH}"
nginx_patch="${workdir}/patch_nginx.py"
cat > "${nginx_patch}" <<'PY'
from pathlib import Path
import os
import re
import shutil

app_name = os.environ["NGINX_APP_NAME"]
port = os.environ["NGINX_PORT"]
server_name = os.environ["NGINX_SERVER_NAME"]
public_path = os.environ["NGINX_PUBLIC_PATH"].strip() or "/"
if not public_path.startswith("/"):
    public_path = "/" + public_path
if public_path != "/" and not public_path.endswith("/"):
    public_path += "/"

marker = f"# {app_name}:start"
end_marker = f"# {app_name}:end"

proxy_headers = f"""
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
        proxy_buffering off;"""

if public_path == "/":
    location = f"""
    {marker}
    location / {{
        proxy_pass http://127.0.0.1:{port}/;{proxy_headers}
    }}
    {end_marker}
"""
else:
    redirect_path = public_path.rstrip("/")
    location = f"""
    {marker}
    location = {redirect_path} {{
        return 301 {public_path};
    }}

    location {public_path} {{
        proxy_pass http://127.0.0.1:{port}/;{proxy_headers}
    }}
    {end_marker}
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
    if marker in text and end_marker in text:
        shutil.copy2(path, f"{path}.bak.{app_name}")
        pattern = re.compile(rf"\n?\s*{re.escape(marker)}.*?{re.escape(end_marker)}\n?", re.S)
        text = pattern.sub("\n" + location + "\n", text)
        path.write_text(text)
        changed = True
        continue
    inserts = []
    for start, end in server_blocks(text):
        block = text[start : end + 1]
        if "server_name" in block and server_name in block:
            inserts.append(end)
    if not inserts:
        continue
    shutil.copy2(path, f"{path}.bak.{app_name}")
    for end in reversed(inserts):
        text = text[:end] + location + "\n" + text[end:]
    path.write_text(text)
    changed = True

if not changed:
    fallback = Path(f"/etc/nginx/conf.d/{app_name}.conf")
    fallback.write_text(f"""
server {{
    listen 80;
    server_name {server_name};
{location}
}}
""")
PY
sudo env \
  NGINX_APP_NAME="${SERVICE_NAME}" \
  NGINX_PORT="${PORT}" \
  NGINX_SERVER_NAME="${SERVER_NAME}" \
  NGINX_PUBLIC_PATH="${PUBLIC_PATH}" \
  python3 "${nginx_patch}"

sudo nginx -t
sudo systemctl reload nginx

echo "Local health check"
curl -fsS "http://127.0.0.1:${PORT}/api/health"
echo
echo "Done: http://${SERVER_NAME}${PUBLIC_PATH}"
