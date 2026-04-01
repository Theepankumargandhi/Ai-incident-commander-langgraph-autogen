import type { ReactNode } from "react";

type PanelProps = {
  eyebrow: string;
  title: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
};

export function Panel({ eyebrow, title, action, children, className }: PanelProps) {
  return (
    <section className={`panel ${className ?? ""}`.trim()}>
      <header className="panel-header">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h2>{title}</h2>
        </div>
        {action ? <div className="panel-action">{action}</div> : null}
      </header>
      {children}
    </section>
  );
}
