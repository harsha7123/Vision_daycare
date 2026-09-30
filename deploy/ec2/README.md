# Hosting the backend on AWS EC2 (GPU): lowest latency, best accuracy

```
Laptop / phone browser (camera) ──wss:// frames──► EC2 GPU: Caddy (HTTPS) ─► API (YOLO pose + phone + ByteTrack + rules)
        ▲                                                                            │
        └──────────── boxes, skeletons, alerts (JSON, same connection) ◄────────────┘
Vercel web app (frontend) ─https──► same EC2 API for uploaded videos
```

## Expected speed (estimates; measure yours with the numbers shown in the Live screen)

| | This laptop's CPU (measured) | EC2 g4dn.xlarge, T4 GPU (estimate) |
|---|---|---|
| AI time per frame | 246 ms | 20–35 ms (yolo11s-pose + yolo11m, FP16) |
| Round trip per frame from the browser | 259 ms (local) | 60–120 ms from India to Mumbai (ap-south-1) |
| Live frames per second | ~4 | 8–12 |
| Alert after the rule's threshold is reached | ~1 s | ~0.3–0.5 s |

The rule thresholds themselves are the main "delay": e.g. phone alert after 6 s (Short clips), 12 s (Demo) or 120 s (Real centre).

## 1. Pick the instance

| Instance | GPU | Good for | Approx. on-demand price* |
|---|---|---|---|
| **g4dn.xlarge** (recommended start) | T4 16 GB | Demo + 4–6 live cameras | ~$0.55–0.65 / hour |
| g5.xlarge | A10G 24 GB | 2–3x faster, 10+ cameras | ~$1.0–1.2 / hour |
| g6.xlarge (if offered in your region) | L4 24 GB | Similar to g5, newer | ~$0.8–1.0 / hour |

\* Check https://aws.amazon.com/ec2/pricing/on-demand/ for your region. Running 24/7, a g4dn.xlarge costs roughly $400–470/month; **stop the instance when you're not demoing**, since a stopped instance only pays for its disk.

**Region:** `ap-south-1` (Mumbai) if your users are in India. Distance is the biggest part of the network delay.

## 2. Before you launch (one time)

1. **GPU quota:** new AWS accounts have **0 GPU quota**. Open **Service Quotas → Amazon EC2 → "Running On-Demand G and VT instances"** → *Request increase* to **4** (vCPUs; a g4dn.xlarge uses 4). Approval takes from minutes to a day.
2. **Key pair:** EC2 → Key pairs → Create (download the `.pem`).

## 3. Launch

EC2 → **Launch instance**:

| Setting | Value |
|---|---|
| Name | `vision-daycare` |
| AMI | search **"Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)"** (drivers, Docker and the NVIDIA toolkit are preinstalled) |
| Instance type | `g4dn.xlarge` |
| Key pair | the one from step 2 |
| Network: security group | allow **SSH 22 from My IP only**, **HTTP 80** and **HTTPS 443 from anywhere** |
| Storage | **60 GB** gp3 (the Docker image is large) |

Then **Elastic IPs → Allocate → Associate** it with the instance, so the IP (and the free HTTPS hostname based on it) never changes.

## 4. Install (one command)

```bash
ssh -i your-key.pem ubuntu@<ELASTIC-IP>
```
```bash
export ALLOWED_ORIGINS=https://your-site.vercel.app
```
```bash
curl -fsSL https://raw.githubusercontent.com/harsha7123/Vision_daycare/main/deploy/ec2/setup.sh | bash
```

The script checks the GPU, clones the repo, creates `.env`, builds the GPU image (first time: 5–15 min) and starts:
* **api**: the analysis server with yolo11s-pose + yolo11m on the GPU
* **caddy**: HTTPS with a free Let's Encrypt certificate on `https://<ip-with-dashes>.sslip.io`, a free hostname that points to your IP. With your own domain, `export DOMAIN=api.example.com` first and point its DNS A record at the Elastic IP.

At the end it prints your backend URL. Check it:

```bash
curl https://<ip-with-dashes>.sslip.io/api/health
```

## 5. Connect the frontend

Vercel → your project → **Settings → Environment Variables** → `VITE_API_URL` = `https://<ip-with-dashes>.sslip.io` → **Deployments → Redeploy**.

Open the site → **Live camera** → **Start live analysis** → allow the camera. The Live screen shows the round trip and AI times; the device should read `cuda:0`.

## 6. Everyday operations

| Task | Command (on the instance, in `~/Vision_daycare`) |
|---|---|
| Update to the latest code | `git pull && sudo docker compose -f deploy/ec2/compose.yaml up -d --build` |
| Logs | `sudo docker compose -f deploy/ec2/compose.yaml logs -f api` |
| Restart | `sudo docker compose -f deploy/ec2/compose.yaml restart` |
| Change settings (Twilio, origins, `MAX_LIVE`) | edit `.env`, then run the "restart" row above |
| Check the GPU is busy | `nvidia-smi` |

## 7. Accuracy tips for phone detection

* **Light:** light the person from the front, not a bright window behind them.
* **Distance:** phone detection works best within ~3 m of a laptop camera. For CCTV, use the camera's 1080p main stream.
* **Bigger models:** on a g5 you can set `DET_MODEL=models/yolo11l.pt` and `DET_IMGSZ=960` in `.env` for more accuracy on small or distant phones, at the cost of about 2x AI time.
* The alert needs a phone **in the hand and the head pointed at it** for the whole threshold time. A phone on the desk is ignored on purpose.

## 8. Real CCTV cameras with EC2

EC2 can't reach cameras inside the centre's private network. Choose one:
1. **Recommended:** run the on-premise edge app (`python run.py --source rtsp://...`, see the main README) on a GPU PC in the centre. There's no internet delay for the video, and footage never leaves the building.
2. Put a small always-on device at the centre (Raspberry Pi / mini PC) with **Tailscale** as a subnet router, and install Tailscale on the EC2 instance. EC2 can then read `rtsp://<camera-lan-ip>/...` privately. This adds internet upload bandwidth (~2–4 Mbps per 1080p camera) and 50–200 ms of delay.
