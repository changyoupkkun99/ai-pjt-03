"""Contract planning application."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
# Preserve the previous project's absolute imports under src.* and config.
sys.path.insert(0, str(ROOT / "legacy"))
load_dotenv(ROOT / ".env", override=False)


os.environ.setdefault("HF_HOME", str(ROOT / ".runtime/huggingface"))
