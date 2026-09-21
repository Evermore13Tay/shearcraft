#!/bin/bash
# Deploy the store to Cloudflare (Workers + D1 + R2).
# Usage: ./deploy-cf.sh
set -e
cd "$(dirname "$0")"
rm -rf public
mkdir -p public
cp index.html admin.html public/
cp -r images public/
npx wrangler deploy
