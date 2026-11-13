import { CSSProperties } from "react";
import { Card } from "@/components/ui/card";
import { TableCell, TableRow } from "@/components/ui/table";

/**
 * Placeholders shown while data loads. Every page draws the shape of what is
 * coming instead of replacing the screen with a "Загрузка…" message, so the
 * layout never jumps and the panel stays usable while a request is in flight.
 */

const WIDTHS = ["72%", "46%", "58%", "40%", "64%", "52%", "34%", "60%", "44%"];

export function Skeleton({ style, className = "" }: { style?: CSSProperties; className?: string }) {
  return <span className={`sk ${className}`.trim()} style={style} aria-hidden="true" />;
}

/** Placeholder lines for a block of text. */
export function SkeletonText({ lines = 3, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className="sk-text" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} style={{ width: WIDTHS[i % WIDTHS.length], height: i === 0 ? 13 : 11 }} />
      ))}
    </div>
  );
}

/** Skeleton rows for a table whose header is already rendered. */
export function TableSkeletonRows({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <>
      {Array.from({ length: rows }, (_, r) => (
        <TableRow key={r} className="sk-row" aria-hidden="true">
          {Array.from({ length: cols }, (_, c) => (
            <TableCell key={c}>
              <Skeleton style={{ width: WIDTHS[(r * 3 + c) % WIDTHS.length], height: c === 0 ? 14 : 12 }} />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

/** Stat tiles (balance, clients, orders…) in the same grid as the real cards. */
export function SkeletonCards({ count = 3 }: { count?: number }) {
  return (
    <div className="sk-cards" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <div className="tile" key={i}>
          <Skeleton style={{ width: `${42 + (i % 3) * 9}%`, height: 10 }} />
          <Skeleton style={{ width: "66%", height: 24 }} />
        </div>
      ))}
    </div>
  );
}

/** Generic page body: title, filter row and a table-shaped block. */
export function PageSkeleton({ rows = 7, cols = 5, title = true }: { rows?: number; cols?: number; title?: boolean }) {
  return (
    <div className="sk-page" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Загрузка…</span>
      {title && <Skeleton className="" style={{ width: 210, height: 30 }} />}
      <div className="sk-blocks" aria-hidden="true">
        <Skeleton style={{ width: 260, height: 34, borderRadius: 9 }} />
        <Skeleton style={{ width: 132, height: 34, borderRadius: 9 }} />
      </div>
      <div className="sk-table" aria-hidden="true">
        <div className="sk-tr is-head" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton key={c} style={{ width: `${48 + (c % 3) * 12}%`, height: 10 }} />
          ))}
        </div>
        {Array.from({ length: rows }, (_, r) => (
          <div className="sk-tr" key={r} style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
            {Array.from({ length: cols }, (_, c) => (
              <Skeleton key={c} style={{ width: WIDTHS[(r * 2 + c) % WIDTHS.length], height: c === 0 ? 14 : 12 }} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Rows of the support dialog list. */
export function SupportListSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="sk-list" role="status" aria-busy="true">
      <span className="sr-only">Загрузка обращений…</span>
      {Array.from({ length: rows }, (_, i) => (
        <div className="item" key={i} aria-hidden="true">
          <Skeleton className="sk-circle" style={{ width: 32, height: 32 }} />
          <div className="item-text">
            <Skeleton style={{ width: `${44 + (i % 3) * 9}%`, height: 13 }} />
            <Skeleton style={{ width: `${30 + (i % 4) * 7}%`, height: 10 }} />
            <Skeleton style={{ width: `${66 - (i % 3) * 8}%`, height: 11 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Generic stacked list (templates, cards): two or three lines per row. */
export function ListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="sk-list" role="status" aria-busy="true">
      <span className="sr-only">Загрузка…</span>
      {Array.from({ length: rows }, (_, i) => (
        <div className="item" key={i} aria-hidden="true">
          <div className="item-text">
            <Skeleton style={{ width: `${44 + (i % 3) * 9}%`, height: 13 }} />
            <Skeleton style={{ width: `${78 - (i % 3) * 10}%`, height: 11 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** The chat pane: header, a few message bubbles and the composer bar. */
export function ChatSkeleton() {
  return (
    <div className="sk-chat" role="status" aria-busy="true">
      <span className="sr-only">Загрузка диалога…</span>
      <Card className="card sup-main sk-chat-card">
        <div className="head" aria-hidden="true">
          <Skeleton className="sk-circle" style={{ width: 38, height: 38 }} />
          <div className="sk-chat-identity">
            <Skeleton style={{ width: 150, height: 14 }} />
            <Skeleton style={{ width: 104, height: 10 }} />
          </div>
          <div className="sk-blocks">
            <Skeleton style={{ width: 74, height: 30, borderRadius: 8 }} />
            <Skeleton style={{ width: 74, height: 30, borderRadius: 8 }} />
          </div>
        </div>
        <div className="sk-chat-messages" aria-hidden="true">
          <Skeleton className="sk-bubble" style={{ width: "58%", height: 52 }} />
          <Skeleton className="sk-bubble" style={{ width: "42%", height: 38 }} />
          <Skeleton className="sk-bubble is-out" style={{ width: "52%", height: 62 }} />
          <Skeleton className="sk-bubble" style={{ width: "64%", height: 46 }} />
          <Skeleton className="sk-bubble is-out" style={{ width: "36%", height: 38 }} />
        </div>
        <div className="composer" aria-hidden="true">
          <Skeleton style={{ width: "100%", height: 46, borderRadius: 12 }} />
        </div>
      </Card>
    </div>
  );
}

/** The frame of the panel: sidebar + content. Used before the session is known. */
export function AppShellSkeleton() {
  return (
    <div className="layout" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Загрузка панели…</span>
      <aside aria-hidden="true">
        <div className="brand">
          <div className="brand-logo-link">
            <img src="/logobig.png" alt="tybee" className="brand-logo-img" />
          </div>
          <span className="brand-tag">панель</span>
        </div>
        <div className="sk-nav">
          {[74, 58, 82, 66, 50, 70, 60].map((width, i) => (
            <div className="sk-nav-item" key={i}>
              <Skeleton style={{ width: 18, height: 18, borderRadius: 5 }} />
              <Skeleton style={{ width: `${width}%`, height: 12 }} />
            </div>
          ))}
        </div>
        <div className="me">
          <Skeleton className="sk-circle" style={{ width: 32, height: 32 }} />
          <div className="me-text">
            <Skeleton style={{ width: "68%", height: 12 }} />
            <Skeleton style={{ width: "44%", height: 10, marginTop: 6 }} />
          </div>
        </div>
      </aside>
      <main>
        <PageSkeleton />
      </main>
    </div>
  );
}

/** Login / invite card while the route chunk is loading. */
export function AuthSkeleton() {
  return (
    <div className="center" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Загрузка…</span>
      <Card className="card login" aria-hidden="true">
        <Skeleton style={{ width: 118, height: 70, borderRadius: 35, margin: "0 auto 22px", display: "block" }} />
        <Skeleton style={{ width: 140, height: 28, margin: "0 auto 8px", display: "block" }} />
        <Skeleton style={{ width: "76%", height: 12, margin: "0 auto 24px", display: "block" }} />
        <Skeleton style={{ width: "100%", height: 46, borderRadius: 12, marginBottom: 10 }} />
        <Skeleton style={{ width: "100%", height: 42, borderRadius: 10 }} />
      </Card>
    </div>
  );
}