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

# swap (needed for LibreDWG compilation on small instances)
if ! swapon --show | grep -q swapfile; then
    sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
    sudo mkswap /swapfile > /dev/null && sudo swapon /swapfile
fi

# LibreDWG dwg2dxf (compile once; ~10-20 min on a small instance)
if [ ! -x /opt/greenplan/libredwg/bin/dwg2dxf ]; then
    echo "building LibreDWG (one-time)..."
    curl -sL -o /tmp/libredwg.tar.gz \
      https://github.com/LibreDWG/libredwg/releases/download/0.13.3/libredwg-0.13.3.tar.gz
    tar xzf /tmp/libredwg.tar.gz -C /tmp
    cd /tmp/libredwg-0.13.3
    ./configure --prefix=/opt/greenplan/libredwg --disable-bindings \
        --disable-python --disable-write --enable-release > /dev/null
    make -j1 CFLAGS="-O1 -g0 --param ggc-min-expand=5 --param ggc-min-heapsize=16384" > /dev/null
    make install > /dev/null
    cd /opt/greenplan
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
Environment=LOCAL_LLM_ENABLED=false
Environment=GREENPLAN_DATA_DIR=/opt/greenplan/data
Environment=GREENPLAN_OUTPUT_DIR=/opt/greenplan/outputs
Environment=GREENPLAN_RULES_FILE=/opt/greenplan/rules/regulations.yaml
Environment=GREENPLAN_SPECIES_FILE=/opt/greenplan/rules/species.yaml
ExecStart=/opt/greenplan/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
UNIT

# nginx: /api on port 80, SSE-friendly, large uploads
sudo tee /etc/nginx/sites-available/greenplan > /dev/null <<'NGINX'
server {
    listen 80 default_server;
    client_max_body_size 200m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_buffering off;
        proxy_read_timeout 600s;
        proxy_set_header Host $host;
    }
    location / {
        return 200 'GreenPlan API host. UI: see Vercel deployment. Health: /api/capabilities';
        add_header Content-Type text/plain;
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
