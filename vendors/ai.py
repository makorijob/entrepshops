"""AI client for product suggestions with graceful provider fallback.

Chain:
  1. Google Gemini (gemini-3.8-flash)       — free tier, best quality
  2. Groq          (llama-3.3-70b-versatile) — free tier, fastest fallback

If a provider fails (rate limit, auth, timeout, unparseable response),
the next one is tried automatically. If every provider fails, `None` is
returned — the view never raises, never 502s.

Enforces a total time budget so the request returns before Vercel's
10-second serverless timeout kills it.
"""

import base64
import json
import logging
import os
import random
import time

from django.conf import settings

log = logging.getLogger(__name__)


# Vercel Hobby plan gives functions 10 seconds. Leave headroom.
TOTAL_BUDGET_SECONDS = 7.0

# Per-provider: attempts and backoff.
ATTEMPTS_PER_PROVIDER = 1
BASE_WAIT_SECONDS = 1.5

# Try at most this many providers before giving up.
MAX_PROVIDERS = 2


SYSTEM_PROMPT = """You are a product classification assistant for an online shop in Kenya called Entrep Shop.

A seller has given you a short description and/or an image of a product they want to list.

Your job: return a JSON object with everything needed to create a good product listing on the shop.

You MUST return pure JSON matching this schema exactly:

{
  "title": "<a clear, specific product name, e.g. 'HP Pavilion 15 Intel Core i5 8GB RAM 256GB SSD Laptop'>",
  "description": "<a 2-4 sentence sales description in English. Neutral tone. No emoji. Mention what the product is, who it's for, and 2-3 key features. Do NOT invent specs you don't see.>",
  "department": "<ONE of the exact department names listed below>",
  "category": "<A short category name, 1-3 words, e.g. 'Computers', 'Smart Screens', 'Fridges', 'Audio'>",
  "price_low_kes": <integer, lowest fair retail price in Kenyan Shillings>,
  "price_high_kes": <integer, highest fair retail price in Kenyan Shillings>,
  "price_notes": "<one short sentence explaining the price range, e.g. 'Entry-level laptops in Kenya range from KES 35,000 to 60,000 depending on RAM and storage.'>"
}

Rules:
- Prices must be in KES and reflect the Kenyan retail market as of 2025. Be conservative.
- Choose the department from the list provided. If nothing fits well, pick the closest.
- Category should be short and reusable. Prefer existing categories listed below.
- Do not add fields. Do not wrap the JSON in markdown. Return only the JSON object.
"""


# ---------------------------------------------------------------------------
# Provider chain configuration
# ---------------------------------------------------------------------------
#
# Each entry: (name, callable). The callable must accept (prompt, image_file)
# and either return a raw text string, or raise. The first success wins.

PROVIDER_CHAIN = ['gemini', 'groq']


def _is_gemini_configured():
    return bool(
        getattr(settings, 'GEMINI_API_KEY', '') or os.environ.get('GEMINI_API_KEY')
    )


def _is_groq_configured():
    return bool(
        getattr(settings, 'GROQ_API_KEY', '') or os.environ.get('GROQ_API_KEY')
    )


def _gemini_key():
    return getattr(settings, 'GEMINI_API_KEY', '') or os.environ.get('GEMINI_API_KEY', '')


def _groq_key():
    return getattr(settings, 'GROQ_API_KEY', '') or os.environ.get('GROQ_API_KEY', '')


# ---------------------------------------------------------------------------
# Provider: Gemini
# ---------------------------------------------------------------------------
def _call_gemini(prompt, image_file, timeout):
    """
    One attempt against Google Gemini using the new google-genai SDK.
    Returns raw response text. Raises on any failure.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise RuntimeError('google-genai not installed')

    api_key = _gemini_key()
    if not api_key:
        raise RuntimeError('GEMINI_API_KEY not configured')

    # New SDK: create a client object (no more genai.configure()).
    client = genai.Client(api_key=api_key)

    model_name = getattr(settings, 'GEMINI_MODEL', '') or 'gemini-3.8-flash'

    # Build contents — image + text if an image was supplied.
    contents = [prompt]
    if image_file:
        try:
            image_file.seek(0)
            image_bytes = image_file.read()
            mime_type = getattr(image_file, 'content_type', 'image/jpeg') or 'image/jpeg'
            contents.append(
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            )
        except Exception:
            log.exception('Could not read uploaded image for Gemini; text only.')

    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.4,
            max_output_tokens=800,
            response_mime_type='application/json',
        ),
    )

    text = getattr(response, 'text', None)
    if not text:
        raise RuntimeError('Gemini returned empty response')
    return text
# ---------------------------------------------------------------------------
# Provider: Groq
# ---------------------------------------------------------------------------

def _call_groq(prompt, image_file, timeout):
    """
    One attempt against Groq. Returns raw response text.
    Raises on any failure.
    """
    try:
        from groq import Groq
    except ImportError:
        raise RuntimeError('groq not installed')

    api_key = _groq_key()
    if not api_key:
        raise RuntimeError('GROQ_API_KEY not configured')

    # Text-only model: llama-3.3-70b-versatile (fast, free, widely available)
    # Vision model: qwen/qwen3.8-27b (supports images, free tier)
    model_name = getattr(settings, 'GROQ_MODEL', '') or 'llama-3.3-70b-versatile'

    client = Groq(api_key=api_key, timeout=timeout)

    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': prompt},
    ]

    # If an image is supplied, swap to a vision-capable model and attach it.
    if image_file:
        try:
            image_file.seek(0)
            image_bytes = image_file.read()
            mime_type = getattr(image_file, 'content_type', 'image/jpeg') or 'image/jpeg'
            b64 = base64.b64encode(image_bytes).decode('ascii')
            model_name = (
                getattr(settings, 'GROQ_VISION_MODEL', '')
                or 'qwen/qwen3.8-27b'
            )
            messages[1] = {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {
                        'type': 'image_url',
                        'image_url': {'url': f'data:{mime_type};base64,{b64}'},
                    },
                ],
            }
        except Exception:
            log.exception('Could not attach image for Groq; text only.')

    completion = client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=0.4,
        max_tokens=800,
        response_format={'type': 'json_object'},
    )

    text = completion.choices[0].message.content if completion.choices else ''
    if not text:
        raise RuntimeError('Groq returned empty response')
    return text


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

_PROVIDERS = {
    'gemini': (_call_gemini, _is_gemini_configured),
    'groq':   (_call_groq,   _is_groq_configured),
}


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(text, departments, categories):
    parts = [SYSTEM_PROMPT]

    if departments:
        parts.append(
            '\nAvailable departments (you MUST choose one of these exact names):\n'
            + '\n'.join(f'- {d}' for d in departments)
        )

    if categories:
        parts.append(
            '\nExisting categories already in the shop (prefer these if the item fits):\n'
            + '\n'.join(f'- {c}' for c in categories)
        )

    if text:
        parts.append(f'\nSeller typed: "{text.strip()[:400]}"')

    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def suggest_product(text, image_file=None):
    """Try each configured provider in order. Returns a dict, or None on
    total failure. Never raises.
    """
    start = time.monotonic()

    if not text and not image_file:
        return None

    # Any provider configured at all?
    configured = [name for name in PROVIDER_CHAIN if _PROVIDERS[name][1]()]
    if not configured:
        log.warning(
            'AI suggestions disabled: no provider configured '
            '(need GEMINI_API_KEY and/or GROQ_API_KEY).'
        )
        return None

    # Departments and categories to feed the prompt.
    try:
        from store.models import Category, Department
        departments = list(
            Department.objects.filter(is_active=True).values_list('name', flat=True)
        )
        categories = list(Category.objects.values_list('name', flat=True))
    except Exception:
        departments = []
        categories = []

    prompt = _build_prompt(text, departments, categories)

    if image_file:
        prompt += (
            '\n\nAn image of the product is attached. '
            'Analyse it to refine the title, description, and pricing.'
        )

    log.info('AI: provider chain=%s', configured[:MAX_PROVIDERS])

    for provider_name in configured[:MAX_PROVIDERS]:
        elapsed = time.monotonic() - start
        remaining = TOTAL_BUDGET_SECONDS - elapsed

        if remaining <= 1.5:
            log.warning(
                'AI: skipping %s — only %.1fs left in budget.',
                provider_name, remaining,
            )
            break

        for attempt in range(ATTEMPTS_PER_PROVIDER):
            if time.monotonic() >= start + TOTAL_BUDGET_SECONDS - 1.5:
                break

            call_fn, _ = _PROVIDERS[provider_name]
            provider_timeout = max(2.0, remaining - 0.5)

            try:
                raw = call_fn(prompt, image_file, provider_timeout)
            except Exception as exc:
                wait = BASE_WAIT_SECONDS + random.uniform(0, 1.0)
                log.warning(
                    'AI provider %s failed (attempt %d/%d): %s',
                    provider_name, attempt + 1, ATTEMPTS_PER_PROVIDER,
                    str(exc)[:200],
                )
                if attempt < ATTEMPTS_PER_PROVIDER - 1 and \
                        time.monotonic() + wait < start + TOTAL_BUDGET_SECONDS - 1.5:
                    time.sleep(wait)
                    continue
                break  # move on to the next provider

            parsed = _parse_response(raw)
            if parsed is not None:
                log.info(
                    'AI: success via %s (%.1fs)',
                    provider_name, time.monotonic() - start,
                )
                parsed['_provider'] = provider_name
                return parsed

            log.warning('AI: %s returned unparseable output.', provider_name)
            break  # move on

    log.warning('AI: all providers exhausted or budget exceeded.')
    return None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_response(raw):
    if not raw:
        return None

    cleaned = raw.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.strip('`')
        if cleaned.lower().startswith('json'):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        log.exception('AI returned invalid JSON: %s', cleaned[:500])
        return None

    required = ('title', 'description', 'department', 'category')
    if not all(data.get(k) for k in required):
        log.warning('AI response missing required fields: %s', data)
        return None

    return {
        'title': str(data.get('title', ''))[:200],
        'description': str(data.get('description', ''))[:5000],
        'department': str(data.get('department', '')).strip(),
        'category': str(data.get('category', '')).strip(),
        'price_low': _safe_int(data.get('price_low_kes')),
        'price_high': _safe_int(data.get('price_high_kes')),
        'price_notes': str(data.get('price_notes', ''))[:300],
    }


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Helper for templates: is AI available at all?
# ---------------------------------------------------------------------------

def is_ai_configured():
    """True if any provider has a key set. Use in views to conditionally
    render the "Auto-fill with AI" button."""
    return any(fn() for _, fn in _PROVIDERS.values())
