import type { Availability, BucketKey, DocStatus, RunStatus } from '../types';

export function money(millions: number | null | undefined): string {
  if (millions === null || millions === undefined) return '—';
  return millions >= 1000 ? `S$${(millions / 1000).toFixed(1)}B` : `S$${Math.round(millions)}M`;
}

// Runs saved before the field formats were tightened can hold long values; show the short form.
export function shortExchange(exchange: string | null | undefined): string {
  if (!exchange) return '?';
  return exchange.match(/\(([A-Z]{2,6})\)/)?.[1] ?? exchange.trim();
}

export function listing(item: { listed: boolean; exchange: string | null; ticker: string | null }): string {
  return item.listed ? `${shortExchange(item.exchange)}: ${item.ticker ?? '?'}` : 'Unlisted';
}

export function shortPlace(place: string | null | undefined): string | null {
  return place ? place.split('(')[0].replace(/[,;\s]+$/, '') || null : null;
}

/** '2026-10-01 (StockAnalysis)' -> '1 Oct 2026'; null if there is no date in the text. */
export function asOfLabel(text: string | null | undefined): string | null {
  const iso = text?.match(/\d{4}-\d{2}-\d{2}/)?.[0];
  if (iso) return new Date(iso).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', year: 'numeric' });
  return text?.match(/\b\d{1,2} [A-Z][a-z]{2,8} \d{4}\b/)?.[0] ?? null;
}

export function initials(name: string): string {
  const words = name.replace(/\(.*?\)/g, '').split(/\s+/).filter(w => /^[A-Za-z]/.test(w));
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? '?').slice(0, 2)).toUpperCase();
}

export function domain(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return '';
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export const statusInfo: Record<RunStatus, { label: string; tone: string }> = {
  created: { label: 'Starting', tone: 'gray' },
  researching: { label: 'Researching', tone: 'blue' },
  awaiting_scope: { label: 'Confirm scope', tone: 'amber' },
  analysing: { label: 'Analysing', tone: 'blue' },
  awaiting_findings: { label: 'Review findings', tone: 'amber' },
  exported: { label: 'Exported', tone: 'green' },
};

export const availabilityInfo: Record<Availability, { label: string; tone: string; text: string }> = {
  good: { label: 'Good', tone: 'green', text: 'Annual report with a risk management section' },
  partial: { label: 'Partial', tone: 'amber', text: 'Report found, but no risk section located' },
  none: { label: 'None', tone: 'red', text: 'No usable document yet' },
};

export const docStatusInfo: Record<DocStatus, { label: string; tone: string }> = {
  found: { label: 'Found', tone: 'gray' },
  ok: { label: 'Checked', tone: 'green' },
  check_failed: { label: 'Check failed', tone: 'red' },
  download_failed: { label: 'Download failed', tone: 'red' },
  not_pdf: { label: 'Not a PDF', tone: 'amber' },
  uploaded: { label: 'Supplied by you', tone: 'blue' },
  na: { label: 'Not available', tone: 'gray' },
};

export const bucketLabel = (key: BucketKey | null | undefined) =>
  key ? { large: 'Large cap', mid: 'Mid cap', small: 'Small cap' }[key] : 'Unknown';

export const docTypeLabel = (type: string) =>
  ({ annual_report: 'Annual report', sustainability_report: 'Sustainability report',
     corporate_governance_report: 'Governance report' } as Record<string, string>)[type] ?? 'Other';

export const plural = (n: number, word: string, many = word + 's') => `${n} ${n === 1 ? word : many}`;
