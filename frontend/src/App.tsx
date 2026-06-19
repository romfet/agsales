import { useEffect, useState } from "react";

import { api } from "./api/client";
import type { Client, ClientInfo, DraftLine, AnalyzeOut, OrderSearch } from "./api/types";
import { AnalysisTables } from "./components/AnalysisTables";
import { OrderBuilder } from "./components/OrderBuilder";

export function App() {
  const [clients, setClients] = useState<Client[]>([]);
  const [n3List, setN3List] = useState<string[]>([]);
  const [clientGuid, setClientGuid] = useState("");
  const [info, setInfo] = useState<ClientInfo | null>(null);
  const [lines, setLines] = useState<DraftLine[]>([]);
  const [result, setResult] = useState<AnalyzeOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // order search
  const [orderQuery, setOrderQuery] = useState("");
  const [orderHits, setOrderHits] = useState<OrderSearch[]>([]);

  useEffect(() => {
    api.GET("/api/clients").then(({ data }) => setClients(data ?? []));
    api.GET("/api/products/n3").then(({ data }) => setN3List(data ?? []));
  }, []);

  useEffect(() => {
    setInfo(null);
    setResult(null);
    if (!clientGuid) return;
    api
      .GET("/api/clients/{client_guid}/info", {
        params: { path: { client_guid: clientGuid } },
      })
      .then(({ data }) => setInfo(data ?? null));
  }, [clientGuid]);

  const addLine = (line: DraftLine) => setLines((prev) => [...prev, line]);
  const removeLine = (i: number) => setLines((prev) => prev.filter((_, idx) => idx !== i));

  const searchOrders = async () => {
    if (!orderQuery.trim()) return;
    const { data } = await api.GET("/api/orders/search", {
      params: { query: { query: orderQuery, client_guid: clientGuid || undefined } },
    });
    setOrderHits(data ?? []);
  };

  const loadOrder = async (orderNum: string) => {
    const { data } = await api.GET("/api/orders/{order_num}", {
      params: { path: { order_num: orderNum } },
    });
    if (!data) return;
    setLines(
      data.lines.map((l) => ({
        n3: l.n3,
        n4: l.n4 ?? "",
        item_guid: l.item_guid ?? "",
        qty: l.qty ?? 0,
      })),
    );
    setOrderHits([]);
    setOrderQuery("");
  };

  const analyze = async () => {
    setError(null);
    if (!clientGuid) {
      setError("Выберите клиента.");
      return;
    }
    if (lines.length === 0) {
      setError("Заявка пуста.");
      return;
    }
    setBusy(true);
    const { data, error: apiError } = await api.POST("/api/analyze", {
      body: {
        client_guid: clientGuid,
        order_lines: lines.map((l) => ({
          n3: l.n3,
          n4: l.n4 || null,
          item_guid: l.item_guid || null,
          qty: l.qty,
        })),
      },
    });
    setBusy(false);
    if (apiError) {
      setError("Ошибка анализа.");
      return;
    }
    setResult(data ?? null);
  };

  return (
    <div className="page">
      <header>
        <h1>AG Sales — анализ заявок</h1>
      </header>

      <section className="card">
        <h2>Клиент</h2>
        <div className="builder-row">
          <select value={clientGuid} onChange={(e) => setClientGuid(e.target.value)}>
            <option value="">— выберите клиента —</option>
            {clients.map((c) => (
              <option key={c.client_guid} value={c.client_guid}>
                {c.client_name}
              </option>
            ))}
          </select>
          {info ? (
            <span className="info">
              Ниша: <b>{info.niche ?? "—"}</b> · заказов: <b>{info.total_orders}</b>
            </span>
          ) : null}
        </div>

        <div className="builder-row">
          <input
            placeholder="Найти заказ по номеру…"
            value={orderQuery}
            onChange={(e) => setOrderQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && searchOrders()}
          />
          <button className="ghost" onClick={searchOrders}>
            Найти
          </button>
        </div>
        {orderHits.length > 0 && (
          <ul className="hits">
            {orderHits.map((h) => (
              <li key={h.order_num}>
                <button className="link" onClick={() => loadOrder(h.order_num)}>
                  {h.order_num}
                </button>{" "}
                <span className="muted small">
                  {h.date ?? "—"} · {h.line_count} поз. · {h.client}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <OrderBuilder n3List={n3List} lines={lines} onAdd={addLine} onRemove={removeLine} />

      <div className="actions">
        <button className="primary" onClick={analyze} disabled={busy}>
          {busy ? "Анализ…" : "Анализировать"}
        </button>
        {error ? <span className="error">{error}</span> : null}
      </div>

      {result ? <AnalysisTables data={result} /> : null}
    </div>
  );
}
