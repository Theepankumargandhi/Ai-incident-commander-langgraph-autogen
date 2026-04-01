import type { ReactNode } from "react";

type StatusBadgeProps = {
  tone: string;
  children: ReactNode;
};

export function StatusBadge({ tone, children }: StatusBadgeProps) {
  return <span className={`status-badge status-${tone}`}>{children}</span>;
}
