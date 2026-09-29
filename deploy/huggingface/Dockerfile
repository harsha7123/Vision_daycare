# Hugging Face Space (Docker, free CPU Basic: 2 vCPU / 16 GB RAM) for the Vision Daycare analysis API.
# Create a Space with SDK "Docker", then upload this Dockerfile and README.md. The Space clones the
# GitHub repo at build time; after pushing new code, use Settings -> "Factory rebuild".
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1 \
    YOLO_CONFIG_DIR=/tmp/Ultralytics DAYCARE_DATA_DIR=/tmp/vision_daycare PORT=7860
WORKDIR /home/user/app

# CPU-only PyTorch keeps the image small (~1.5 GB instead of ~6 GB)
RUN pip install --no-cache-dir --user torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --user "ultralytics>=8.3" "supervision>=0.22" "fastapi>=0.110" "uvicorn[standard]>=0.25" \
    "httpx>=0.25" "python-multipart>=0.0.9" "pyyaml>=6"

ARG REPO=https://github.com/harsha7123/Vision_daycare.git
ARG REF=main
RUN git clone --depth 1 --branch ${REF} ${REPO} .
# bake the YOLO weights into the image so the first request doesn't download them
RUN python -c "from ultralytics import YOLO; YOLO('models/yolo11n-pose.pt'); YOLO('models/yolo11s.pt')"

EXPOSE 7860
CMD ["python", "run_cloud.py"]
