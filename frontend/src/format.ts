export function formatDecimal(value: number, maxFractionDigits = 3): string {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: maxFractionDigits,
  }).format(value);
}

export function formatPercent(value: number): string {
  return `${formatDecimal(value * 100)}%`;
}
