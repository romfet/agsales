# API: эндпоинты и форматы (фиксация)

Только вызовы и форматы вход/ответ. Контекст и семантика — в `onec_ingest_contract.md`.

База: `https://<host>`, тела — JSON UTF-8. Во всех вызовах кроме `/api/auth/token` и
`/health`: заголовок `Authorization: Bearer <access_token>`.

**Идентичность — по GUID:** клиент (`client_id`), подгруппа (`n3_id`), товар (`n4_id`).
Наименования (`client_name`, `n3_name`, `n4_name`) — только для отображения. Заказ — по
`ЗаказНомер`, ниша — по `Отрасль`. (Точные имена GUID-полей зафиксируем по образцу JSON 1С.)

| # | Метод | Путь | Право (scope) |
|---|---|---|---|
| 1 | POST | `/api/auth/token` | — |
| 2 | POST | `/api/ingest/order-lines` | `ingest` |
| 3 | POST | `/api/ingest/commit` | `ingest` |
| 4 | POST | `/api/analyze` | `analyze` |
| 5 | GET  | `/health` | — |

---

## 1. POST /api/auth/token

**Вход:**
```json
{ "client_id": "onec", "client_secret": "<секрет>" }
```
**Ответ 200:**
```json
{ "access_token": "<JWT>", "token_type": "bearer", "expires_in": 3600, "scope": "ingest" }
```

---

## 2. POST /api/ingest/order-lines

**Вход** (дельта; строка = позиция заказа):
```json
{
  "items": [
    {
      "client_id":   "c0ffee00-0000-0000-0000-000000000012",
      "client_name": "Клиент 12",
      "Отрасль":     "04. Металлотрейдеры",
      "ЗаказНомер":  "АГAG-212482",
      "ЗаказДата":   "2024-08-11",
      "n3_id":       "a0a0a0a0-0000-0000-0000-0000000000n3",
      "n3_name":     "02.01.01. Трубы профильные до 120 Ст1-3, Ст10-20, C235-С285",
      "n4_id":       "b0b0b0b0-0000-0000-0000-0000000000n4",
      "n4_name":     "Труба профильная 40х20х2 Углерод.",
      "Количество":  1.032
    }
  ]
}
```

| Поле | Тип | Обяз. | Назначение |
|---|---|---|---|
| `client_id` | string (GUID) | да | идентичность клиента |
| `n3_id` | string (GUID) | да | идентичность подгруппы (уровень сопоставления) |
| `n4_id` | string (GUID) | да | идентичность товара |
| `Отрасль` | string | да | ниша |
| `ЗаказНомер` | string | да | ключ заказа (счёт уникальных заказов) |
| `Количество` | number | да | объём |
| `client_name` | string | нет | отображение |
| `n3_name` | string | нет | отображение |
| `n4_name` | string | нет | отображение |
| `ЗаказДата` | string `YYYY-MM-DD` | нет | давность |

> `N1`/`N2` в анализе не используются — можно не передавать.

**Ответ 200:**
```json
{ "accepted": 1, "orders_affected": 1 }
```

---

## 3. POST /api/ingest/commit

**Вход:** пустое тело.
**Ответ 200:**
```json
{ "status": "ok", "rows": 26565 }
```

---

## 4. POST /api/analyze

**Вход** (товар — по `n4_id`; можно `n3_id` вместо него):
```json
{
  "client_id": "c0ffee00-0000-0000-0000-000000000012",
  "lines": [
    { "n4_id": "b0b0b0b0-0000-0000-0000-0000000000n4", "qty": 1.5 },
    { "n4_id": "b0b0b0b0-0000-0000-0000-0000000000a5", "qty": 2.0 }
  ]
}
```

| Поле | Тип | Обяз. |
|---|---|---|
| `client_id` | string (GUID) | да |
| `lines[].n4_id` | string (GUID) | да* |
| `lines[].n3_id` | string (GUID) | да* |
| `lines[].qty` | number | нет |

\* В строке достаточно `n4_id` **или** `n3_id`.

**Ответ 200:**
```json
{
  "client_id": "c0ffee00-0000-0000-0000-000000000012",
  "niche": "04. Металлотрейдеры",
  "analysis1_forgotten": [
    {
      "type": "missing",
      "n3_id": "a0a0a0a0-…-0n3",
      "n3_name": "05.02.01. Лист горячекатаный",
      "n4_id": "b0b0b0b0-…-0n4",
      "n4_name": "Лист г/к 8 мм",
      "order_count": 8,
      "total_orders": 20,
      "frequency_pct": 40.0,
      "avg_qty": 12.5,
      "current_qty": null,
      "priority": "high"
    }
  ],
  "analysis2_niche": [
    {
      "n3_id": "a0a0a0a0-…-1n3",
      "n3_name": "03.01.02. Арматура А400С, А500С, А500СП",
      "n4_id": "b0b0b0b0-…-1n4",
      "n4_name": "Арматура ф 16 А500С",
      "niche": "04. Металлотрейдеры",
      "niche_pct": 35.0,
      "client_count": 18,
      "total_clients": 51,
      "order_count": 120,
      "total_orders": 940,
      "frequency_pct": 12.8,
      "client_bought_before": false,
      "avg_qty_per_client": 9.3,
      "priority": "high"
    }
  ]
}
```

**Поля ответа — `analysis1_forgotten[]`:**

| Поле | Тип | Смысл |
|---|---|---|
| `type` | string | `missing` \| `low_quantity` |
| `n3_id` / `n3_name` | string | подгруппа (GUID / имя) |
| `n4_id` / `n4_name` | string\|null | товар (GUID / имя) |
| `order_count` / `total_orders` | int | заказов с товаром / всего заказов клиента |
| `frequency_pct` | number | частота, % |
| `avg_qty` | number | средний объём в заказе |
| `current_qty` | number\|null | объём в текущей заявке (для `low_quantity`) |
| `priority` | string | `high` \| `medium` |

**Поля ответа — `analysis2_niche[]`:**

| Поле | Тип | Смысл |
|---|---|---|
| `n3_id` / `n3_name` | string | подгруппа (GUID / имя) |
| `n4_id` / `n4_name` | string\|null | товар (GUID / имя) |
| `niche` | string | ниша клиента |
| `niche_pct` | number | % клиентов ниши, берущих товар |
| `client_count` / `total_clients` | int | клиентов ниши с товаром / всего в нише |
| `order_count` / `total_orders` | int | заказов с товаром / всего заказов ниши |
| `frequency_pct` | number | частота в заказах ниши, % |
| `client_bought_before` | bool | брал ли клиент товар ранее |
| `avg_qty_per_client` | number | средний объём на клиента ниши |
| `priority` | string | `high` \| `medium` |

---

## 5. GET /health

**Ответ 200:** `{ "status": "ok" }`

---

## Ошибки

```json
{ "detail": "missing bearer token" }    // 401
{ "detail": "missing scope: analyze" }  // 403
{ "detail": "Заявка пуста" }            // 400
```

---

> **Статус:** формат выше — целевой (идентичность по GUID для client/N3/N4). Текущий
> код на моках использует внутреннюю схему (`client_guid`/`item_guid`, N3/N4 по имени);
> приведём к этому формату при подключении реального ingest по образцу JSON 1С.
> `POST /api/ingest/refresh` (scope `ingest`) — внутренний пересчёт агрегатов (ops/dev).
