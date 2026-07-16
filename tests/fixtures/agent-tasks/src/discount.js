const RATES = { SAVE10: 0.1, SAVE25: 0.25 };

export function discountRateFor(code) {
  return RATES[String(code ?? "").toUpperCase()] ?? 0;
}
