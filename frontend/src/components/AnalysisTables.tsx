import type { AnalyzeOut } from "../api/types";

function fmt(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : String(v);
}

export function AnalysisTables({ data }: { data: AnalyzeOut }) {
  const { analysis1_forgotten: forgotten, analysis2_niche: niche } = data;

  return (
    <div className="analysis">
      <section className="card">
        <h2>
          Забытое <span className="muted">— что клиент обычно берёт</span>
        </h2>
        {forgotten.length === 0 ? (
          <p className="muted">Нет рекомендаций.</p>
        ) : (
          <table className="result">
            <thead>
              <tr>
                <th>Товар</th>
                <th className="num">Частота</th>
                <th className="num">Ср. кол-во</th>
                <th className="num">В заявке</th>
                <th className="num">Остаток</th>
                <th className="num">В пути</th>
                <th>Приоритет</th>
              </tr>
            </thead>
            <tbody>
              {forgotten.map((r, i) => (
                <tr key={`${r.item_guid}-${i}`}>
                  <td>
                    <div className="prod">{r.n4 ?? r.n3}</div>
                    <div className="muted small">{r.n3}</div>
                  </td>
                  <td className="num">{fmt(r.frequency_pct)}%</td>
                  <td className="num">{fmt(r.avg_qty)}</td>
                  <td className="num">{fmt(r.current_qty)}</td>
                  <td className="num">{fmt(r.stock_on)}</td>
                  <td className="num">{fmt(r.stock_in_transit)}</td>
                  <td>
                    <span className={`badge ${r.priority}`}>{r.priority}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card">
        <h2>
          По нише <span className="muted">— что берут похожие клиенты</span>
          {data.niche ? <span className="niche-tag">{data.niche}</span> : null}
        </h2>
        {niche.length === 0 ? (
          <p className="muted">Нет рекомендаций.</p>
        ) : (
          <table className="result">
            <thead>
              <tr>
                <th>Товар</th>
                <th className="num">% ниши</th>
                <th className="num">Частота</th>
                <th>Брал ранее</th>
                <th className="num">Ср/клиент</th>
                <th className="num">Остаток</th>
                <th className="num">В пути</th>
                <th>Приоритет</th>
              </tr>
            </thead>
            <tbody>
              {niche.map((r, i) => (
                <tr key={`${r.item_guid}-${i}`}>
                  <td>
                    <div className="prod">{r.n4 ?? r.n3}</div>
                    <div className="muted small">{r.n3}</div>
                  </td>
                  <td className="num">{fmt(r.niche_pct)}%</td>
                  <td className="num">{fmt(r.frequency_pct)}%</td>
                  <td>{r.client_bought_before ? "да" : "—"}</td>
                  <td className="num">{fmt(r.avg_qty_per_client)}</td>
                  <td className="num">{fmt(r.stock_on)}</td>
                  <td className="num">{fmt(r.stock_in_transit)}</td>
                  <td>
                    <span className={`badge ${r.priority}`}>{r.priority}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
