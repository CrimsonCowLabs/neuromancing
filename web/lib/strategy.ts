// Human-readable rendering of deterministic strategy specs for the trader page.
//
// TOTAL by design: this runs inside a React Server Component, so it must NEVER
// throw on an unexpected spec shape (that would 500 the whole page). Every branch
// falls back to a safe string. Handles both `indicator_dsl` (named indicators +
// states + nestable all/any/not rules) and the legacy `rule_dsl` (inline
// indicator/period conditions). See trade-api/app/strategies/spec.py.

type AnySpec = any;

export interface StrategyView {
  indicators: { id: string; label: string; timeframe?: string }[];
  buy: string;
  exit: string;
  type?: string;
  baseTimeframe?: string;
}

const OP: Record<string, string> = {
  "<": "<", "<=": "≤", ">": ">", ">=": "≥", "==": "=", "!=": "≠",
};

function num(v: unknown, fallback: number): number | string {
  return v === undefined || v === null ? fallback : (v as any);
}

// Params suffix for an indicator, e.g. macd(12/26/9), bb(20/2), rsi(14).
function paramStr(ind: AnySpec): string {
  const fn = String(ind?.fn ?? "");
  if (fn === "macd") return `${num(ind.fast, 12)}/${num(ind.slow, 26)}/${num(ind.signal, 9)}`;
  if (fn === "bollinger" || fn === "bbpercent") return `${num(ind.period, 20)}/${num(ind.mult, 2)}`;
  if (ind?.period !== undefined && ind?.period !== null) return String(ind.period);
  return "";
}

const FN_LABEL: Record<string, string> = {
  sma: "SMA", ema: "EMA", rsi: "RSI", roc: "ROC", macd: "MACD",
  bollinger: "BB", bbpercent: "%B", atr: "ATR", vwap: "VWAP",
};

// Display term for a condition's left/right side.
function fnTerm(fn: string, params: string, field?: string): string {
  const base = FN_LABEL[fn] ?? (fn ? fn.toUpperCase() : "?");
  const withParams = params ? `${base}(${params})` : base;
  return field ? `${withParams}.${field}` : withParams;
}

// indicator_dsl: resolve an id → its display term (via the indicators map).
function idTerm(id: string, field: string | undefined, byId: Record<string, AnySpec>): string {
  const ind = byId[id];
  if (!ind) return field ? `${id}.${field}` : String(id);
  return fnTerm(String(ind.fn ?? ""), paramStr(ind), field);
}

// rule_dsl: a condition's `indicator` is a fn name + optional period.
function ruleTerm(indicator: string, period: unknown, field?: string): string {
  return fnTerm(String(indicator), period === undefined || period === null ? "" : String(period), field);
}

function condStr(cond: AnySpec, kind: string, byId: Record<string, AnySpec>): string {
  if (!cond || typeof cond !== "object") return "?";
  const isDsl = kind === "indicator_dsl";
  const left = isDsl
    ? idTerm(String(cond.indicator), cond.field, byId)
    : ruleTerm(String(cond.indicator), cond.period, cond.field);

  // right-hand side: another indicator or a constant value
  const otherId = isDsl ? cond.other : cond.other_indicator;
  const right =
    otherId !== undefined && otherId !== null
      ? isDsl
        ? idTerm(String(otherId), cond.other_field, byId)
        : ruleTerm(String(otherId), cond.other_period, cond.other_field)
      : String(cond.value ?? "?");

  if (cond.cross === "above" || cond.cross === "below") {
    return `${left} crosses ${cond.cross} ${right}`;
  }
  const op = OP[cond.op] ?? cond.op ?? "?";
  return `${left} ${op} ${right}`;
}

function walk(node: AnySpec, kind: string, byId: Record<string, AnySpec>, states: AnySpec): string {
  if (node == null) return "";
  if (typeof node === "string") {
    // indicator_dsl state reference
    const st = states && states[node];
    return st ? condStr(st, kind, byId) : String(node);
  }
  if (typeof node !== "object") return String(node);
  if (Array.isArray(node.all)) {
    const parts = node.all.map((n: AnySpec) => walk(n, kind, byId, states)).filter(Boolean);
    return parts.join(" and ");
  }
  if (Array.isArray(node.any)) {
    const parts = node.any.map((n: AnySpec) => walk(n, kind, byId, states)).filter(Boolean);
    return parts.length > 1 ? `(${parts.join(" or ")})` : parts.join(" or ");
  }
  if (node.not !== undefined) return `not (${walk(node.not, kind, byId, states)})`;
  // inline condition
  return condStr(node, kind, byId);
}

export function describeStrategy(kind: string, spec: AnySpec): StrategyView {
  const empty: StrategyView = { indicators: [], buy: "", exit: "" };
  try {
    if (!spec || typeof spec !== "object") return empty;
    const indsArr: AnySpec[] = Array.isArray(spec.indicators) ? spec.indicators : [];
    const byId: Record<string, AnySpec> = {};
    for (const i of indsArr) if (i && i.id) byId[String(i.id)] = i;

    const indicators = indsArr.map((i) => ({
      id: String(i?.id ?? "?"),
      label: fnTerm(String(i?.fn ?? ""), paramStr(i)),
      timeframe: i?.timeframe ? String(i.timeframe) : undefined,
    }));

    return {
      indicators,
      buy: walk(spec.buy_when, kind, byId, spec.states),
      exit: walk(spec.exit_when, kind, byId, spec.states),
      type: spec.type ? String(spec.type) : undefined,
      baseTimeframe: spec.base_timeframe ? String(spec.base_timeframe) : undefined,
    };
  } catch {
    return empty; // never throw — server component crash guard
  }
}
