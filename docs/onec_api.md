# API: эндпоинты и форматы (как на стенде)

Актуально для развёрнутого сервиса. База: `https://sgsales.rumsk01.keenetic.link`.
Тела — JSON UTF-8. **Идентичность по GUID:** `order_id`, `shipment_id`, `client_id`,
`niche` (Отрасль — тоже GUID), `n3_id`, `n4_id` (наименований товара/подгруппы нет —
1С резолвит их у себя). `order_num`/`shipment_num` — человекочитаемые номера
(отображение). Во всех `/api/ingest/*`, `/api/analyze` и `/api/effect/*` — заголовок
`Authorization: Bearer <access_token>` (право: `ingest` / `analyze` / `effect`).

| # | Метод | Путь | Право (scope) |
|---|---|---|---|
| 1 | POST | `/api/auth/token` | — |
| 2 | POST | `/api/ingest/order-lines` | `ingest` |
| 3 | POST | `/api/ingest/shipments` | `ingest` |
| 4 | POST | `/api/ingest/commit` | `ingest` |
| 5 | POST | `/api/analyze` | `analyze` |
| 6 | GET  | `/api/effect/funnel` | `effect` |
| 7 | GET  | `/api/effect/recommendations` | `effect` |
| 8 | GET  | `/health` | — |

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

## 2. POST /api/ingest/order-lines — заказы
История заказов — дельта (новые/изменённые). Грейн: строка = `(заказ × товар)`.
Ключ заказа — **`order_id` (GUID)**. Заказ обновляется целиком: все строки одного
`order_id` присылаются вместе и заменяют прежние. **Присылайте заказ при каждом
изменении** — сервис хранит **версии** (историю состояний) для аналитики диффа.
Удаление — `deleted: true` (в 1С удалений нет → можно не слать).
```json
{
  "items": [
    {
      "order_id":   "a1b2c3d4-e211-11ef-a380-3448edf7a2ad",
      "order_num":  "АГAG-212482",
      "order_date": "2024-08-11",
      "client_id":  "c0ffee00-4c57-11ec-a32e-3448edf7a2ad",
      "niche":      "f928c3fd-e211-11ef-a380-3448edf7a2ad",
      "n3_id":      "d24d39bd-1e9e-11ea-a969-0050569a475e",
      "n4_id":      "8917a4a0-1ea5-11ea-a969-0050569a475e",
      "qty":        1.032
    }
  ]
}
```

| Поле | Тип | Обяз. |
|---|---|---|
| `order_id` | string (GUID) | да (**ключ заказа**) |
| `order_num` | string | да (номер, отображение) |
| `client_id` | string (GUID) | да |
| `niche` | string (GUID) | да* (Отрасль; без неё нет анализа по нише) |
| `n3_id` | string (GUID) | да (подгруппа) |
| `n4_id` | string (GUID) | да (товар) |
| `qty` | number | да |
| `order_date` | string `YYYY-MM-DD` (допустим ISO с временем) | нет |
| `client_name` | string | нет (отображение) |
| `deleted` | bool | нет (`true` → удалить заказ по `order_id`) |

**Ответ 200:** `{ "accepted": 1, "orders_affected": 1 }`

## 3. POST /api/ingest/shipments — отгрузки
Факт отгруженного товара. Контракт как у заказа + **`shipment_id` (ключ)** и
**`order_id` (линк на заказ)**. Заказ ↔ отгрузка — **many-to-many** (заказ может
отгружаться несколькими отгрузками; отгрузка — покрывать несколько заказов; связь
на строке через `order_id`). Грейн: строка = `(отгрузка × товар)`. **Upsert по
`shipment_id`** (перезалив заменяет строки этой отгрузки), удалений нет.
`qty` = **фактически отгружено**.
```json
{
  "items": [
    {
      "shipment_id":   "5e7ff0cd-e211-11ef-a380-3448edf7a2ad",
      "shipment_num":  "РТ-000123",
      "shipment_date": "2024-08-15",
      "order_id":      "a1b2c3d4-e211-11ef-a380-3448edf7a2ad",
      "client_id":     "c0ffee00-4c57-11ec-a32e-3448edf7a2ad",
      "niche":         "f928c3fd-e211-11ef-a380-3448edf7a2ad",
      "n3_id":         "d24d39bd-1e9e-11ea-a969-0050569a475e",
      "n4_id":         "8917a4a0-1ea5-11ea-a969-0050569a475e",
      "qty":           0.850
    }
  ]
}
```

| Поле | Тип | Обяз. |
|---|---|---|
| `shipment_id` | string (GUID) | да (**ключ отгрузки**) |
| `order_id` | string (GUID) | да (линк на заказ; m2m) |
| `client_id` | string (GUID) | да |
| `niche` | string (GUID) | да* (Отрасль) |
| `n3_id` | string (GUID) | да (подгруппа) |
| `n4_id` | string (GUID) | да (товар) |
| `qty` | number | да (фактически отгружено) |
| `shipment_num` | string | нет (номер, отображение) |
| `shipment_date` | string `YYYY-MM-DD` (допустим ISO с временем) | нет |
| `client_name` | string | нет |
| `deleted` | bool | нет |

**Ответ 200:** `{ "accepted": 1, "shipments_affected": 1 }`

> **Важно:** рекомендации (`/api/analyze`) считаются по **истории отгрузок**, поэтому
> фид отгрузок обязателен для работы аналитики. Заказы используются для истории
> версий/диффа (эффект ассистента).

## 4. POST /api/ingest/commit
После загрузки пачек — пересобрать аналитику.
**Ответ 200:** `{ "status": "ok", "rows": 26565 }`

## 5. POST /api/analyze
Текущая заявка → рекомендации. Stateless. Анализ на уровне подгруппы (N3).
```json
{
  "client_id": "c0ffee00-4c57-11ec-a32e-3448edf7a2ad",
  "order_id":  "a1b2c3d4-e211-11ef-a380-3448edf7a2ad",
  "lines": [
    { "n4_id": "8917a4a0-1ea5-11ea-a969-0050569a475e", "qty": 1.5 }
  ]
}
```
- В строке достаточно `n4_id` **или** `n3_id` + `qty`.
- `order_id` (GUID разбираемого заказа) — необязателен, но **желателен**: по нему
  выданные рекомендации логируются и привязываются к заказу (аналитика эффекта).

**Ответ 200:**
```json
{
  "client_id": "c0ffee00-...-475e",
  "niche": "f928c3fd-e211-11ef-a380-3448edf7a2ad",
  "client_summary": { "shipped_qty": 200.0, "last_shipment_date": "2026-05-13", "orders_in_history": 4 },
  "analysis1_forgotten": [
    { "type": "missing", "n3_id": "...", "n4_id": "...",
      "order_count": 8, "total_orders": 20, "shipped_qty": 96.5, "frequency_pct": 40.0,
      "avg_qty": 12.5, "current_qty": null, "priority": "high" }
  ],
  "analysis2_niche": [
    { "n3_id": "...", "n4_id": "...", "niche": "f928c3fd-...",
      "niche_pct": 35.0, "client_count": 18, "total_clients": 51,
      "order_count": 120, "total_orders": 940, "shipped_qty": 1680.0, "frequency_pct": 12.8,
      "client_bought_before": false, "avg_qty_per_client": 9.3, "priority": "high" }
  ]
}
```

**`client_summary`** — сводка по клиенту для шапки формы: `shipped_qty` — **суммарная
отгрузка клиента за период (последние 12 мес, тонны)** («Отгрузка за период»),
`last_shipment_date` — дата последней отгрузки (`YYYY-MM-DD`), `orders_in_history` —
число заказов (отгрузок) клиента в истории.

**Поля рекомендаций** (товар/подгруппа — только GUID; имена 1С подставляет сама).
Примечание: счётчики `order_count`/`total_orders` считаются по **отгрузкам**
(имена полей сохранены для совместимости контракта).

`analysis1_forgotten[]`: `type` (`missing`|`low_quantity`), `n3_id`, `n4_id`,
`order_count`/`total_orders`, **`shipped_qty`**, `frequency_pct`, `avg_qty`, `current_qty`, `priority` (`high`|`medium`).

`analysis2_niche[]`: `n3_id`, `n4_id`, `niche`, `niche_pct`, `client_count`/`total_clients`,
`order_count`/`total_orders`, **`shipped_qty`**, `frequency_pct`, `client_bought_before`, `avg_qty_per_client`, `priority`.

**`shipped_qty`** — **отгружено тонн по этой подгруппе за последние 12 месяцев (из
РТУ)**: в `analysis1_forgotten` — по данному клиенту, в `analysis2_niche` — по всей
отрасли. Рядом с `total_orders`. Окно скользящее (от даты пересборки аналитики).

## 6. GET /api/effect/funnel — витрина эффекта для BI
Read-only выгрузка витрины воронки (`effect_funnel`, грейн `order_id × n3_id`).
Право `effect`. Пагинация `limit` (1..10000, деф. 1000) / `offset`. Только GUID +
меры — наименования BI резолвит из справочников 1С.

Query: `niche` (GUID отрасли), `period_from` / `period_to` (`YYYY-MM-DD`, месяц заказа), `limit`, `offset`.
```json
{
  "items": [
    { "order_id": "a1b2c3d4-...", "n3_id": "d24d39bd-...",
      "client_id": "c0ffee00-...", "niche": "f928c3fd-...", "period_month": "2026-08-01",
      "qty_original": 10.0, "qty_final": 12.0, "qty_added": 2.0, "qty_shipped": 8.0,
      "is_original": 1, "is_final": 1, "is_new_position": 0, "is_qty_increased": 1, "is_shipped": 1 }
  ],
  "limit": 1000, "offset": 0, "count": 1
}
```
Меры на `(order_id, n3)`: `qty_original`/`qty_final` — исходный/финальный тоннаж
подгруппы; `qty_added = max(final−original,0)` — прирост (вкл. рост); `qty_shipped`
— отгружено. Флаги (0/1): `is_new_position` (подгруппы не было → появилась),
`is_qty_increased` (была и выросла), `is_shipped`. `count` — всего строк по фильтру.

## 7. GET /api/effect/recommendations — журнал выданных рекомендаций для BI
Read-only выгрузка `recommendation_log` (append-only). Право `effect`. Query:
`since_id` (инкрементально — id больше указанного), `kind` (`forgotten`|`niche`),
`limit`, `offset`.
```json
{
  "items": [
    { "id": 42, "created_at": "2026-08-18T10:15:00+00:00", "client_id": "c0ffee00-...",
      "order_id": "a1b2c3d4-...", "kind": "forgotten", "n3_id": "d24d39bd-...",
      "n4_id": "8917a4a0-...", "priority": "high" }
  ],
  "limit": 1000, "offset": 0, "count": 1
}
```
BI связывает журнал с витриной по `(order_id, n3_id)` для атрибуции эффекта.

## 8. GET /health → `{ "status": "ok" }`

---

## Ошибки
```json
{ "detail": "missing bearer token" }    // 401 нет/просрочен токен
{ "detail": "missing scope: analyze" }  // 403 токен без нужного права
{ "detail": "Заявка пуста" }            // 400 пустые lines в /api/analyze
```
