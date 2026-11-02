"""Day 1 check: confirm your Gemini API key works and find a model name that works.

Usage:
    python scripts/gemini_check.py

Needs GEMINI_API_KEY in .env. Optionally set GEMINI_MODEL to try that name first.
"""
import json
import os
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

key = os.getenv("GEMINI_API_KEY", "")
if not key:
    sys.exit("ERROR: GEMINI_API_KEY missing. Copy .env.example to .env and fill it in.")

client = genai.Client(api_key=key)

candidates = [
    os.getenv("GEMINI_MODEL", ""),
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
]

seen = set()
working = None
for name in candidates:
    if not name or name in seen:
        continue
    seen.add(name)
    try:
        resp = client.models.generate_content(model=name, contents="Reply with exactly: ok")
        print("OK    %s -> %s" % (name, (resp.text or "").strip()))
        working = name
        break
    except Exception as exc:
        print("FAIL  %s -> %s" % (name, str(exc)[:140].replace("\n", " ")))

if not working:
    print("\nNo candidate worked. Models your key can see that mention 'flash':")
    try:
        for m in client.models.list():
            if "flash" in m.name:
                print("  " + m.name)
    except Exception as exc:
        print("  could not list models: %s" % exc)
    sys.exit(1)

print("\nTesting structured JSON output (the verifier will need this)...")
prompt = (
    "A user asked an agent to buy a birthday gift under 40 USD. The agent wants to buy "
    "a 400 USD gift card. Return JSON with keys match (boolean) and reason (string)."
)
resp = client.models.generate_content(
    model=working,
    contents=prompt,
    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
)
try:
    print(json.dumps(json.loads(resp.text), indent=2))
except Exception:
    print("Model did not return clean JSON. Raw output:\n" + (resp.text or ""))

print("\nPut this in .env:  GEMINI_MODEL=%s" % working)
