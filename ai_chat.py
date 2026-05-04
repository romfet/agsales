"""
AI Chat assistant — helps managers add order lines via natural language.
Uses OpenRouter API with Gemini 2.5 Flash.
"""
import os
import json
import requests
import data_store

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")


def _build_system_prompt() -> str:
    """Build system prompt with current product catalog."""
    products_n3 = data_store.get_products_n3()
    catalog_lines = []
    for n3 in products_n3:
        n4_list = data_store.get_products_n4(n3)
        n4_str = ", ".join(n4_list)
        catalog_lines.append(f"  {n3}: [{n4_str}]")
    catalog = "\n".join(catalog_lines)

    return f"""Ты — ИИ-ассистент менеджера по продажам металлопроката. Помогаешь быстро добавлять позиции в заявку клиента.

КАТАЛОГ ТОВАРОВ (N3 — подгруппа, внутри — N4 товары):
{catalog}

ТВОЯ ЗАДАЧА:
1. Менеджер описывает товар на естественном языке (например: "профильная 40 на 20 двойка 5 тонн", "лист горячекатаный 8мм", "арматура 12").
2. Найди наиболее подходящий товар в каталоге.
3. Если нашёл один точный вариант — предложи его.
4. Если несколько похожих — покажи список и попроси уточнить.
5. Если не нашёл — скажи об этом.

ФОРМАТ ОТВЕТА:
Всегда отвечай в JSON:
{{
  "message": "Текст для менеджера на русском",
  "suggestions": [
    {{
      "n3": "точное название N3 из каталога",
      "n4": "точное название N4 из каталога",
      "qty": число_тонн_или_null
    }}
  ]
}}

- "suggestions" — массив найденных товаров (может быть пустым если ничего не найдено)
- Если менеджер указал количество — поставь в qty (число). Если не указал — null.
- Названия n3 и n4 должны ТОЧНО совпадать с каталогом.
- Если менеджер просто общается (здоровается, спрашивает не про товар) — отвечай с пустым suggestions.
- Будь кратким и дружелюбным. Отвечай всегда на русском."""


def chat(messages: list[dict], user_message: str) -> dict:
    """
    Send chat message to OpenRouter.

    Args:
        messages: conversation history [{"role": "user"|"assistant", "content": "..."}]
        user_message: new user message

    Returns:
        {"message": "...", "suggestions": [...]}
    """
    if not API_KEY:
        return {"message": "API-ключ OpenRouter не настроен.", "suggestions": []}

    system_prompt = _build_system_prompt()

    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        api_messages.append({"role": msg["role"], "content": msg["content"]})
    api_messages.append({"role": "user", "content": user_message})

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": api_messages,
                "temperature": 0.3,
                "max_tokens": 1024,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"]

        # Parse JSON from response (handle markdown code blocks)
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            content = content.rsplit("```", 1)[0]
            content = content.strip()

        result = json.loads(content)

        # Validate suggestions against actual catalog
        validated = []
        for s in result.get("suggestions", []):
            n3 = s.get("n3", "")
            n4 = s.get("n4", "")
            qty = s.get("qty")
            if n3 in data_store.get_products_n3():
                n4_list = data_store.get_products_n4(n3)
                if n4 in n4_list:
                    validated.append({"n3": n3, "n4": n4, "qty": qty})
                elif n4_list:
                    # N4 not found exactly — try fuzzy match
                    n4_lower = n4.lower()
                    for real_n4 in n4_list:
                        if n4_lower in real_n4.lower() or real_n4.lower() in n4_lower:
                            validated.append({"n3": n3, "n4": real_n4, "qty": qty})
                            break
                    else:
                        validated.append({"n3": n3, "n4": None, "qty": qty})
            # If n3 not found, skip

        return {
            "message": result.get("message", ""),
            "suggestions": validated,
        }

    except requests.exceptions.Timeout:
        return {"message": "Модель не ответила вовремя. Попробуйте ещё раз.", "suggestions": []}
    except json.JSONDecodeError:
        # Model returned non-JSON, return as plain text
        return {"message": content if 'content' in dir() else "Ошибка разбора ответа.", "suggestions": []}
    except Exception as e:
        return {"message": f"Ошибка: {e}", "suggestions": []}
