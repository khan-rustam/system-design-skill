#!/usr/bin/env bash
# Deploy the latest main to this VM. Run it on each VM in turn.
set -e
cd /srv/shop-orders
git pull origin main
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart shop
sleep 3
curl -fsS http://127.0.0.1:8000/health && echo "deploy ok"
