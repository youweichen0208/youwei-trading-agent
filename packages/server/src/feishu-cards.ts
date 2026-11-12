import type { PoolClient } from "pg";
export type Card = {
  schema: string;
  header: object;
  body: { elements: any[] };
};
const display = (v: unknown): string =>
  v === null || v === undefined ? "缺失" : String(v);
const numeric = (v: unknown) =>
  ((typeof v === "string" && v.trim() !== "") || typeof v === "number") &&
  Number.isFinite(Number(v))
    ? Number(v)
    : null;
const md = (content: string) => ({ tag: "markdown", content });
const table = (columns: [string, string][], rows: any[]) => ({
  tag: "table",
  page_size: 10,
  columns: columns.map(([name, display_name]) => ({
    name,
    display_name,
    data_type: "text",
  })),
  rows: rows.map((r) =>
    Object.fromEntries(columns.map(([k]) => [k, display(r[k])])),
  ),
});
const percent = (v: unknown) =>
  numeric(v) === null ? "缺失" : `${(numeric(v)! * 100).toFixed(3)}%`;
function card(title: string, elements: any[], origin: string): Card {
  return {
    schema: "2.0",
    header: {
      template: "blue",
      title: { tag: "plain_text", content: title.slice(0, 120) },
    },
    body: {
      elements: [
        ...elements,
        md(
          `[打开研究工作台](${new URL(origin).origin}) · 图表使用已保存的数据；用于研究与模拟。`,
        ),
      ],
    },
  };
}
export function artifactCards(a: any, data: any, origin: string): Card[] {
  const p = data.payload,
    meta =
      data.metadata ??
      (a.component === "indicator_panel"
        ? data.lineage?.inputs?.[0]?.metadata
        : undefined) ??
      {},
    identity = a.identity ?? {};
  const source = meta.source ?? identity.source ?? "已保存计算结果";
  const info = [
    meta.symbol,
    source,
    meta.feed,
    meta.currency,
    meta.exchangeTimezone,
    meta.adjustment ? `口径：${meta.adjustment}` : null,
    `截至：${meta.asOf ?? data.created_at ?? "见工作台"}`,
  ]
    .filter(Boolean)
    .join(" · ");
  if (a.component === "stock_chart" || a.component === "indicator_panel") {
    let rows = Array.isArray(p) ? p : p?.rows;
    if (!Array.isArray(rows) || !rows.length)
      return [
        card(a.title, [md("该结果没有可绘制的数据。"), md(info)], origin),
      ];
    rows = rows
      .filter(
        (r: any) =>
          typeof r.session === "string" &&
          (!meta.displayRange ||
            (r.session >= meta.displayRange.start &&
              r.session <= meta.displayRange.end)),
      )
      .sort((x: any, y: any) => x.session.localeCompare(y.session));
    const stock = a.component === "stock_chart";
    const fields: [string, string][] = stock
      ? [
          ["close", "收盘价"],
          ...(a.props?.showVolume === false
            ? []
            : [["volume", "成交量（股）"] as [string, string]]),
        ]
      : [
          ["ma20", "MA20"],
          ["rsi14", "RSI14"],
        ];
    const columns: [string, string][] = stock
      ? [
          ["session", "交易日"],
          ["open", "开盘"],
          ["high", "最高"],
          ["low", "最低"],
          ["close", "收盘"],
          ["volume", "成交量"],
        ]
      : [
          ["session", "交易日"],
          ["ma20", "MA20"],
          ["rsi14", "RSI14"],
        ];
    const cards: Card[] = [];
    for (let offset = 0; offset < rows.length; offset += 100) {
      const window = rows.slice(offset, offset + 100),
        elements: any[] = [
          md(info),
          md(
            `${window[0].session} — ${window.at(-1).session} · ${window.length} 个数据日`,
          ),
        ];
      for (const [field, label] of fields) {
        let segment = 0;
        const values: any[] = [];
        for (const r of window) {
          const value = numeric(r[field]);
          if (value === null) {
            segment++;
            continue;
          }
          values.push({
            date:
              window[0].session.slice(0, 4) ===
              window.at(-1).session.slice(0, 4)
                ? r.session.slice(5).replaceAll("-", "/")
                : r.session.slice(2).replaceAll("-", "/"),
            session: r.session,
            value: field === "volume" ? value / 10000 : value,
            segment: String(segment),
          });
        }
        if (!values.length) {
          elements.push(md(`${label}：没有有效数据。`));
          continue;
        }
        elements.push({
          tag: "chart",
          height: "260px",
          preview: true,
          chart_spec: {
            type: field === "volume" ? "bar" : "line",
            title: { text: field === "volume" ? "成交量（万股）" : label },
            animation: false,
            label: { visible: false },
            point: { visible: false, style: { visible: false } },
            line: { style: { lineWidth: 2 } },
            tooltip: { visible: true },
            data: { values },
            xField: "date",
            yField: "value",
            seriesField: "segment",
            color: ["#3266d4"],
            legends: { visible: false },
            axes: [
              {
                orient: "bottom",
                type: "band",
                sampling: true,
                tickCount: 4,
                tick: { visible: false },
                grid: { visible: false },
                label: {
                  autoRotate: false,
                  autoHide: true,
                  autoLimit: false,
                  minGap: 24,
                  style: { angle: 0, fontSize: 11 },
                },
              },
              {
                orient: "left",
                type: "linear",
                tickCount: 4,
                zero: field === "volume" || field === "rsi14",
                label: {
                  style: { fontSize: 11 },
                },
                tick: { visible: false },
                ...(field === "rsi14" ? { min: 0, max: 100 } : {}),
              },
            ],
          },
        });
      }
      elements.push(
        md(
          "点击图表查看数值。下表列出本段最近 10 条，成交量单位为股；缺失值不按零计算。",
        ),
        table(columns, window.slice(-10)),
      );
      cards.push(
        card(
          `${a.title}${rows.length > 100 ? ` · 第 ${offset / 100 + 1} 段` : ""}`,
          elements,
          origin,
        ),
      );
    }
    return cards.length
      ? cards
      : [card(a.title, [md("所选区间没有数据。")], origin)];
  }
  if (a.component === "forecast_result")
    return [
      card(
        a.title,
        [
          md(`**${display(p.symbol)} · ${display(p.horizon)} 个交易日预测**`),
          md(
            `预期收益：${percent(p.meanReturn)}\n上涨概率：${percent(p.upProbability)}\n80% 收益区间：${percent(p.lowerReturn)} ～ ${percent(p.upperReturn)}\n起算：${display(p.startSession)} · 到期：${display(p.targetSession)}`,
          ),
          md(display(p.thesis)),
          md("模型判断已保存；后续改判保留原记录，到期自动验证。"),
        ],
        origin,
      ),
    ];
  if (a.component === "forecast_evaluation")
    return [
      card(
        a.title,
        [
          md(
            `**事前预测 · 到期验证**\n实际收益：${percent(p.actualReturn)}\n相对 SPY：${percent(p.excessReturn)}\n概率误差：${display(p.metrics?.brier)}`,
          ),
        ],
        origin,
      ),
    ];
  if (a.component === "review")
    return [
      card(
        a.title,
        [
          md("**到期复盘**"),
          md(display(p.text)),
          md(p.attribution === "supported" ? "归因有证据支持" : "归因仍待验证"),
        ],
        origin,
      ),
    ];
  if (a.component === "prediction_result")
    return [
      card(
        a.title,
        [
          md("**探索性统计基线 · 回溯评估**"),
          md(
            p.status === "unavailable"
              ? "连续有效样本不足，暂不生成预测。"
              : `下一交易日收益预测：${percent(p.prediction)}\n目标日：${display(p.target)}\n输入截至：${display(p.origin)}`,
          ),
          table(
            [
              ["metric", "指标"],
              ["model", "20 日均收益"],
              ["baseline", "零收益基线"],
            ],
            ["mae", "rmse"].map((k) => ({
              metric: k.toUpperCase(),
              model: percent(p.metrics?.[k]),
              baseline: percent(p.zeroBaseline?.[k]),
            })),
          ),
          md("历史效果不代表未来收益。"),
          md(info),
        ],
        origin,
      ),
    ];
  if (a.component === "portfolio_summary") {
    const positions = Array.isArray(p.positions) ? p.positions : [];
    const cards: Card[] = [];
    for (let i = 0; i < Math.max(1, positions.length); i += 30)
      cards.push(
        card(
          a.title,
          [
            md(
              `**模拟持仓** · ${display(p.asOf)}\n总市值 / USD：${display(p.total)}\n集中度：${display(p.concentration)}`,
            ),
            ...(positions.length
              ? [
                  table(
                    [
                      ["symbol", "股票"],
                      ["value", "市值"],
                      ["weight", "权重"],
                    ],
                    positions
                      .slice(i, i + 30)
                      .map((r: any) => ({ ...r, weight: percent(r.weight) })),
                  ),
                ]
              : []),
            ...(p.missing?.length
              ? [md("部分持仓缺少有效价格，总估值不完整。")]
              : []),
            md(info),
          ],
          origin,
        ),
      );
    return cards;
  }
  return [];
}
export async function loadTaskCards(
  c: Pick<PoolClient, "query">,
  owner: string,
  taskId: string,
  origin: string,
): Promise<Card[]> {
  const artifacts = await c.query(
    "SELECT a.body FROM task_artifacts a JOIN tasks t ON t.id=a.root_id AND t.owner_id=a.owner_id WHERE a.owner_id=$1 AND a.root_id=$2 AND NOT a.removed ORDER BY array_position(t.artifact_order,a.id) NULLS LAST,a.id",
    [owner, taskId],
  );
  const cards: Card[] = [];
  for (const { body: a } of artifacts.rows) {
    if (
      ![
        "stock_chart",
        "indicator_panel",
        "prediction_result",
        "portfolio_summary",
        "forecast_result",
        "forecast_evaluation",
        "review",
      ].includes(a.component)
    )
      continue;
    const dataset = a.component === "stock_chart";
    const ref = dataset ? a.datasetRef : a.resultRef;
    if (!ref) continue;
    const result = await c.query(
      `SELECT payload,${dataset ? "metadata" : "lineage"},created_at FROM ${dataset ? "datasets" : "results"} WHERE owner_id=$1 AND id=$2`,
      [owner, ref],
    );
    if (!result.rows[0]) continue;
    for (const built of artifactCards(a, result.rows[0], origin)) {
      cards.push(
        Buffer.byteLength(JSON.stringify(built)) <= 28000
          ? built
          : card(
              a.title,
              [md("此结果超出消息大小限制，请在工作台查看完整数据。")],
              origin,
            ),
      );
    }
  }
  return cards;
}
