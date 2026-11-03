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
