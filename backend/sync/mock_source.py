"""Mock 1C source — generates synthetic order-line rows (GUID-keyed).

Stand-in for the real feed so the data layer can be exercised before the real
1C JSON lands. Row shape matches the GUID contract: client_id / n3_id / n4_id are
GUIDs, names are display-only, order key is order_num.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

NICHES = ["Строительство", "Машиностроение", "Энергетика", "Торговля"]

# n3_name -> list of n4_name
CATALOG = {
    "01.02.03. Арматура": ["Арматура А500С 12мм", "Арматура А500С 16мм", "Арматура А240 10мм"],
    "01.02.05. Балка": ["Балка 20Б1", "Балка 30Ш1"],
    "01.03.01. Лист горячекатаный": ["Лист г/к 4мм", "Лист г/к 8мм", "Лист г/к 10мм"],
    "01.04.02. Труба профильная": ["Труба 40х20х2", "Труба 60х40х3", "Труба 80х80х4"],
    "01.05.01. Уголок": ["Уголок 50х50х5", "Уголок 63х63х6"],
}


def _guid(prefix: str, n: int) -> str:
    return f"{prefix}{n:032d}"[:8] + "-0000-0000-0000-" + f"{n:012d}"


def generate_rows(
    n_clients: int = 40,
    orders_per_client: tuple[int, int] = (3, 25),
    seed: int = 42,
) -> list[dict]:
    rng = random.Random(seed)
    n3_keys = list(CATALOG.keys())

    # stable GUIDs for subgroups (n3) and products (n4)
    n3_id = {name: _guid("n300", i) for i, name in enumerate(n3_keys)}
    n4_id: dict[str, str] = {}
    n4_n3: dict[str, str] = {}
    j = 0
    for n3_name, n4s in CATALOG.items():
        for n4_name in n4s:
            n4_id[n4_name] = _guid("n400", j)
            n4_n3[n4_name] = n3_name
            j += 1

    rows: list[dict] = []
    base = date(2025, 1, 1)
    order_seq = 0
    for c in range(n_clients):
        client_id = _guid("clnt", c)
        client_name = f"ООО Клиент {c + 1}"
        niche = NICHES[c % len(NICHES)]
        favorites = rng.sample(n3_keys, k=rng.randint(2, 4))
        for _ in range(rng.randint(*orders_per_client)):
            order_seq += 1
            order_num = f"ЗП-{order_seq:07d}"
            order_date = base + timedelta(days=rng.randint(0, 500))
            chosen = rng.sample(favorites, k=min(len(favorites), rng.randint(1, len(favorites))))
            for n3_name in chosen:
                n4_name = rng.choice(CATALOG[n3_name])
                rows.append({
                    "order_num": order_num,
                    "order_date": order_date,
                    "client_id": client_id,
                    "client_name": client_name,
                    "niche": niche,
                    "n3_id": n3_id[n3_name],
                    "n3_name": n3_name,
                    "n4_id": n4_id[n4_name],
                    "n4_name": n4_name,
                    "qty": round(rng.uniform(0.5, 20.0), 3),
                })
    return rows
