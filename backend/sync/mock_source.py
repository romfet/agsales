"""Mock 1C source — generates synthetic order-line rows.

Stand-in for the real ``onec_client`` (added in phase 2) so the data layer can be
built and tested before the 1C HTTP-service contract is finalized. The row shape
mirrors docs/onec_contract.md exactly.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

NICHES = ["Строительство", "Машиностроение", "Энергетика", "Торговля"]

# n3 -> list of n4 product names
CATALOG = {
    "01.02.03. Арматура": ["Арматура А500С 12мм", "Арматура А500С 16мм", "Арматура А240 10мм"],
    "01.02.05. Балка": ["Балка 20Б1", "Балка 30Ш1"],
    "01.03.01. Лист горячекатаный": ["Лист г/к 4мм", "Лист г/к 8мм", "Лист г/к 10мм"],
    "01.04.02. Труба профильная": ["Труба 40х20х2", "Труба 60х40х3", "Труба 80х80х4"],
    "01.05.01. Уголок": ["Уголок 50х50х5", "Уголок 63х63х6"],
}
N1 = "01. Металлопрокат"
N2_BY_N3 = {
    "01.02.03. Арматура": "01.02. Сортовой прокат",
    "01.02.05. Балка": "01.02. Сортовой прокат",
    "01.03.01. Лист горячекатаный": "01.03. Листовой прокат",
    "01.04.02. Труба профильная": "01.04. Трубный прокат",
    "01.05.01. Уголок": "01.02. Сортовой прокат",
}


def _guid(prefix: str, n: int) -> str:
    return f"{prefix}{n:032d}"[:8] + "-0000-0000-0000-" + f"{n:012d}"


def generate_rows(
    n_clients: int = 40,
    orders_per_client: tuple[int, int] = (3, 25),
    lines_per_order: tuple[int, int] = (2, 8),
    seed: int = 42,
) -> list[dict]:
    rng = random.Random(seed)
    n3_keys = list(CATALOG.keys())
    item_guids: dict[str, str] = {}
    for i, (n3, n4s) in enumerate(CATALOG.items()):
        for j, n4 in enumerate(n4s):
            item_guids[n4] = _guid("item", i * 100 + j)

    rows: list[dict] = []
    base = date(2025, 1, 1)
    order_seq = 0
    for c in range(n_clients):
        client_guid = _guid("clnt", c)
        client_name = f"ООО Клиент {c + 1}"
        niche = NICHES[c % len(NICHES)]
        # Each client has a stable "favorite" subset of categories.
        favorites = rng.sample(n3_keys, k=rng.randint(2, 4))
        n_orders = rng.randint(*orders_per_client)
        for _ in range(n_orders):
            order_seq += 1
            order_guid = _guid("ordr", order_seq)
            order_num = f"ЗП-{order_seq:07d}"
            order_date = base + timedelta(days=rng.randint(0, 500))
            chosen_n3 = rng.sample(favorites, k=min(len(favorites), rng.randint(1, len(favorites))))
            for n3 in chosen_n3:
                n4 = rng.choice(CATALOG[n3])
                rows.append({
                    "order_guid": order_guid,
                    "order_num": order_num,
                    "order_date": order_date,
                    "client_guid": client_guid,
                    "client_name": client_name,
                    "niche": niche,
                    "n1": N1,
                    "n2": N2_BY_N3[n3],
                    "n3": n3,
                    "n4": n4,
                    "item_guid": item_guids[n4],
                    "qty": round(rng.uniform(0.5, 20.0), 3),
                })
    return rows


def generate_stock(rows: list[dict], seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    by_item: dict[str, str] = {}
    for r in rows:
        by_item[r["item_guid"]] = r["n4"]
    return [
        {
            "item_guid": guid,
            "n4_name": name,
            "on_stock": round(rng.uniform(0, 200), 3),
            "in_transit": round(rng.uniform(0, 80), 3) if rng.random() > 0.4 else None,
        }
        for guid, name in by_item.items()
    ]
