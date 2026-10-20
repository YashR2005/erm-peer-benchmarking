import type { ReactNode } from 'react';

export function Badge({ tone = 'gray', dot = false, children, title }:
  { tone?: string; dot?: boolean; children: ReactNode; title?: string }) {
  return <span className={`badge ${tone}`} title={title}>{dot && <i className="dot" />}{children}</span>;
}
