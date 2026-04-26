#!/bin/sh

# Error out if anything fails.
set -e

# Make sure script is run as root.
if [ "$(id -u)" != "0" ]; then
  echo "Must be run as root with sudo! Try: sudo ./install.sh"
  exit 1
fi

echo "Installing system dependencies..."
echo "================================="
apt update && apt -y install \
    python3 python3-pip supervisor ntfs-3g exfat-fuse \
    mpv retroarch retroarch-assets libretro-nestopia ffmpeg \
    make gcc g++ python3-dev build-essential \
    i2c-tools python3-smbus

echo ""
echo "Installing Python dependencies..."
echo "================================="
pip3 install setuptools wheel
pip3 install -r "$(dirname "$0")/requirements.txt"

echo ""
echo "Installing video_looper program..."
echo "==================================="

# Change the directory to the script location
cd "$(dirname "$0")"

pip3 install .

# Create required directories
mkdir -p /mnt/usbdrive0
mkdir -p /home/pi/video
mkdir -p /var/lib/video_looper

# Copy configuration files
cp ./assets/video_looper.ini /boot/video_looper.ini
cp ./assets/video_looper.conf /etc/supervisor/conf.d/

echo ""
echo "Enabling I2C interface..."
echo "========================="
raspi-config nonint do_i2c 0

echo ""
echo "Enabling DRM/KMS display driver..."
echo "==================================="
# Add vc4-kms-v3d overlay if not already present
if ! grep -q "^dtoverlay=vc4-kms-v3d" /boot/config.txt; then
    echo "dtoverlay=vc4-kms-v3d" >> /boot/config.txt
    echo "Added dtoverlay=vc4-kms-v3d to /boot/config.txt"
else
    echo "dtoverlay=vc4-kms-v3d already enabled"
fi

echo ""
echo "Configuring video_looper to run on start..."
echo "============================================"
service supervisor restart

echo ""
echo "=========================================="
echo "Installation complete!"
echo ""
echo "Reboot for all changes to take effect."
echo "=========================================="
