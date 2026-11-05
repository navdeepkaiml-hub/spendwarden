"""Settings, read from the environment and the .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PAYPAL_BASE_URL = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com").rstrip("/")
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")

# vault    = charge the buyer's saved payment token, no redirect (needs the vault spike to work)
# approval = create an order and hand back a link the buyer must approve
PAYMENT_MODE = os.getenv("PAYMENT_MODE", "vault").strip().lower()
VAULT_TOKEN_FILE = Path(os.getenv("VAULT_TOKEN_FILE", ".vault_token"))

DB_PATH = os.getenv("SPENDWARDEN_DB", "spendwarden.sqlite3")

# Intent verifier (LLM). Modes: gemini = ask Gemini, replay = answer only from the recorded cache
# (no API key needed, for judges and rehearsals), off = skip the verifier.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-3.1-flash-lite"
VERIFIER_MODE = os.getenv("VERIFIER_MODE", "gemini").strip().lower()
VERIFIER_RECORD = os.getenv("VERIFIER_RECORD", "0").strip().lower() in ("1", "true", "yes")
VERIFIER_CACHE_PATH = Path(os.getenv("VERIFIER_CACHE_PATH", "demo/replay/verifier_cache.json"))
VERIFIER_TIMEOUT_SECONDS = float(os.getenv("VERIFIER_TIMEOUT_SECONDS", "25"))
