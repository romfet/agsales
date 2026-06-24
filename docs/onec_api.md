# API: эндпоинты и форматы (как на стенде)

Актуально для развёрнутого сервиса. База: `https://sgsales.rumsk01.keenetic.link`.
Тела — JSON UTF-8. Идентичность по **GUID**: `client_id`, `n3_id`, `n4_id`
(наименований товара/подгруппы нет — 1С резолвит их у себя). `niche` = Отрасль
(строка), заказ = `order_num`. Во всех `/api/ingest/*` и `/api/analyze` —
заголовок `Authorization: Bearer <access_token>`.

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
{ "access_token": "<JWT>", "token_type": "bearer", "expires_in": 3600, "scope": "ingest analyze" }
```

## 2. POST /api/ingest/order-lines
История — дельта (новые/изменённые заказы). Грейн: строка = `(заказ × товар)`.
Заказ обновляется целиком: все строки одного `order_num` присылаются вместе и
заменяют прежние. Удаление — `deleted: true` (в 1С удалений нет → можно не слать).
```json
{
  "items": [
    {
      "order_num":  "АГAG-212482",
      "order_date": "2024-08-11",
      "client_id":  "c0ffee00-0000-0000-0000-000000000012",
      "niche":      "04. Металлотрейдеры",
      "n3_id":      "a0a0a0a0-0000-0000-0000-0000000000n3",
      "n4_id":      "b0b0b0b0-0000-0000-0000-0000000000n4",
      "qty":        1.032
    }
  ]
}
```

| Поле | Тип | Обяз. |
|---|---|---|
| `order_num` | string | да (ключ заказа) |
| `client_id` | string (GUID) | да |
| `n3_id` | string (GUID) | да (подгруппа) |
| `n4_id` | string (GUID) | да (товар) |
| `niche` | string | да* (Отрасль; без неё нет анализа по нише) |
| `qty` | number | да |
| `order_date` | string `YYYY-MM-DD` | нет |
| `client_name` | string | нет (отображение) |
| `deleted` | bool | нет (`true` → удалить заказ) |

**Ответ 200:** `{ "accepted": 1, "orders_affected": 1 }`

## 3. POST /api/ingest/commit
После загрузки пачки — пересобрать аналитику.
**Ответ 200:** `{ "status": "ok", "rows": 26565 }`

## 4. POST /api/analyze
Текущая заявка → рекомендации. Stateless. Анализ на уровне подгруппы (N3).
```json
{
  "client_id": "c0ffee00-0000-0000-0000-000000000012",
  "lines": [
    { "n4_id": "b0b0b0b0-0000-0000-0000-0000000000n4", "qty": 1.5 }
  ]
}
```
- В строке достаточно `n4_id` **или** `n3_id` + `qty`.

**Ответ 200:**
```json
{
  "client_id": "c0ffee00-...-012",
  "niche": "04. Металлотрейдеры",
  "analysis1_forgotten": [
    { "type": "missing", "n3_id": "...", "n4_id": "...",
      "order_count": 8, "total_orders": 20, "frequency_pct": 40.0,
      "avg_qty": 12.5, "current_qty": null, "priority": "high" }
  ],
  "analysis2_niche": [
    { "n3_id": "...", "n4_id": "...", "niche": "04. Металлотрейдеры",
      "niche_pct": 35.0, "client_count": 18, "total_clients": 51,
      "order_count": 120, "total_orders": 940, "frequency_pct": 12.8,
      "client_bought_before": false, "avg_qty_per_client": 9.3, "priority": "high" }
  ]
}
```

**Поля рекомендаций** (товар/подгруппа — только GUID; имена 1С подставляет сама):

`analysis1_forgotten[]`: `type` (`missing`|`low_quantity`), `n3_id`, `n4_id`,
`order_count`/`total_orders`, `frequency_pct`, `avg_qty`, `current_qty`, `priority` (`high`|`medium`).

`analysis2_niche[]`: `n3_id`, `n4_id`, `niche`, `niche_pct`, `client_count`/`total_clients`,
`order_count`/`total_orders`, `frequency_pct`, `client_bought_before`, `avg_qty_per_client`, `priority`.

## 5. GET /health → `{ "status": "ok" }`

---

## Ошибки
```json
{ "detail": "missing bearer token" }    // 401 нет/просрочен токен
{ "detail": "missing scope: analyze" }  // 403 токен без нужного права
{ "detail": "Заявка пуста" }            // 400 пустые lines в /api/analyze
```
