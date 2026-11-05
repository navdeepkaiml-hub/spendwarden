"""Thin Gemini wrapper that returns a JSON object. Imported lazily so tests need no SDK."""
import json

from server import config


class LLMError(Exception):
    pass


def generate_json(system, payload):
    if not config.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set")
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=int(config.VERIFIER_TIMEOUT_SECONDS * 1000)),
        )
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        text = (resp.text or "").strip()
    except Exception as exc:  # network, quota, bad model name, SDK changes
        raise LLMError("Gemini call failed: %s" % str(exc)[:200].replace("\n", " "))
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except ValueError:
        raise LLMError("Gemini did not return valid JSON")
    if not isinstance(data, dict):
        raise LLMError("Gemini returned JSON that is not an object")
    return data
