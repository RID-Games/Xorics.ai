#!/usr/bin/env bash
# Xorics restart script — stops all services, starts them in the correct order.
# Safe to run anytime; idempotent. Call from bridge.py's restart endpoint or manually.
# Usage: bash restart.sh

set -e
cd "$(dirname "$0")"
source venv/bin/activate
mkdir -p logs

echo "=== Stopping bridge ==="
pkill -f "uvicorn bridge:app" 2>/dev/null || true

echo "=== Stopping xorics REPL ==="
tmux kill-session -t xorics 2>/dev/null || true

echo "=== Stopping llama-swap ==="
pkill -f "llama-swap" 2>/dev/null || true

echo "=== Starting bridge ==="
nohup uvicorn bridge:app --host 127.0.0.1 --port 8090 >> logs/bridge.log 2>&1 &

echo "=== Starting xorics REPL ==="
tmux new -s xorics -d "python xorics.py"

echo "=== Starting llama-swap ==="
nohup llama-swap --config llama-swap.yaml --listen 127.0.0.1:9090 >> logs/llama-swap.log 2>&1 &

echo "=== Waiting for bridge ==="
for i in $(seq 1 15); do
    sleep 1
    if curl -sf http://127.0.0.1:8090/healthz > /dev/null 2>&1; then
        echo "=== All services up ==="
        exit 0
    fi
    echo "  waiting... ($i/15)"
done

echo "=== WARNING: bridge did not respond within 15s ==="
exit 1
