#!/usr/bin/env bash
# deploy/install.sh — Install and enable all Minervini systemd services.
# Usage: sudo bash deploy/install.sh
# Must be run from the project root: /home/ubuntu/projects/sepa_ai

set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Guards ─────────────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "This script must be run with sudo."

PROJECT_ROOT="/home/ubuntu/projects/sepa_ai"
VENV="${PROJECT_ROOT}/.venv"
DEPLOY_DIR="${PROJECT_ROOT}/deploy"
SYSTEMD_DIR="/etc/systemd/system"

# ── Pre-flight checks ──────────────────────────────────────────────────────────
info "Checking project root: ${PROJECT_ROOT}"
[[ -d "${PROJECT_ROOT}" ]] || error "Project root not found: ${PROJECT_ROOT}"

info "Checking Python venv: ${VENV}"
[[ -f "${VENV}/bin/python" ]] || \
    error "Python venv not found at ${VENV}. Run: cd ${PROJECT_ROOT} && python3 -m venv .venv && make install"

info "Checking .env file: ${PROJECT_ROOT}/.env"
[[ -f "${PROJECT_ROOT}/.env" ]] || \
    warn ".env file missing — services will start without environment overrides. Copy from .env.example if needed."

# ── Service files ──────────────────────────────────────────────────────────────
SERVICES=(
    "minervini-daily.service"
    "minervini-daily.timer"
    "minervini-api.service"
    "minervini-dashboard.service"
)

info "Copying service files to ${SYSTEMD_DIR}/"
for svc in "${SERVICES[@]}"; do
    src="${DEPLOY_DIR}/${svc}"
    [[ -f "${src}" ]] || error "Service file not found: ${src}"
    cp "${src}" "${SYSTEMD_DIR}/${svc}"
    chmod 644 "${SYSTEMD_DIR}/${svc}"
    info "  Installed: ${svc}"
done

# ── Reload systemd ─────────────────────────────────────────────────────────────
info "Running: systemctl daemon-reload"
systemctl daemon-reload

# ── Enable + start each unit ───────────────────────────────────────────────────
info "Enabling and starting minervini-api.service"
systemctl enable --now minervini-api.service

info "Enabling and starting minervini-dashboard.service"
systemctl enable --now minervini-dashboard.service

# The pipeline runs as a timer (not --now, so it fires on schedule, not immediately)
info "Enabling minervini-daily.timer (fires next weekday at 10:05 UTC)"
systemctl enable --now minervini-daily.timer

# ── Status summary ─────────────────────────────────────────────────────────────
echo ""
# ── Dashboard health check ─────────────────────────────────────────────────────
info "Waiting for dashboard to start…"
sleep 5
if curl -sf http://localhost:8501/healthz | grep -q "ok"; then
    info "Dashboard OK — http://localhost:8501"
else
    warn "Dashboard may not be ready yet (healthz did not return 'ok'). Check: journalctl -u minervini-dashboard.service -n 20"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Service status:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for unit in minervini-daily.timer minervini-api.service minervini-dashboard.service; do
    echo ""
    echo "▶  ${unit}"
    systemctl status "${unit}" --no-pager --lines=4 || true
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Deployment complete."
info "  API:       http://0.0.0.0:8000/docs"
info "  Dashboard: http://0.0.0.0:8501"
info "  Timer:     systemctl list-timers | grep minervini"
info "  Logs:      journalctl -u minervini-daily.service -f"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
