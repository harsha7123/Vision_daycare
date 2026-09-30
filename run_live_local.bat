@echo off
REM Local test on this PC (uses the NVIDIA GPU if CUDA PyTorch is installed):
REM builds the web app once, then serves web app + API + live camera on http://localhost:7860
cd /d "%~dp0"
if not exist web\dist\index.html (
  where npm >nul 2>nul || (echo Node.js is needed once to build the web app: https://nodejs.org & pause & exit /b 1)
  pushd web
  call npm install || (popd & pause & exit /b 1)
  call npm run build || (popd & pause & exit /b 1)
  popd
)
python -c "import torch;print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none - running on CPU (install CUDA PyTorch, see README)')"
REM Fast models (about 90 ms per frame on a laptop RTX 3050). For more accuracy on small or distant
REM phones, run:  run_live_local.bat accurate   (yolo11s-pose + yolo11m, about 170 ms per frame)
if /i "%1"=="accurate" (
  set POSE_MODEL=models/yolo11s-pose.pt
  set DET_MODEL=models/yolo11m.pt
)
start "" http://localhost:7860
python run_cloud.py
