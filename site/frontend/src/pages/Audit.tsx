import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { CustomSelect, DateRangePicker } from "../Controls";
import { Fragment, useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, AUDIT_GROUPS, auditLabel, auditTarget, AuditEntry, fmtDate, StaffMember } from "../api";
import { TableSkeletonRows } from "../Skeletons";

type Page = { items: AuditEntry[]; next_before_id: number | null };

export default function Audit() {
  const [f, setF] = useState({ actor: "", action: "", target: "", since: "", until: "" });
  const [open, setOpen] = useState<number | null>(null);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const actions = useQuery({ queryKey: ["audit-actions"], queryFn: () => api<string[]>("/api/audit/actions") });
  const staff = useQuery({ queryKey: ["staff", ""], queryFn: () => api<StaffMember[]>("/api/staff") });
  const q = useInfiniteQuery({
    queryKey: ["audit", f],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams({ limit: "50" });
      if (f.actor) p.set("actor", f.actor);
      if (f.action) p.set("action", f.action);
      if (f.target.trim()) p.set("target", f.target.trim());
      if (f.since) p.set("since", new Date(f.since + "T00:00:00").toISOString());
      if (f.until) p.set("until", new Date(f.until + "T23:59:59").toISOString());
      if (pageParam) p.set("before_id", String(pageParam));
      return api<Page>(`/api/audit?${p}`);
    },
    getNextPageParam: (last) => last.next_before_id,
  });
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];
  const groups = Array.from(new Set((actions.data ?? []).map((a) => a.split(".")[0]))).sort();
  const groupName = (g: string) => AUDIT_GROUPS[g] ?? g;
  // Load the next page when the end of the table scrolls into view; the button
  // stays as a fallback for keyboards and browsers without IntersectionObserver.
  const sentinel = useRef<HTMLDivElement>(null);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = q;
  useEffect(() => {
    const node = sentinel.current;
    if (!node || !hasNextPage || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !isFetchingNextPage) void fetchNextPage();
    }, { rootMargin: "400px 0px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);
  return (
    <>
      <h1>Аудит</h1>
      <div className="filters">
        <CustomSelect value={f.actor} onChange={set("actor")}>
          <option value="">Все сотрудники</option>
          {staff.data?.map((s) => <option key={s.telegram_id} value={s.telegram_id}>{s.display_name}</option>)}
        </CustomSelect>
        <CustomSelect value={f.action} onChange={set("action")}>
          <option value="">Все действия</option>
          {groups.map((g) => (
            <optgroup key={g} label={groupName(g)}>
              <option value={g + "."}>{groupName(g)}: все действия</option>
              {actions.data?.filter((a) => a.split(".")[0] === g).map((a) => <option key={a} value={a}>{auditLabel(a)}</option>)}
            </optgroup>
          ))}
        </CustomSelect>
        <Input placeholder="ID объекта (номер диалога, заказа, Telegram ID…)" value={f.target} onChange={set("target")} />
        <DateRangePicker from={f.since} to={f.until} onChange={({ from, to }) => setF((current) => ({ ...current, since: from, until: to }))} />
      </div>
      {q.error && <p className="error">{(q.error as Error).message}</p>}
      <Table>
        <TableHeader><TableRow><TableHead>Когда</TableHead><TableHead>Кто</TableHead><TableHead>Действие</TableHead><TableHead>Объект</TableHead></TableRow></TableHeader>
        <TableBody>
          {q.isLoading && <TableSkeletonRows rows={8} cols={4} />}
          {rows.map((a) => (
            <Fragment key={a.id}>
              <TableRow
                onClick={() => setOpen(open === a.id ? null : a.id)}
                className={a.details ? "is-clickable" : undefined}
                tabIndex={a.details ? 0 : undefined}
                role={a.details ? "button" : undefined}
                aria-expanded={a.details ? open === a.id : undefined}
                onKeyDown={(e) => {
                  if (!a.details) return;
                  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(open === a.id ? null : a.id); }
                }}
                style={{ cursor: a.details ? "pointer" : "default" }}
              >
                <TableCell>{fmtDate(a.created_at)}</TableCell>
                <TableCell>{a.actor_name ?? a.actor_id ?? "—"}</TableCell>
                <TableCell title={a.action}>{auditLabel(a.action, a.details)}</TableCell>
                <TableCell title={a.target ?? undefined}>{auditTarget(a)}</TableCell>
              </TableRow>
              {open === a.id && a.details && (
                <TableRow><TableCell colSpan={4}><pre className="details">{JSON.stringify(a.details, null, 2)}</pre></TableCell></TableRow>
              )}
            </Fragment>
          ))}
          {!rows.length && !q.isLoading && <TableRow><TableCell colSpan={4} className="muted">Записей нет</TableCell></TableRow>}
        </TableBody>
      </Table>
      <div ref={sentinel} aria-hidden="true" />
      {q.hasNextPage && (
        <p><Button disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? "Загружаем…" : "Загрузить ещё"}</Button></p>
      )}
    </>
  );
}
