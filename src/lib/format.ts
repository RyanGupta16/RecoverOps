/** All money moves through the app as paise. It is formatted in exactly one place. */

const inr = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});

const inrPrecise = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function rupees(paise: number): string {
  return inr.format(paise / 100);
}

export function rupeesPrecise(paise: number): string {
  return inrPrecise.format(paise / 100);
}

/** Compact form for counters that tick during a batch run. */
export function rupeesCompact(paise: number): string {
  const value = paise / 100;
  const sign = value < 0 ? '-' : '';
  const abs = Math.abs(value);
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(2)} L`;
  if (abs >= 1e3) return `${sign}₹${(abs / 1e3).toFixed(1)}k`;
  return `${sign}₹${abs.toFixed(0)}`;
}

export function percent(fraction: number, decimals = 1): string {
  return `${(fraction * 100).toFixed(decimals)}%`;
}

export function signed(value: number, decimals = 3): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(decimals)}`;
}

export function shortTime(iso: string): string {
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export const SEGMENT_LABELS: Record<string, string> = {
  sure_thing: 'Sure thing',
  persuadable: 'Persuadable',
  lost_cause: 'Lost cause',
  sleeping_dog: 'Sleeping dog',
};
