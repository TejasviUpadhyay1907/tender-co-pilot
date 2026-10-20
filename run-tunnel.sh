#!/bin/bash
# Tunnel wrapper for the Tender Co-Pilot live preview.
# Keeps retrying the pinggy SSH tunnel; pinggy assigns a fresh public URL
# on each successful connect (see /tmp/tcp-tunnel-svc.log for the URL).
PROXY=$(cat /root/.tcp-proxy)
CREDS=$(python3 -c "from urllib.parse import urlparse; u=urlparse('$PROXY'.strip()); print(u.username+':'+u.password)")
while true; do
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=120 -o ServerAliveInterval=30 \
      -o ProxyCommand="socat STDIO PROXY:hatch-egress-proxy:%h:%p,proxyport=3128,proxyauth=${CREDS} 2>/dev/null" \
      -p 443 -R0:localhost:8000 a.pinggy.io >> /tmp/tcp-tunnel-svc.log 2>&1
  echo "ssh exited $? at $(date -u +%H:%M:%S)" >> /tmp/tcp-tunnel-svc.log
  sleep 120
done
