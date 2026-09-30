#!/usr/bin/env bash
# One-time setup on an EC2 GPU instance (AMI: "Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)").
#   curl -fsSL https://raw.githubusercontent.com/harsha7123/Vision_daycare/main/deploy/ec2/setup.sh | bash
# Optional first:  export DOMAIN=api.yourcompany.com   ALLOWED_ORIGINS=https://your-site.vercel.app
set -euo pipefail

echo "== checking GPU"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || { echo "No NVIDIA driver found. Use the Deep Learning GPU AMI."; exit 1; }

if ! command -v docker >/dev/null; then
  echo "== installing docker"
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER" || true
if ! docker info 2>/dev/null | grep -qi nvidia; then
  echo "== installing NVIDIA container toolkit"
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
  sudo apt-get update -qq && sudo apt-get install -y -qq nvidia-container-toolkit
  sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
fi

cd ~
[ -d Vision_daycare ] || git clone https://github.com/harsha7123/Vision_daycare.git
cd Vision_daycare && git pull --ff-only

if [ -z "${DOMAIN:-}" ]; then
  TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
  IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4)
  DOMAIN="${IP//./-}.sslip.io"            # free hostname that resolves to this IP, so HTTPS works without buying a domain
fi
if [ ! -f .env ]; then
  cat > .env <<ENV
DOMAIN=$DOMAIN
ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-*}
DAYCARE_DEVICE=cuda:0
MAX_LIVE=6
# Real parent calls (optional):
# TWILIO_ACCOUNT_SID=
# TWILIO_AUTH_TOKEN=
# TWILIO_FROM_NUMBER=
# ALLOWED_CALL_NUMBERS=+91XXXXXXXXXX
ENV
fi
export DOMAIN
echo "== building and starting (first build downloads ~10 GB, 5-15 min)"
sudo DOMAIN="$DOMAIN" docker compose -f deploy/ec2/compose.yaml up -d --build
echo
echo "Done. Backend URL:  https://$DOMAIN"
echo "Check:              https://$DOMAIN/api/health   (the certificate can take ~1 min on first start)"
echo "Set VITE_API_URL=https://$DOMAIN on Vercel and redeploy the frontend."
