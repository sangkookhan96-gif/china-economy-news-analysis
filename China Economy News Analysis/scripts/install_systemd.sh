#!/bin/bash
# install_systemd.sh - Install and enable all systemd units
# Usage: sudo bash scripts/install_systemd.sh

set -euo pipefail

SYSTEMD_SRC="/home/jeozeohan/vibe_temp/China Economy News Analysis/systemd"
SYSTEMD_DST="/etc/systemd/system"

echo "=== Installing systemd units ==="

# Copy all service and timer files
for unit in "$SYSTEMD_SRC"/*.service "$SYSTEMD_SRC"/*.timer; do
    name=$(basename "$unit")
    echo "  Installing $name"
    cp "$unit" "$SYSTEMD_DST/$name"
done

# Reload systemd
echo "  Reloading systemd daemon..."
systemctl daemon-reload

# Enable and start timers
for timer in news-morning news-afternoon news-evening news-watchdog; do
    echo "  Enabling ${timer}.timer"
    systemctl enable "${timer}.timer"
    systemctl start "${timer}.timer"
done

# Enable and start scheduler daemon
echo "  Enabling news-scheduler.service"
systemctl enable news-scheduler.service
systemctl start news-scheduler.service

echo ""
echo "=== Installation complete ==="
echo ""
echo "Verify with:"
echo "  systemctl list-timers 'news-*'"
echo "  systemctl status news-scheduler"
echo ""

# Remove old cron entries (backup first)
echo "=== Migrating from cron ==="
CRON_BACKUP="/home/jeozeohan/crontab_backup_$(date +%Y%m%d).txt"
crontab -u jeozeohan -l > "$CRON_BACKUP" 2>/dev/null || true
echo "  Cron backup saved to: $CRON_BACKUP"

# Comment out old news-related cron entries
crontab -u jeozeohan -l 2>/dev/null | sed \
    -e 's|^\(.*run_daily_selection.*\)|# MIGRATED TO SYSTEMD: \1|' \
    -e 's|^\(@reboot.*scheduler_agent.*\)|# MIGRATED TO SYSTEMD: \1|' \
    | crontab -u jeozeohan -
echo "  Old cron entries commented out (prefixed with '# MIGRATED TO SYSTEMD')"
echo ""
echo "Done."
