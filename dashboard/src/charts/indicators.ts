export function calcMA(data: number[], period: number): Array<number | null> {
  return data.map((_, index) => {
    if (index < period - 1) return null;
    let sum = 0;
    for (let i = index - period + 1; i <= index; i += 1) sum += data[i];
    return Number((sum / period).toFixed(2));
  });
}

export function calcEMA(data: number[], period: number): number[] {
  if (data.length === 0) return [];
  const k = 2 / (period + 1);
  let previous = data[0];
  return data.map((value, index) => {
    if (index === 0) return Number(previous.toFixed(2));
    previous = value * k + previous * (1 - k);
    return Number(previous.toFixed(2));
  });
}

export function calcBOLL(data: number[], period: number) {
  const mid = calcMA(data, period);
  const upper: Array<number | null> = [];
  const lower: Array<number | null> = [];
  for (let index = 0; index < data.length; index += 1) {
    if (mid[index] == null) {
      upper.push(null);
      lower.push(null);
      continue;
    }
    let sum = 0;
    let count = 0;
    for (let i = Math.max(0, index - period + 1); i <= index; i += 1) {
      sum += (data[i] - Number(mid[index])) ** 2;
      count += 1;
    }
    const std = Math.sqrt(sum / count);
    upper.push(Number((Number(mid[index]) + 2 * std).toFixed(2)));
    lower.push(Number((Number(mid[index]) - 2 * std).toFixed(2)));
  }
  return { mid, upper, lower };
}
