#!/bin/bash

echo "Installing dependencies..."
pip install pyyaml==5.4.1

echo "Installing switch-config..."
curl -fsSL -o /usr/bin/switch-config https://raw.githubusercontent.com/garet90/switch-config/refs/heads/main/platforms/opx/files/switch-config
chmod +x /usr/bin/switch-config

if [ -f "/etc/switch-config/config.yaml" ]; then
  echo "Configuration already exists. Leaving as is"
else
  echo "Creating default config..."
  mkdir -p /etc/switch-config
  curl -fsSL -o /etc/switch-config/config.yaml https://raw.githubusercontent.com/garet90/switch-config/refs/heads/main/examples/default.yaml
fi

echo "Creating service..."
curl -fsSL -o /lib/systemd/system/switch-config.service https://raw.githubusercontent.com/garet90/switch-config/refs/heads/main/platforms/opx/files/switch-config.service
systemctl enable switch-config.service
systemctl start switch-config.service

echo "Installation successful! Edit '/etc/switch-config/config.yaml' to configure your switch and 'systemctl restart switch-config' to reload"
