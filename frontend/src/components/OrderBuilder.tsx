import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { DraftLine, ProductN4 } from "../api/types";

type Props = {
  n3List: string[];
  lines: DraftLine[];
  onAdd: (line: DraftLine) => void;
  onRemove: (index: number) => void;
};

export function OrderBuilder({ n3List, lines, onAdd, onRemove }: Props) {
  const [n3, setN3] = useState("");
  const [n4s, setN4s] = useState<ProductN4[]>([]);
  const [n4, setN4] = useState("");
  const [qty, setQty] = useState("");

  useEffect(() => {
    let active = true;
    if (!n3) {
      setN4s([]);
      setN4("");
      return;
    }
    api.GET("/api/products/n4", { params: { query: { n3 } } }).then(({ data }) => {
      if (!active) return;
      setN4s(data ?? []);
      setN4("");
    });
    return () => {
      active = false;
    };
  }, [n3]);

  const add = () => {
    const product = n4s.find((p) => p.n4 === n4);
    if (!n3 || !product) return;
    onAdd({ n3, n4: product.n4, item_guid: product.item_guid, qty: parseFloat(qty) || 0 });
    setQty("");
  };

  return (
    <section className="card">
      <h2>Заявка</h2>
      <div className="builder-row">
        <select value={n3} onChange={(e) => setN3(e.target.value)}>
          <option value="">— подгруппа (N3) —</option>
          {n3List.map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
        </select>
        <select value={n4} onChange={(e) => setN4(e.target.value)} disabled={!n3}>
          <option value="">— товар (N4) —</option>
          {n4s.map((p) => (
            <option key={p.item_guid} value={p.n4}>
              {p.n4}
            </option>
          ))}
        </select>
        <input
          type="number"
          step="0.001"
          min="0"
          placeholder="тонн"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
        />
        <button onClick={add} disabled={!n4}>
          Добавить
        </button>
      </div>

      {lines.length === 0 ? (
        <p className="muted">Позиции не добавлены.</p>
      ) : (
        <table className="lines">
          <thead>
            <tr>
              <th>N3</th>
              <th>N4</th>
              <th className="num">Кол-во, т</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={`${l.item_guid}-${i}`}>
                <td>{l.n3}</td>
                <td>{l.n4}</td>
                <td className="num">{l.qty}</td>
                <td>
                  <button className="link" onClick={() => onRemove(i)}>
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
