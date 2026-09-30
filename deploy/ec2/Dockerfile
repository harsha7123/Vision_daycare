# GPU image for the Vision Daycare API (uploads + live camera) on an EC2 GPU instance.
# Base image already contains CUDA, PyTorch (GPU) and Ultralytics.
FROM ultralytics/ultralytics:latest

WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn[standard]>=0.25" "httpx>=0.25" \
        "python-multipart>=0.0.9" "pyyaml>=6" "supervision>=0.22"
COPY src/ src/
COPY configs/ configs/
COPY run_cloud.py .

# Bigger models than the free CPU host: better keypoints and much better small-phone detection.
ENV POSE_MODEL=models/yolo11s-pose.pt DET_MODEL=models/yolo11m.pt \
    DAYCARE_DATA_DIR=/data YOLO_CONFIG_DIR=/tmp/Ultralytics PYTHONUNBUFFERED=1 PORT=7860
RUN mkdir -p models && for i in 1 2 3 4 5; do       python -c "from ultralytics import YOLO; YOLO('models/yolo11s-pose.pt'); YOLO('models/yolo11m.pt')" && break;       echo "weights download failed, retrying"; sleep 10; done && test -s models/yolo11m.pt

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:7860/api/health')" || exit 1
CMD ["python", "run_cloud.py"]
