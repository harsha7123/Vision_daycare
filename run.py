"""Run without installing:  python run.py [--source sim|0|video.mp4|rtsp://...] [--profile demo|production]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from daycare.main import main  # noqa: E402

main()
