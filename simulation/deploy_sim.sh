#!/bin/bash
# Deploy Power Manager simulation helpers + dashboard to Home Assistant
# Usage: ./simulation/deploy_sim.sh <ha-ip>
#
# Run from the repo root:
#   bash simulation/deploy_sim.sh 192.168.1.10

HA_IP=${1:?Usage: $0 <ha-ip>}
HA_USER=root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "→ Creating /config/packages/ on HA..."
ssh "$HA_USER@$HA_IP" "mkdir -p /config/packages"

echo "→ Copying simulation helpers package..."
scp "$SCRIPT_DIR/power_manager_sim.yaml" "$HA_USER@$HA_IP:/config/packages/power_manager_sim.yaml"

echo "→ Copying simulation dashboard YAML..."
scp "$SCRIPT_DIR/pm_sim_dashboard.yaml" "$HA_USER@$HA_IP:/config/pm_sim_dashboard.yaml"

echo ""
echo "✓ Files copied."
echo ""
echo "──────────────────────────────────────────────────────────"
echo "Add the following to /config/configuration.yaml on HA:"
echo "──────────────────────────────────────────────────────────"
cat <<'EOF'

homeassistant:
  packages: !include_dir_named packages/

lovelace:
  dashboards:
    power-manager-sim:
      mode: yaml
      title: PM Simulation
      icon: mdi:solar-power
      show_in_sidebar: true
      filename: pm_sim_dashboard.yaml
EOF
echo "──────────────────────────────────────────────────────────"
echo ""
echo "Then restart Home Assistant."
echo "The 'PM Simulation' dashboard will appear in the sidebar."
