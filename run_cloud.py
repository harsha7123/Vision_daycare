"""Cloud / web-app backend without installing:  python run_cloud.py  ->  http://localhost:7860/docs"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("daycare.cloud_api:create_app", factory=True, host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", 7860)), log_level="info")
