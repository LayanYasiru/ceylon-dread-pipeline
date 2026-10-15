"""Thin Gemini wrapper (free tier). Returns parsed JSON."""
import json
import re
import time

from config import GEMINI_API_KEY, GEMINI_MODEL


def _clean_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text


def generate_json(prompt: str, system: str = "", temperature: float = 0.9, retries: int = 3) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey and put it in .env")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system or None,
                    temperature=temperature,
                    response_mime_type="application/json",
                ),
            )
            return json.loads(_clean_json(resp.text))
        except Exception as e:  # network, quota, bad JSON
            last_err = e
            print(f"  [LLM attempt {attempt}/{retries}] {type(e).__name__}: {str(e)[:160]}")
            time.sleep(8 * attempt)
    raise RuntimeError(f"LLM failed after {retries} attempts: {last_err}")
