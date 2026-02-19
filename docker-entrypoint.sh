#!/bin/bash
set -e

echo "🚀 Starting KIS-Stock-AI Integrated System..."
echo ""

# ==========================
# 0. Environment Setup
# ==========================
export PYTHONPATH=$PYTHONPATH:/app/kis-stock-ai:/app/kis-stock-ai/src:/app/kis-stock-ai/src/ai
export WEB_PORT=${WEB_PORT:-80}
export APP_PORT=${APP_PORT:-8000}

# Google OAuth Defaults (if not set)
# Default Client ID is public, but Secret must be provided by user
if [ -z "$GOOGLE_OAUTH_CLIENT_ID" ]; then
    export GOOGLE_OAUTH_CLIENT_ID="1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
fi
# if [ -z "$GOOGLE_OAUTH_CLIENT_SECRET" ]; then
#     # Secret must be provided via ENV or Web UI
# fi

echo "📌 Port Configuration:"
echo "   Web Dashboard : $WEB_PORT"
echo "   Main Program  : $APP_PORT"
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
# 2. PM2 Ecosystem Configuration
# ==========================
cat <<EOF > /app/ecosystem.config.js
module.exports = {
  apps : [
    // 1. Web Dashboard (FastAPI/Uvicorn) — Port $WEB_PORT
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
    },
    // 2. Stock AI Bot (Main Auto Trading) — Port $APP_PORT
    {
      name: "kis-stock-ai",
      cwd: "/app/kis-stock-ai",
      script: "python3",
      args: "src/ai/main_auto.py --live",
      interpreter: "none",
      autorestart: true,
      restart_delay: 10000,
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
echo "   [1] kis-dashboard (FastAPI)     → :$WEB_PORT"
echo "   [2] kis-stock-ai  (Auto Trade)  → :$APP_PORT"
echo ""

exec pm2-runtime start /app/ecosystem.config.js
