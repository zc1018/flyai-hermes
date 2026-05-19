#!/usr/bin/env bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-/home/ec2-user/.hermes}"
HERMES_SRC_ARCHIVE="${HERMES_SRC_ARCHIVE:-/tmp/hermes-agent-src.tar.gz}"
FLYAI_SKILL_ARCHIVE="${FLYAI_SKILL_ARCHIVE:-/tmp/flyai-hermes-skill.tar.gz}"
HERMES_CONFIG="${HERMES_CONFIG:-/tmp/config.yaml}"
HERMES_AUTH="${HERMES_AUTH:-}"
HERMES_ENV="${HERMES_ENV:-/tmp/hermes.env}"
HERMES_PROVIDER="${HERMES_PROVIDER:-kimi-coding}"
HERMES_MODEL="${HERMES_MODEL:-kimi-k2.6}"
HERMES_WHEELHOUSE_ARCHIVE="${HERMES_WHEELHOUSE_ARCHIVE:-/tmp/hermes-wheelhouse.tar.gz}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
PIP_INDEX_URL="${PIP_INDEX_URL:-}"

if [[ ! -f "${HERMES_SRC_ARCHIVE}" ]]; then
  echo "Missing Hermes source archive: ${HERMES_SRC_ARCHIVE}" >&2
  exit 2
fi

echo "Stopping stale Hermes pip/install processes"
pkill -f "${HERMES_HOME}/hermes-agent/venv/bin/python -m pip" 2>/dev/null || true
pkill -f "${HERMES_HOME}/hermes-agent/venv/bin/pip" 2>/dev/null || true

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Installing Python 3.11"
  sudo dnf install -y python3.11 python3.11-pip python3.11-devel
fi

echo "Preparing Hermes home at ${HERMES_HOME}"
mkdir -p "${HERMES_HOME}/hermes-agent" "${HERMES_HOME}/skills" /home/ec2-user/.local/bin
rm -rf "${HERMES_HOME}/hermes-agent"
mkdir -p "${HERMES_HOME}/hermes-agent"
tar -xzf "${HERMES_SRC_ARCHIVE}" -C "${HERMES_HOME}/hermes-agent"

if [[ -f "${FLYAI_SKILL_ARCHIVE}" ]]; then
  rm -rf "${HERMES_HOME}/skills/flyai-skill"
  tar -xzf "${FLYAI_SKILL_ARCHIVE}" -C "${HERMES_HOME}/skills"
fi

if [[ -f "${HERMES_CONFIG}" ]]; then
  cp "${HERMES_CONFIG}" "${HERMES_HOME}/config.yaml"
fi
if [[ -n "${HERMES_AUTH}" && -f "${HERMES_AUTH}" ]]; then
  cp "${HERMES_AUTH}" "${HERMES_HOME}/auth.json"
fi
if [[ -f "${HERMES_ENV}" ]]; then
  cp "${HERMES_ENV}" "${HERMES_HOME}/.env"
fi

chmod 700 "${HERMES_HOME}"
chmod 600 "${HERMES_HOME}/config.yaml" "${HERMES_HOME}/auth.json" 2>/dev/null || true
chmod 600 "${HERMES_HOME}/.env" 2>/dev/null || true

echo "Creating Hermes Python environment"
"${PYTHON_BIN}" -m venv "${HERMES_HOME}/hermes-agent/venv"
pip_args=(--no-cache-dir --timeout 60 --retries 2 --prefer-binary)
if [[ -n "${PIP_INDEX_URL}" ]]; then
  pip_args+=(--index-url "${PIP_INDEX_URL}")
fi

if [[ -f "${HERMES_WHEELHOUSE_ARCHIVE}" ]]; then
  echo "Installing Hermes dependencies from local wheelhouse"
  rm -rf /tmp/hermes-wheelhouse /tmp/hermes-requirements.txt
  tar -xzf "${HERMES_WHEELHOUSE_ARCHIVE}" -C /tmp
  "${HERMES_HOME}/hermes-agent/venv/bin/python" -m pip install \
    --no-index \
    --find-links /tmp/hermes-wheelhouse \
    -r /tmp/hermes-requirements.txt
  "${HERMES_HOME}/hermes-agent/venv/bin/python" -m pip install --no-deps -e "${HERMES_HOME}/hermes-agent"
else
  "${HERMES_HOME}/hermes-agent/venv/bin/python" -m pip install "${pip_args[@]}" -e "${HERMES_HOME}/hermes-agent"
fi
ln -sfn "${HERMES_HOME}/hermes-agent/venv/bin/hermes" /home/ec2-user/.local/bin/hermes
if [[ -x /home/ec2-user/flyai-hermes-travel/node_modules/.bin/flyai ]]; then
  ln -sfn /home/ec2-user/flyai-hermes-travel/node_modules/.bin/flyai /home/ec2-user/.local/bin/flyai
fi

echo "Hermes version"
HERMES_HOME="${HERMES_HOME}" HOME=/home/ec2-user /home/ec2-user/.local/bin/hermes --version

echo "Checking flyai skill registration"
HERMES_HOME="${HERMES_HOME}" HOME=/home/ec2-user /home/ec2-user/.local/bin/hermes skills list | grep -i flyai

echo "Running Hermes oneshot smoke test"
timeout 180 env HERMES_HOME="${HERMES_HOME}" HOME=/home/ec2-user PATH="/home/ec2-user/flyai-hermes-travel/node_modules/.bin:/home/ec2-user/.local/bin:${PATH}" \
  /home/ec2-user/.local/bin/hermes chat -q "只回答 OK，不要调用工具。" --accept-hooks --skills flyai --source flyai-web --max-turns 2 --provider "${HERMES_PROVIDER}" -m "${HERMES_MODEL}"

echo "Hermes install complete"
