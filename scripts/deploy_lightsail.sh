#!/usr/bin/env bash
# Deploy GreenPlan API to an Ubuntu Lightsail instance.
# Usage: scripts/deploy_lightsail.sh <pem> <user@host>
# Idempotent: safe to re-run for updates.
set -euo pipefail

PEM="$1"; TARGET="$2"
SSH="ssh -i $PEM -o UserKnownHostsFile=/tmp/known_hosts -o StrictHostKeyChecking=accept-new $TARGET"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> packaging backend"
TAR=/tmp/greenplan_api.tar.gz
tar czf "$TAR" -C "$REPO_DIR" \
    apps/api/app apps/api/scripts apps/api/tests apps/api/requirements.txt \
    rules fixtures/synthetic_site.dxf

echo "==> uploading"
scp -i "$PEM" -o UserKnownHostsFile=/tmp/known_hosts "$TAR" "$TARGET":/tmp/

echo "==> provisioning"
$SSH 'bash -s' <<'REMOTE'
set -euo pipefail
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3-venv python3-pip nginx gcc make curl > /dev/null

sudo mkdir -p /opt/greenplan && sudo chown "$USER" /opt/greenplan
tar xzf /tmp/greenplan_api.tar.gz -C /opt/greenplan
cd /opt/greenplan

python3 -m venv venv 2>/dev/null || true
./venv/bin/pip install -q -r apps/api/requirements.txt

# LibreDWG dwg2dxf (compile once)
if [ ! -x /opt/greenplan/libredwg/bin/dwg2dxf ]; then
    echo "building LibreDWG (one-time)..."
    MEM_MB=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
    if [ "$MEM_MB" -lt 3000 ]; then
        # low-memory boxes: swap + single-threaded, GC-thrifty compile
        if ! swapon --show | grep -q swapfile; then
            sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
            sudo mkswap /swapfile > /dev/null && sudo swapon /swapfile
        fi
        MAKE_ARGS='-j1 CFLAGS=-O1\ -g0\ --param\ ggc-min-expand=5\ --param\ ggc-min-heapsize=16384'
    else
        MAKE_ARGS="-j$(nproc)"
    fi
    curl -sL -o /tmp/libredwg.tar.gz \
      https://github.com/LibreDWG/libredwg/releases/download/0.13.3/libredwg-0.13.3.tar.gz
    tar xzf /tmp/libredwg.tar.gz -C /tmp
    cd /tmp/libredwg-0.13.3
    ./configure --prefix=/opt/greenplan/libredwg --disable-bindings \
        --disable-python --disable-write --enable-release > /dev/null
    eval make $MAKE_ARGS > /dev/null
    make install > /dev/null
    cd /opt/greenplan
fi

# runtime env (survives redeploys; edit /opt/greenplan/api.env to change LLM etc.)
if [ ! -f /opt/greenplan/api.env ]; then
cat > /opt/greenplan/api.env <<'ENV'
LOCAL_LLM_ENABLED=false
LOCAL_LLM_BASE_URL=http://127.0.0.1:8080
ENV
fi

# systemd service
sudo tee /etc/systemd/system/greenplan-api.service > /dev/null <<'UNIT'
[Unit]
Description=GreenPlan API
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/greenplan/apps/api
Environment=LIBREDWG_DWG2DXF_PATH=/opt/greenplan/libredwg/bin/dwg2dxf
Environment=GREENPLAN_DATA_DIR=/opt/greenplan/data
Environment=GREENPLAN_OUTPUT_DIR=/opt/greenplan/outputs
Environment=GREENPLAN_RULES_FILE=/opt/greenplan/rules/regulations.yaml
Environment=GREENPLAN_SPECIES_FILE=/opt/greenplan/rules/species.yaml
EnvironmentFile=-/opt/greenplan/api.env
ExecStart=/opt/greenplan/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
UNIT

# nginx: static UI (when deployed to /opt/greenplan/web) + /api proxy,
# SSE-friendly, large uploads
sudo mkdir -p /opt/greenplan/web && sudo chown "$USER" /opt/greenplan/web
[ -f /opt/greenplan/web/index.html ] || \
    echo 'GreenPlan API host. Deploy the web UI to /opt/greenplan/web. Health: /api/capabilities' \
    > /opt/greenplan/web/index.html
sudo tee /etc/nginx/sites-available/greenplan > /dev/null <<'NGINX'
server {
    listen 80 default_server;
    client_max_body_size 200m;
    root /opt/greenplan/web;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_buffering off;
        proxy_read_timeout 600s;
        proxy_set_header Host $host;
    }
    location / {
        try_files $uri /index.html;
    }
}
NGINX
sudo ln -sf /etc/nginx/sites-available/greenplan /etc/nginx/sites-enabled/default

sudo systemctl daemon-reload
sudo systemctl enable --now greenplan-api
sudo systemctl restart greenplan-api nginx
sleep 3
curl -sf http://127.0.0.1/api/capabilities > /dev/null && echo "DEPLOY OK"
REMOTE
