#!/usr/bin/env bash
# What this script does:
#   1. Stops any running Docker Compose services and removes volumes (clean start)
#   2. Removes and recreates /tmp data directories used by Docker volumes
#   3. Starts the infrastructure services (database, messagebus, cache) via Docker Compose
#   4. Starts MailHog (SMTP trap on :1025, web UI on :8025)
#
# Usage:
#   chmod +x setup-docker.sh
#   ./setup-docker.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MZINGA_DIR="$SCRIPT_DIR/mzinga/mzinga-apps"

# ── 1. Stop existing containers and remove volumes ────────────────────────────
echo ""
echo "==> [1/4] Stopping existing Docker Compose services and removing volumes..."
(cd "$MZINGA_DIR" && docker compose down -v --remove-orphans) || true
echo "    Done."

# ── 2. Clean and recreate tmp data directories ────────────────────────────────
echo ""
echo "==> [2/4] Cleaning and recreating /tmp data directories..."
rm -rf /tmp/database /tmp/mzinga /tmp/messagebus
mkdir -p /tmp/database /tmp/mzinga /tmp/messagebus
echo "    /tmp/database, /tmp/mzinga, /tmp/messagebus created."

# ── 3. Start infrastructure services in the background ───────────────────────
echo ""
echo "==> [3/4] Starting Docker infrastructure services (database, messagebus, cache)..."
(cd "$MZINGA_DIR" && docker compose up database messagebus cache --detach)
echo "    Infrastructure started in the background."
echo "    Run 'docker compose logs -f' inside mzinga/mzinga-apps/ to follow logs."

# ── 4. Start MailHog ──────────────────────────────────────────────────────────
echo ""
echo "==> [4/4] Starting MailHog (SMTP on :1025, web UI on :8025)..."
docker rm -f mailhog 2>/dev/null || true
docker run -d --name mailhog -p 1025:1025 -p 8025:8025 mailhog/mailhog
echo "    MailHog started. Open http://localhost:8025 to view caught emails."