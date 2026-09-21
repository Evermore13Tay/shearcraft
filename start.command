#!/bin/bash
# Double-click to start the store + admin on this Mac.
cd "$(dirname "$0")"
export ADMIN_PASSWORD=huitong-admin-2026   # ← change this to your own password
( sleep 1; open "http://127.0.0.1:8765/admin.html" ) &
exec python3 server.py 8765
