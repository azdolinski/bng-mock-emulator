import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

API_HOST = os.environ.get("BNG_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("BNG_API_PORT", "8080"))

COA_HOST = os.environ.get("BNG_COA_HOST", "0.0.0.0")
COA_PORT = int(os.environ.get("BNG_COA_PORT", "3799"))
COA_SECRET = os.environ.get("BNG_COA_SECRET", "testing123")

DICTIONARY = os.environ.get("BNG_DICTIONARY", str(APP_ROOT / "dictionary"))

DEFAULT_NAS_IP = os.environ.get("BNG_NAS_IP", "10.255.0.1")
DEFAULT_RADIUS_SECRET = os.environ.get("BNG_RADIUS_SECRET", "testing123")
