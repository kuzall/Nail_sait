import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Simple loader for .env without requiring extra pip packages
def _load_env():
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    os.environ.setdefault(key, val)

_load_env()

# Bot credentials & admins
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in raw_admin_ids.split(",") if x.strip().isdigit()]

# Server & Database
PORT = int(os.getenv("PORT", "8000"))
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://138.249.149.151.sslip.io/admin")

# Database in data/ folder with auto-migration
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "database.sqlite")

# Backward compatibility: auto-migrate root database.sqlite if present
_legacy_db = os.path.join(BASE_DIR, "database.sqlite")
if os.path.exists(_legacy_db) and not os.path.exists(DB_PATH):
    try:
        import shutil
        shutil.move(_legacy_db, DB_PATH)
    except Exception:
        pass

