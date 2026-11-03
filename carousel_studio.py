"""Carousel Studio: validated one-carousel OpenRouter rewriting. No image generation.
Rendering and asset selection live in the browser for immediate local previews.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from avatar_studio import api_key as configured_api_key

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4.1-mini"
MAX_BODY = 100_000
MAX_SLIDES = 40
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{2,95}\Z")
KEY_RE = re.compile(r"sk-or-[A-Za-z0-9_-]{6,256}\Z")


class CarouselError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def parse_rewrite_payload(payload: dict) -> tuple[list[dict], str, str, str]:
    if not isinstance(payload, dict):
        raise CarouselError("invalid_json", "Нужен JSON-объект.")
    raw_slides = payload.get("slides")
    if not isinstance(raw_slides, list) or not 1 <= len(raw_slides) <= MAX_SLIDES:
        raise CarouselError("invalid_slides", "Отправьте от 1 до 40 слайдов.")
    model = payload.get("model") or DEFAULT_MODEL
    if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
        raise CarouselError("invalid_model", "Некорректный идентификатор LLM.")
    key = payload.get("api_key", "")
    if not isinstance(key, str) or (key and not KEY_RE.fullmatch(key)):
        raise CarouselError("invalid_key", "Некорректный формат ключа OpenRouter.")
    language = payload.get("language", "ru")
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}(-[A-Z]{2})?", language):
        raise CarouselError("invalid_language", "Некорректный язык.")
    slides = []
    seen = set()
    total = 0
    for i, item in enumerate(raw_slides):
        if not isinstance(item, dict):
            raise CarouselError("invalid_slide", "Каждый слайд должен быть объектом.")
        slide_id = str(item.get("id", i + 1))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", slide_id) or slide_id in seen:
            raise CarouselError("invalid_slide_id", "ID слайдов должны быть уникальными и безопасными.")
        seen.add(slide_id)
        texts = item.get("texts")
        limits = item.get("max_chars")
        if not isinstance(texts, list) or not 1 <= len(texts) <= 6 or not all(
                isinstance(x, str) and 1 <= len(x.strip()) <= 900 for x in texts):
            raise CarouselError("invalid_text", f"Слайд {slide_id}: от 1 до 6 текстовых блоков.")
        if limits is None:
            limits = [max(40, min(900, len(x) * 2)) for x in texts]
        if not isinstance(limits, list) or len(limits) != len(texts) or not all(
                isinstance(n, int) and not isinstance(n, bool) and 10 <= n <= 1200 for n in limits):
            raise CarouselError("invalid_limits", f"Слайд {slide_id}: некорректные лимиты текста.")
        total += sum(map(len, texts))
        slides.append({"id": slide_id, "texts": [x.strip() for x in texts], "max_chars": limits,
                       "role": str(item.get("role", ""))[:80]})
    if total > 18_000:
        raise CarouselError("too_long", "Текст карусели слишком большой (лимит 18 000 символов).")
    return slides, model, key, language


def validate_rewrite(slides: list[dict], result: object) -> list[dict]:
    if not isinstance(result, dict) or not isinstance(result.get("slides"), list):
        raise CarouselError("bad_provider_output", "LLM вернула некорректную структуру JSON.", 502)
    incoming = result["slides"]
    if len(incoming) != len(slides):
        raise CarouselError("bad_provider_output", "LLM изменила количество слайдов.", 502)
    output = []
    for original, item in zip(slides, incoming):
        if not isinstance(item, dict) or str(item.get("id", "")) != original["id"]:
            raise CarouselError("bad_provider_output", "LLM изменила порядок или ID слайдов.", 502)
        texts = item.get("texts")
        if not isinstance(texts, list) or len(texts) != len(original["texts"]):
            raise CarouselError("bad_provider_output", "LLM изменила число текстовых блоков.", 502)
        normalized = []
        for value, limit in zip(texts, original["max_chars"]):
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
                raise CarouselError("bad_provider_output", "LLM превысила лимит текста или вернула пустой блок.", 502)
            normalized.append(value.strip())
        output.append({"id": original["id"], "texts": normalized})
    return output


def rewrite(slides: list[dict], model: str, key: str, language: str) -> dict:
    system = (
        "You are a careful carousel copy editor. Rewrite each provided slide text in the requested "
        "language, preserving the exact meaning, factual claims, first-person perspective, "
        "emotional sequence, CTA intent, names and numbers. Do NOT invent results, testimonials, "
        "medical efficacy, guarantees, or personal experiences. Do NOT add new facts. "
        "Do not change slide IDs, ordering, or the number of text blocks. Respect max_chars "
        "for each block. Return ONLY a JSON object shaped "
        '{"slides":[{"id":"1","texts":["rewritten block 1","rewritten block 2"]}]}. '
        "No markdown, no explanations."
    )
    body = {
        "model": model,
        "temperature": 0.35,
        "max_tokens": 7500,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(
                {"language": language, "slides": slides}, ensure_ascii=False)},
        ],
    }
    request = Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                               "HTTP-Referer": "http://localhost:8765", "X-Title": "Content Machine Carousel Studio"},
                      method="POST")
    try:
        with urlopen(request, timeout=110) as response:
            raw = response.read(500_001)
    except HTTPError as exc:
        error_messages = {
            400: "Провайдер отклонил запрос или формат JSON. Попробуйте другую LLM.",
            401: "OpenRouter отклонил API-ключ.",
            402: "Недостаточно средств на балансе OpenRouter.",
            403: "Доступ к модели запрещён.",
            404: "Модель не найдена в OpenRouter.",
            429: "Превышен лимит запросов OpenRouter.",
        }
        raise CarouselError("provider_http", error_messages.get(
            exc.code, f"Ошибка провайдера HTTP {exc.code}."), 502) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise CarouselError("network", "Не удалось связаться с OpenRouter. Повторять платный "
                            "запрос автоматически не будем.", 502) from None
    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("empty completion")
        parsed = json.loads(content)
        rewritten = validate_rewrite(slides, parsed)
    except (ValueError, TypeError, KeyError, IndexError, CarouselError) as exc:
        if isinstance(exc, CarouselError):
            raise
        raise CarouselError("bad_provider_output", "LLM вернула некорректный JSON. "
                            "Исходный текст остался доступен.", 502) from None
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    cost = usage.get("cost")
    return {
        "slides": rewritten,
        "model": model,
        "usage": {"prompt_tokens": usage.get("prompt_tokens"),
                  "completion_tokens": usage.get("completion_tokens"),
                  "cost_usd": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0 else None},
    }


def handle_get(handler, url) -> bool:
    if url.path != "/api/carousel-studio/status":
        return False
    handler.json_response(200, {"configured": bool(configured_api_key()), "default_model": DEFAULT_MODEL})
    return True


def handle_post(handler, route: str) -> bool:
    if route != "/api/carousel-studio/rewrite":
        return False
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 2 <= length <= MAX_BODY:
            raise CarouselError("invalid_size", "Некорректный размер запроса.")
        data = json.loads(handler.rfile.read(length).decode("utf-8"))
        slides, model, key, language = parse_rewrite_payload(data)
        key = key or configured_api_key()
        if not key:
            raise CarouselError("key_missing", "Для перефразирования настройте OpenRouter API Key. "
                                "Предпросмотр без LLM доступен.", 503)
        result = rewrite(slides, model, key, language)
        handler.json_response(200, result)
    except CarouselError as exc:
        handler.json_response(exc.status, {"error": {"code": exc.code, "message": str(exc)}})
    except (json.JSONDecodeError, UnicodeError, ValueError):
        handler.json_response(400, {"error": {"code": "invalid_json", "message": "Не удалось прочитать JSON."}})
    return True
