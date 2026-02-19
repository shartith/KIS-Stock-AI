#!/bin/bash
set -e

echo "🚀 Starting KIS-Stock-AI Integrated System..."
echo ""

# ==========================
# 0. Environment Setup
# ==========================
export PYTHONPATH=$PYTHONPATH:/app/kis-stock-ai:/app/kis-stock-ai/src:/app/kis-stock-ai/src/ai
export WEB_PORT=${WEB_PORT:-8000}

# Google OAuth Defaults (if not set)
# Default Client ID is public, but Secret must be provided by user
if [ -z "$GOOGLE_OAUTH_CLIENT_ID" ]; then
    export GOOGLE_OAUTH_CLIENT_ID="1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
fi

echo "📌 Configuration:"
echo "   Web Dashboard : Port $WEB_PORT"
echo "   Local AI      : External (11434)"
echo ""

# ==========================
# 1. Environment Variable Check
# ==========================
if [ -z "$KIS_APP_KEY" ] || [ -z "$KIS_SECRET_KEY" ]; then
    echo "⚠️ Warning: KIS_APP_KEY or KIS_SECRET_KEY is missing."
    echo "   Trading features will not work without these."
fi

if [ -z "$DISCORD_WEBHOOK_URL" ]; then
    echo "⚠️ Warning: DISCORD_WEBHOOK_URL is missing. Notifications disabled."
fi

echo ""

# ==========================
# 2. PM2 Ecosystem Configuration (Unified Process)
# ==========================
cat <<EOF > /app/ecosystem.config.js
module.exports = {
  apps : [
    // 1. Web Dashboard + Auto Trading Bot (Integrated) — Port $WEB_PORT
    {
      name: "kis-dashboard",
      cwd: "/app/kis-stock-ai",
      script: "uvicorn",
      args: "src.web.app:app --host 0.0.0.0 --port $WEB_PORT",
      interpreter: "none",
      autorestart: true,
      env: {
        PYTHONPATH: "/app/kis-stock-ai:/app/kis-stock-ai/src:/app/kis-stock-ai/src/ai"
      }
    }
  ]
}
EOF

# ==========================
# 3. Start PM2 Runtime
# ==========================
echo "🔥 Starting services with PM2..."
echo "   [1] kis-dashboard (FastAPI + Bot) → :$WEB_PORT"
echo ""

exec pm2-runtime start /app/ecosystem.config.js
