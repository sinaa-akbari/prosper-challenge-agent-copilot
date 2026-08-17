#!/usr/bin/env bash
#
# Ship the current working tree to the box.
#
# Builds the frontend locally (the server has no reason to carry a node
# toolchain), tars everything that isn't generated, and restarts the service.
# `.env` is deliberately excluded — production credentials differ from local
# ones and live only on the server, so a careless deploy can't overwrite them
# with a developer's laptop config.
#
#   ./deploy.sh              # build, upload, restart
#   ./deploy.sh --no-build   # skip the frontend build
#
set -euo pipefail

HOST="${DEPLOY_HOST:-root@212.227.246.190}"
DEST="${DEPLOY_DEST:-/opt/agent-composer}"
SERVICE="agent-composer"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$HERE"

if [[ "${1:-}" != "--no-build" ]]; then
  echo "==> building frontend"
  npm --prefix frontend run build >/dev/null
fi

echo "==> packaging"
TAR="$(mktemp -t composer-XXXXXX.tgz)"
tar --exclude='.venv' --exclude='node_modules' --exclude='__pycache__' \
    --exclude='.git' --exclude='backend/data' --exclude='backend/logs' \
    --exclude='backend/certs' --exclude='.env' --exclude='docs/shots' \
    --exclude='*.zip' --exclude='*.tgz' \
    -czf "$TAR" .

echo "==> uploading $(du -h "$TAR" | cut -f1)"
scp -q "$TAR" "$HOST:/tmp/composer.tgz"
rm -f "$TAR"

echo "==> installing"
ssh "$HOST" bash -s <<'REMOTE'
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
tar -xzf /tmp/composer.tgz -C /opt/agent-composer
rm -f /tmp/composer.tgz
cd /opt/agent-composer/backend
uv sync --quiet
systemctl restart agent-composer
REMOTE

echo "==> waiting for health"
for i in $(seq 1 30); do
  if ssh "$HOST" 'curl -sf --max-time 5 http://127.0.0.1:7860/api/health' >/dev/null 2>&1; then
    ssh "$HOST" 'curl -s http://127.0.0.1:7860/api/health'; echo
    echo "==> live at https://212.227.246.190.sslip.io"
    exit 0
  fi
  sleep 2
done

echo "!! service did not come up; last 30 log lines:" >&2
ssh "$HOST" "journalctl -u $SERVICE -n 30 --no-pager" >&2
exit 1
