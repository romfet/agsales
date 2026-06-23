# API: эндпоинты и форматы (фиксация)

Только вызовы и форматы вход/ответ. Контекст и семантика — в `onec_ingest_contract.md`.

База: `https://<host>`, тела — JSON UTF-8. Во всех вызовах кроме `/api/auth/token` и
`/health`: заголовок `Authorization: Bearer <access_token>`.

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
      "client_id":  "c0ffee00-0000-0000-0000-000000000012",
      "Клиент":     "Клиент 12",
      "Отрасль":    "04. Металлотрейдеры",
      "ЗаказНомер": "АГAG-212482",
      "ЗаказДата":  "2024-08-11",
      "N1":         "02. ТРУБЫ ПРОФИЛЬНЫЕ",
      "N2":         "02.01. Трубы профильные до 120",
      "N3":         "02.01.01. Трубы профильные до 120 Ст1-3, Ст10-20, C235-С285",
      "N4":         "Труба профильная 40х20х2 Углерод.",
      "Количество": 1.032
    }
  ]
}
```

| Поле | Тип | Обяз. |
|---|---|---|
| `client_id` | string (GUID) | да |
| `Отрасль` | string | да |
| `ЗаказНомер` | string | да |
| `N3` | string | да |
| `N4` | string | да |
| `Количество` | number | да |
| `Клиент` | string | нет |
| `ЗаказДата` | string `YYYY-MM-DD` | нет |
| `N1`, `N2` | string | нет |

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

**Вход:**
```json
{
  "client_id": "c0ffee00-0000-0000-0000-000000000012",
  "lines": [
    { "N4": "Труба профильная 40х20х2 Углерод.", "qty": 1.5 },
    { "N4": "Арматура ф 14 А500С", "qty": 2.0 }
  ]
}
```

| Поле | Тип | Обяз. |
|---|---|---|
| `client_id` | string | да |
| `lines[].N4` | string | да* |
| `lines[].N3` | string | да* |
| `lines[].qty` | number | нет |

\* В строке достаточно `N4` **или** `N3`.

**Ответ 200:**
```json
{
  "client_id": "c0ffee00-0000-0000-0000-000000000012",
  "niche": "04. Металлотрейдеры",
  "analysis1_forgotten": [
    {
      "type": "missing",
      "n3": "05.02.01. Лист горячекатаный",
      "n4": "Лист г/к 8 мм",
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
      "n3": "03.01.02. Арматура А400С, А500С, А500СП",
      "n4": "Арматура ф 16 А500С",
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
| `n3` | string | подгруппа |
| `n4` | string\|null | товар |
| `order_count` / `total_orders` | int | заказов с товаром / всего заказов клиента |
| `frequency_pct` | number | частота, % |
| `avg_qty` | number | средний объём в заказе |
| `current_qty` | number\|null | объём в текущей заявке (для `low_quantity`) |
| `priority` | string | `high` \| `medium` |

**Поля ответа — `analysis2_niche[]`:**

| Поле | Тип | Смысл |
|---|---|---|
| `n3` | string | подгруппа |
| `n4` | string\|null | товар |
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

> Внутренний `POST /api/ingest/refresh` (scope `ingest`, ответ `{ "status": "ok" }`)
> — принудительный пересчёт агрегатов для ops/dev; в обмене 1С не участвует.
