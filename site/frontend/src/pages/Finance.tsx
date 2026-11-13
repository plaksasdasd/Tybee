import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { CustomSelect, DateRangePicker } from "../Controls";
import { FormEvent, useRef, useState } from "react";
import { newRequestKey } from "../supportDraft";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, FinOp, fmtDate, fmtRub, json, Me, Payout, PAYOUT_STATUS, StaffMember } from "../api";
import { TableSkeletonRows } from "../Skeletons";
import { toast } from "../Toaster";
import PayrollPage from "./Payroll";

const KINDS = [["salary", "Зарплата"], ["bonus", "Премия"], ["fine", "Штраф"], ["adjustment", "Корректировка"]];
const ALL_KINDS = [...KINDS, ["payout", "Выплата"], ["reversal", "Отмена операции"]];
const today = () => new Date().toISOString().slice(0, 10);
const monthAgo = () => new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
const qs = (o: Record<string, string>) => new URLSearchParams(Object.entries(o).filter(([, v]) => v)).toString();

function Csv({ report, params }: { report: string; params: Record<string, string> }) {
  return <a className="btn ghost" href={`/api/finance/export/${report}.xlsx?${qs(params)}`}>XLSX</a>;
}

function Accrue({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const staff = useQuery({ queryKey: ["staff", ""], queryFn: () => api<StaffMember[]>("/api/staff") });
  const targets = (staff.data ?? []).filter((s) => s.manageable && s.is_active);
  const [f, setF] = useState({ staff_id: "", kind: "bonus", amount: "", period: today().slice(0, 7), comment: "" });
  const [err, setErr] = useState("");
  const requestKey = useRef(newRequestKey());
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const create = useMutation({
    mutationFn: () => api("/api/finance/operations", { ...json("POST", {
      staff_id: Number(f.staff_id), kind: f.kind, amount: f.amount.replace(",", "."),
      period: f.kind === "salary" ? f.period : null, comment: f.comment || null,
    }), headers: { "Idempotency-Key": requestKey.current } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["fin-ops"] }); qc.invalidateQueries({ queryKey: ["fin-staff"] }); onClose(); toast("Операция добавлена"); },
    onError: (e: Error) => setErr(e.message),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); setErr(""); create.mutate(); };
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}><DialogContent className="app-dialog" aria-describedby={undefined}>
      <form className="form" onSubmit={submit}>
        <DialogTitle>Начислить</DialogTitle>
        <label>Сотрудник
          <CustomSelect value={f.staff_id} onChange={set("staff_id")}>
            <option value="" disabled>Выберите сотрудника</option>
            {targets.map((s) => <option key={s.telegram_id} value={s.telegram_id}>{s.display_name} · {s.role_label}</option>)}
          </CustomSelect>
        </label>
        <label>Вид<CustomSelect value={f.kind} onChange={set("kind")}>{KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</CustomSelect></label>
        <label>Сумма, ₽{f.kind === "adjustment" && " (можно отрицательную)"}<Input inputMode="decimal" value={f.amount} onChange={set("amount")} /></label>
        {f.kind === "salary" && <label>Период<Input type="month" value={f.period} onChange={set("period")} /></label>}
        <label>Комментарий{["fine", "adjustment"].includes(f.kind) && " (обязателен)"}<Input value={f.comment} maxLength={1000} onChange={set("comment")} /></label>
        {err && <p className="error">{err}</p>}
        <div className="row-actions">
          <Button type="button" className="ghost" onClick={onClose}>Отмена</Button>
          <Button disabled={create.isPending || !f.amount.trim() || !f.staff_id}>Начислить</Button>
        </div>
      </form>
    </DialogContent></Dialog>
  );
}

function Operations({ me }: { me: Me }) {
  const qc = useQueryClient();
  const canAccrue = me.permissions.includes("finance.bonuses");
  const [f, setF] = useState({ staff_id: "", kind: "", from: "", to: "" });
  const [modal, setModal] = useState(false);
  const [reversing, setReversing] = useState<FinOp | null>(null);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const staff = useQuery({
    queryKey: ["staff", ""], queryFn: () => api<StaffMember[]>("/api/staff"), enabled: me.permissions.includes("staff.view"),
  });
  const q = useInfiniteQuery({
    queryKey: ["fin-ops", f], initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => api<{ items: FinOp[]; next_before_id: number | null }>(`/api/finance/operations?${qs({ ...f, ...(pageParam ? { before_id: String(pageParam) } : {}) })}`),
    getNextPageParam: (l) => l.next_before_id,
  });
  const reverse = useMutation({
    mutationFn: ({ id, comment }: { id: number; comment: string }) => api(`/api/finance/operations/${id}/reverse`, json("POST", { comment })),
    onSuccess: () => { setReversing(null); setReason(""); setErr(""); qc.invalidateQueries({ queryKey: ["fin-ops"] }); qc.invalidateQueries({ queryKey: ["fin-staff"] }); toast("Операция отменена сторно"); }, onError: (e: Error) => setErr(e.message),
  });
  // Rows come back in id order, which is not always the date order people read.
  const rows = [...(q.data?.pages.flatMap((p) => p.items) ?? [])].sort((a, b) =>
    (b.created_at ?? "").localeCompare(a.created_at ?? "") || b.id - a.id,
  );
  return (
    <>
      <div className="filters">
        {staff.data && (
          <CustomSelect value={f.staff_id} onChange={set("staff_id")}>
            <option value="">Все сотрудники</option>
            {staff.data.map((s) => <option key={s.telegram_id} value={s.telegram_id}>{s.display_name}</option>)}
          </CustomSelect>
        )}
        <CustomSelect value={f.kind} onChange={set("kind")}><option value="">Все виды</option>{ALL_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</CustomSelect>
        <DateRangePicker from={f.from} to={f.to} onChange={(range) => setF((current) => ({ ...current, ...range }))} />
        <span style={{ flex: 1 }} />
        {me.permissions.includes("finance.analytics") && <Csv report="operations" params={f} />}
        {canAccrue && <Button onClick={() => setModal(true)}>Начислить</Button>}
      </div>
      {err && <p className="error">{err}</p>}
      <Table>
        <TableHeader><TableRow><TableHead>Когда</TableHead><TableHead>Сотрудник</TableHead><TableHead>Вид</TableHead><TableHead>Сумма</TableHead><TableHead>Комментарий</TableHead><TableHead>Кто провёл</TableHead>{canAccrue && <TableHead></TableHead>}</TableRow></TableHeader>
        <TableBody>
          {q.isLoading && <TableSkeletonRows rows={7} cols={canAccrue ? 7 : 6} />}
          {rows.map((o) => (
            <TableRow key={o.id} style={o.reversed ? { opacity: 0.55 } : undefined}>
              <TableCell>{fmtDate(o.created_at)}</TableCell><TableCell>{o.staff_name}</TableCell>
              <TableCell>{o.kind_label}{o.period && <span className="muted"> · {o.period}</span>}{o.reversal_of && <span className="muted"> · №{o.reversal_of}</span>}</TableCell>
              <TableCell className={o.amount_kop < 0 ? "neg" : "pos"}>{fmtRub(o.amount_kop)}</TableCell>
              <TableCell className="wrap">{o.comment ?? "—"}</TableCell><TableCell>{o.created_by_name ?? "—"}</TableCell>
              {canAccrue && <TableCell>{!o.reversed && !["reversal", "payout"].includes(o.kind) && <Button className="ghost" onClick={() => { setErr(""); setReason(""); setReversing(o); }}>Отменить операцию</Button>}</TableCell>}
            </TableRow>
          ))}
          {!q.isLoading && !rows.length && <TableRow><TableCell colSpan={7} className="muted">Операций нет</TableCell></TableRow>}
        </TableBody>
      </Table>
      {q.hasNextPage && <p><Button className="ghost" onClick={() => q.fetchNextPage()}>Ещё</Button></p>}
      {modal && <Accrue onClose={() => setModal(false)} />}
      {reversing && <Dialog open onOpenChange={(open) => { if (!open && !reverse.isPending) { setReversing(null); setErr(""); } }}><DialogContent className="app-dialog" aria-describedby={undefined}>
        <form className="form" onSubmit={(e) => { e.preventDefault(); if (reason.trim()) { setErr(""); reverse.mutate({ id: reversing.id, comment: reason.trim() }); } }}>
          <DialogTitle>Отменить операцию</DialogTitle>
          <p>{reversing.staff_name} · {reversing.kind_label} · {fmtRub(reversing.amount_kop)}</p>
          <p className="muted">Будет создана операция с противоположной суммой. Исходная запись сохранится в истории.</p>
          <label>Причина отмены<Input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} autoFocus /></label>
          {err && <p className="error" role="alert">{err}</p>}
          <div className="row-actions"><Button type="button" className="ghost" disabled={reverse.isPending} onClick={() => { setReversing(null); setErr(""); }}>Назад</Button><Button disabled={reverse.isPending || !reason.trim()}>Подтвердить отмену</Button></div>
        </form>
      </DialogContent></Dialog>}
    </>
  );
}

type LogisticsRow = { id: number; order_id: number | null; purpose: string; amount_kop: number; requisites: string; status: string; created_by: number; created_by_name: string | null; created_at: string; decided_by_name: string | null; decision_comment: string | null; paid_reference: string | null };
function Logistics({ me }: { me: Me }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ order_id: "", purpose: "", amount: "", requisites: "" });
  const [decision, setDecision] = useState<{ row: LogisticsRow; action: "reject" | "paid" } | null>(null);
  const [detail, setDetail] = useState("");
  const [err, setErr] = useState("");
  const key = useRef(newRequestKey());
  const q = useQuery({ queryKey: ["logistics"], queryFn: () => api<LogisticsRow[]>("/api/finance/logistics"), refetchInterval: 10000 });
  const create = useMutation({ mutationFn: () => api("/api/finance/logistics", { ...json("POST", { ...f, order_id: f.order_id ? Number(f.order_id) : null, amount: f.amount.replace(",", ".") }), headers: { "Idempotency-Key": key.current } }),
    onSuccess: () => { key.current = newRequestKey(); setF({ order_id: "", purpose: "", amount: "", requisites: "" }); setOpen(false); setErr(""); qc.invalidateQueries({ queryKey: ["logistics"] }); toast("Заявка на логистику создана"); }, onError: (e: Error) => setErr(e.message) });
  const act = useMutation({ mutationFn: ({ id, action, value }: { id: number; action: string; value: string }) => api(`/api/finance/logistics/${id}/${action}`, json("POST", { detail: value })),
    onSuccess: () => { setDecision(null); setDetail(""); setErr(""); qc.invalidateQueries({ queryKey: ["logistics"] }); toast("Заявка обновлена"); }, onError: (e: Error) => setErr(e.message) });
  const labels: Record<string, string> = { requested: "Ожидает решения", approved: "Одобрена", rejected: "Отклонена", paid: "Оплачена" };
  return <>
    <div className="filters"><p className="muted">Заявки на расходы по выкупу и логистике. Перевод выполняется вне системы.</p><span style={{ flex: 1 }} />{me.permissions.includes("buyout.manage") && <Button onClick={() => { setErr(""); setOpen(true); }}>Создать заявку</Button>}</div>
    {(err && !open && !decision) && <p className="error" role="alert">{err}</p>}{q.error && <p className="error">{q.error.message}</p>}
    <Table><TableHeader><TableRow><TableHead>Заявка</TableHead><TableHead>Назначение</TableHead><TableHead>Сумма</TableHead><TableHead>Реквизиты</TableHead><TableHead>Статус</TableHead><TableHead>Действия</TableHead></TableRow></TableHeader><TableBody>
      {q.isLoading && <TableSkeletonRows rows={4} cols={6} />}
      {(q.data ?? []).map((r) => <TableRow key={r.id}><TableCell>#{r.id}<div className="muted">{r.created_by_name} · {fmtDate(r.created_at)}</div>{r.order_id && <div className="muted">Заказ #{r.order_id}</div>}</TableCell><TableCell className="wrap">{r.purpose}</TableCell><TableCell>{fmtRub(r.amount_kop)}</TableCell><TableCell style={{ whiteSpace: "pre-wrap" }}>{r.requisites}</TableCell><TableCell className="wrap">{labels[r.status] ?? r.status}{r.decided_by_name && <div className="muted">{r.decided_by_name}</div>}{r.decision_comment && <div className="muted">{r.decision_comment}</div>}{r.paid_reference && <div className="muted">Перевод: {r.paid_reference}</div>}</TableCell><TableCell><div className="row-actions">{me.permissions.includes("finance.payouts.approve") && r.created_by !== me.telegram_id && r.status === "requested" && <><Button disabled={act.isPending} onClick={() => act.mutate({ id: r.id, action: "approve", value: "" })}>Одобрить</Button><Button className="ghost" disabled={act.isPending} onClick={() => { setErr(""); setDetail(""); setDecision({ row: r, action: "reject" }); }}>Отклонить</Button></>}{me.permissions.includes("finance.payouts.approve") && r.created_by !== me.telegram_id && r.status === "approved" && <Button disabled={act.isPending} onClick={() => { setErr(""); setDetail(""); setDecision({ row: r, action: "paid" }); }}>Оплачена</Button>}</div></TableCell></TableRow>)}
      {q.data && !q.data.length && <TableRow><TableCell colSpan={6} className="muted">Заявок нет</TableCell></TableRow>}</TableBody></Table>
    {open && <Dialog open onOpenChange={(v) => { if (!v && !create.isPending) setOpen(false); }}><DialogContent className="app-dialog" aria-describedby={undefined}><form className="form" onSubmit={(e) => { e.preventDefault(); setErr(""); create.mutate(); }}><DialogTitle>Заявка на расход</DialogTitle><label>Номер заказа (необязательно)<Input type="number" min="1" value={f.order_id} onChange={(e) => setF({ ...f, order_id: e.target.value })} /></label><label>Назначение расхода<Input required maxLength={256} value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })} /></label><label>Сумма, ₽<Input required inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></label><label>Получатель и реквизиты<Input required maxLength={500} value={f.requisites} onChange={(e) => setF({ ...f, requisites: e.target.value })} /></label>{err && <p className="error" role="alert">{err}</p>}<Button disabled={create.isPending || !f.purpose.trim() || !f.amount || !f.requisites.trim()}>Отправить заявку</Button></form></DialogContent></Dialog>}
    {decision && <Dialog open onOpenChange={(v) => { if (!v && !act.isPending) setDecision(null); }}><DialogContent className="app-dialog" aria-describedby={undefined}><form className="form" onSubmit={(e) => { e.preventDefault(); if (detail.trim()) act.mutate({ id: decision.row.id, action: decision.action, value: detail.trim() }); }}><DialogTitle>{decision.action === "paid" ? "Подтвердить перевод" : "Отклонить заявку"}</DialogTitle><p>{decision.row.purpose} · {fmtRub(decision.row.amount_kop)}</p>{decision.action === "paid" && <p className="muted">Укажите номер уже выполненного перевода. Система не переводит деньги.</p>}<label>{decision.action === "paid" ? "Номер перевода" : "Причина отказа"}<Input required maxLength={decision.action === "paid" ? 128 : 1000} value={detail} onChange={(e) => setDetail(e.target.value)} /></label>{err && <p className="error" role="alert">{err}</p>}<Button disabled={act.isPending || !detail.trim()}>Подтвердить</Button></form></DialogContent></Dialog>}
  </>;
}

function Payouts({ me }: { me: Me }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState("requested");
  const [err, setErr] = useState("");
  const [decision, setDecision] = useState<{ payout: Payout; action: "paid" | "reject" } | null>(null);
  const [detail, setDetail] = useState("");
  const q = useQuery({ queryKey: ["fin-payouts", status], queryFn: () => api<Payout[]>(`/api/finance/payouts?${qs({ status })}`), refetchInterval: 10000 });
  // Show the newest request first even when the API returns rows in id order.
  const rows = [...(q.data ?? [])].sort((a, b) =>
    (b.created_at ?? "").localeCompare(a.created_at ?? "") || b.id - a.id,
  );
  const act = useMutation({
    mutationFn: ({ id, action, body }: { id: number; action: string; body?: unknown }) => api(`/api/finance/payouts/${id}/${action}`, json("POST", body ?? {})),
    onSuccess: (_data, variables) => {
      setErr(""); setDecision(null); setDetail("");
      qc.invalidateQueries({ queryKey: ["fin-payouts"] }); qc.invalidateQueries({ queryKey: ["fin-summary"] }); qc.invalidateQueries({ queryKey: ["fin-staff"] }); qc.invalidateQueries({ queryKey: ["fin-ops"] });
      toast(variables.action === "paid" ? "Выплата отмечена" : "Заявка отклонена");
    },
    onError: (e: Error) => setErr(e.message),
  });
  const openDecision = (payout: Payout, action: "paid" | "reject") => { setErr(""); setDetail(""); setDecision({ payout, action }); };
  return (
    <>
      <div className="filters">
        <CustomSelect value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Все статусы</option>
          {Object.entries(PAYOUT_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </CustomSelect>
      </div>
      {err && !decision && <p className="error" role="alert">{err}</p>}
      <Table>
        <TableHeader><TableRow><TableHead>№</TableHead><TableHead>Сотрудник</TableHead><TableHead>Сумма</TableHead><TableHead>Реквизиты</TableHead><TableHead>Статус</TableHead><TableHead></TableHead></TableRow></TableHeader>
        <TableBody>
          {q.isLoading && <TableSkeletonRows rows={5} cols={6} />}
          {(rows).map((p) => (
            <TableRow key={p.id}>
              <TableCell>{p.id}<div className="muted">{fmtDate(p.created_at)}</div></TableCell>
              <TableCell className="wrap">{p.staff_name}{p.comment && <div className="muted">{p.comment}</div>}</TableCell>
              <TableCell>{fmtRub(p.amount_kop)}</TableCell>
              <TableCell style={{ whiteSpace: "pre-wrap" }}>{p.requisites}</TableCell>
              <TableCell className="wrap">
                <span className={`badge ${PAYOUT_STATUS[p.status]?.cls}`}>{PAYOUT_STATUS[p.status]?.label ?? p.status}</span>
                {p.decided_by_name && <div className="muted">{p.decided_by_name}</div>}
                {p.decision_comment && <div className="muted">{p.decision_comment}</div>}
                {p.paid_reference && <div className="muted">Перевод: {p.paid_reference}</div>}
              </TableCell>
              <TableCell>
                <div className="row-actions" style={{ marginTop: 0 }}>
                  {p.status === "requested" && me.permissions.includes("finance.payouts.approve") && <><Button disabled={act.isPending} onClick={() => act.mutate({ id: p.id, action: "approve" })}>Одобрить</Button><Button className="ghost" disabled={act.isPending} onClick={() => openDecision(p, "reject")}>Отклонить</Button></>}
                  {p.status === "approved" && me.permissions.includes("finance.payouts.pay") && <Button disabled={act.isPending} onClick={() => openDecision(p, "paid")}>Выплачено</Button>}
                </div>
              </TableCell>
            </TableRow>
          ))}
          {q.data && !q.data.length && <TableRow><TableCell colSpan={6} className="muted">Запросов нет</TableCell></TableRow>}
        </TableBody>
      </Table>
      {decision && <Dialog open onOpenChange={(open) => { if (!open && !act.isPending) { setDecision(null); setErr(""); } }}><DialogContent className="app-dialog" aria-describedby={undefined}>
        <form className="form" onSubmit={(e) => { e.preventDefault(); const value = detail.trim(); if (value) act.mutate({ id: decision.payout.id, action: decision.action, body: decision.action === "paid" ? { reference: value } : { comment: value } }); }}>
          <DialogTitle>{decision.action === "paid" ? "Подтвердить выплату" : "Отклонить заявку"}</DialogTitle>
          <p>{decision.payout.staff_name} · {fmtRub(decision.payout.amount_kop)}</p>
          {decision.action === "paid" && <p className="muted">Отметьте выплату после перевода денег сотруднику. Кнопка не выполняет банковский перевод.</p>}
          <label>{decision.action === "paid" ? "Номер перевода" : "Причина отказа"}<Input value={detail} onChange={(e) => setDetail(e.target.value)} maxLength={decision.action === "paid" ? 128 : 1000} autoFocus /></label>
          {err && <p className="error" role="alert">{err}</p>}
          <div className="row-actions"><Button type="button" className="ghost" disabled={act.isPending} onClick={() => { setDecision(null); setErr(""); }}>Назад</Button><Button disabled={act.isPending || !detail.trim()}>{decision.action === "paid" ? "Подтвердить выплату" : "Отклонить"}</Button></div>
        </form>
      </DialogContent></Dialog>}
    </>
  );
}

type StaffRow = Record<"salary" | "bonus" | "fine" | "adjustment" | "paid" | "balance" | "reserved", number>;
function Settlements() {
  const [range, setRange] = useState({ from: "", to: "" });
  const q = useQuery({ queryKey: ["fin-staff", range], queryFn: () => api<{ rows: (StaffRow & { staff_id: number; name: string })[]; total: StaffRow }>(`/api/finance/reports/staff?${qs(range)}`) });
  const cols: [keyof StaffRow, string][] = [["salary", "Зарплата"], ["bonus", "Премии"], ["fine", "Штрафы"], ["adjustment", "Корректировки"], ["paid", "Выплачено"], ["balance", "Баланс (долг)"], ["reserved", "В резерве"]];
  return (
    <>
      <div className="filters">
        <DateRangePicker from={range.from} to={range.to} onChange={setRange} />
        <span style={{ flex: 1 }} /><Csv report="staff" params={range} />
      </div>
      {q.data && <p>Мы должны сотрудникам: <b>{fmtRub(q.data.total.balance)}</b> · в резерве под выплаты {fmtRub(q.data.total.reserved)}</p>}
      <p className="muted">Периодные колонки считаются за выбранные даты; баланс и резерв — на текущий момент.</p>
      <Table>
        <TableHeader><TableRow><TableHead>Сотрудник</TableHead>{cols.map(([, l]) => <TableHead key={l}>{l}</TableHead>)}</TableRow></TableHeader>
        <TableBody>
          {q.isLoading && <TableSkeletonRows rows={6} cols={8} />}
          {q.data?.rows.map((r) => <TableRow key={r.staff_id}><TableCell>{r.name}</TableCell>{cols.map(([k]) => <TableCell key={k}>{fmtRub(r[k])}</TableCell>)}</TableRow>)}
          {q.data && <TableRow><TableCell><b>Итого</b></TableCell>{cols.map(([k]) => <TableCell key={k}><b>{fmtRub(q.data!.total[k])}</b></TableCell>)}</TableRow>}
        </TableBody>
      </Table>
    </>
  );
}

type RevRow = {
  key: string; label: string; orders_count: number; orders_sum: number; fines_count: number; fines_sum: number; total: number;
  refunds_count: number; refunds_sum: number; net: number;
};
function Revenue() {
  const [f, setF] = useState({ from: monthAgo(), to: today(), group_by: "day" });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const q = useQuery({ queryKey: ["fin-rev", f], queryFn: () => api<{ rows: RevRow[]; total: RevRow }>(`/api/finance/reports/revenue?${qs(f)}`) });
  const max = Math.max(1, ...(q.data?.rows.map((r) => r.total) ?? []));
  return (
    <>
      <div className="filters">
        <DateRangePicker from={f.from} to={f.to} onChange={(range) => setF((current) => ({ ...current, ...range }))} />
        <CustomSelect value={f.group_by} onChange={set("group_by")}><option value="day">По дням</option><option value="cover">По обложкам и Avito</option><option value="staff">По сотрудникам</option></CustomSelect>
        <span style={{ flex: 1 }} /><Csv report="revenue" params={f} />
      </div>
      {q.data && <p>Выручка за период: <b>{fmtRub(q.data.total.total)}</b> (заказы {fmtRub(q.data.total.orders_sum)}, штрафы за разбан {fmtRub(q.data.total.fines_sum)}) · возвраты {fmtRub(q.data.total.refunds_sum)} · чистая выручка <b>{fmtRub(q.data.total.net)}</b></p>}
      <p className="muted">Данные поступают из оплаченных заказов и подтверждённых оплат за разбан; возвраты вычитаются. Штрафы сотрудников учитываются отдельно в расчётах с сотрудниками.</p>
      {f.group_by === "day" && q.data && q.data.rows.length > 0 && (
        <div className="chart">
          {q.data.rows.map((r) => (
            <div key={r.key} className="bar" title={`${r.label}: ${fmtRub(r.total)}`}><i style={{ height: `${(r.total / max) * 100}%` }} /><span>{r.label.slice(5)}</span></div>
          ))}
        </div>
      )}
      <Table>
        <TableHeader><TableRow><TableHead>Группа</TableHead><TableHead>Оплат заказов</TableHead><TableHead>Заказы</TableHead><TableHead>Штрафов за разбан</TableHead><TableHead>Штрафы</TableHead><TableHead>Итого</TableHead><TableHead>Возвраты</TableHead><TableHead>Чистая выручка</TableHead></TableRow></TableHeader>
        <TableBody>
          {q.isLoading && <TableSkeletonRows rows={6} cols={8} />}
          {q.data?.rows.map((r) => (
            <TableRow key={r.key}>
              <TableCell>{r.label}</TableCell><TableCell>{r.orders_count}</TableCell><TableCell>{fmtRub(r.orders_sum)}</TableCell><TableCell>{r.fines_count}</TableCell><TableCell>{fmtRub(r.fines_sum)}</TableCell>
              <TableCell>{fmtRub(r.total)}</TableCell><TableCell className={r.refunds_sum ? "neg" : ""}>{r.refunds_sum ? `−${fmtRub(r.refunds_sum)} (${r.refunds_count})` : "—"}</TableCell><TableCell><b>{fmtRub(r.net)}</b></TableCell>
            </TableRow>
          ))}
          {q.data && !q.data.rows.length && <TableRow><TableCell colSpan={8} className="muted">Оплат за период нет</TableCell></TableRow>}
        </TableBody>
      </Table>
    </>
  );
}

export default function Finance({ me }: { me: Me }) {
  const has = (p: string) => me.permissions.includes(p);
  const tabs = [
    { id: "ops", label: "Операции", show: has("finance.bonuses") || has("finance.analytics") },
    { id: "payroll", label: "Зарплата саппорта", show: has("finance.bonuses") || has("finance.analytics") },
    { id: "payouts", label: "Выплаты", show: has("finance.payouts.approve") || has("finance.payouts.pay") },
    { id: "logistics", label: "Расходы на логистику", show: has("buyout.manage") || has("finance.payouts.approve") },
    { id: "staff", label: "Расчёты с сотрудниками", show: has("finance.analytics") },
    { id: "revenue", label: "Выручка", show: has("finance.analytics") },
  ].filter((t) => t.show);
  const [tab, setTab] = useState(tabs[0]?.id);
  const summary = useQuery({ queryKey: ["fin-summary"], queryFn: () => api<{ requested: number }>("/api/finance/summary"), enabled: has("finance.payouts.approve"), refetchInterval: 15000 });
  return (
    <>
      <h1>Финансы</h1>
      <Tabs value={tab} onValueChange={setTab}><TabsList className="app-tabs">{tabs.map((t) => <TabsTrigger key={t.id} value={t.id} data-guide={`fin:${t.id}`}>{t.label}{t.id === "payouts" && !!summary.data?.requested && <span className="finance-tab-count" aria-label={`Ожидают решения: ${summary.data.requested}`}>{summary.data.requested}</span>}</TabsTrigger>)}</TabsList></Tabs>
      {!!summary.data?.requested && <div className="card finance-payout-notice" role="status"><span>Ожидают решения заявки на выплату: <b>{summary.data.requested}</b></span>{tab !== "payouts" && <Button className="ghost" onClick={() => setTab("payouts")}>Посмотреть заявки</Button>}</div>}
      {tab === "ops" && <Operations me={me} />}
      {tab === "payroll" && <PayrollPage me={me} />}
      {tab === "payouts" && <Payouts me={me} />}
      {tab === "logistics" && <Logistics me={me} />}
      {tab === "staff" && <Settlements />}
      {tab === "revenue" && <Revenue />}
    </>
  );
}
