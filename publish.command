#!/bin/bash
# Double-click to publish local product changes to the live website.
cd "$(dirname "$0")"
python3 publish.py
echo
read -n 1 -s -r -p "Press any key to close..."
echo
