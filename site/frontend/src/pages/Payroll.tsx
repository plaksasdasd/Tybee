import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { CustomSelect } from "../Controls";
import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, fmtDate, fmtRub, json, Me } from "../api";
import { TableSkeletonRows } from "../Skeletons";
import { toast } from "../Toaster";

type Quality = { speed: number; fullness: number; coefficient: number; avg_chars: number; median_answer_sec: number | null };
export type PayrollRow = {
  staff_id: number; name: string; role: string; period: string; base_salary_kop: number;
  messages_count: number; message_rate_kop: number; messages_kop: number;
  new_clients_count: number; client_rate_kop: number; clients_kop: number; dialogue_kop: number;
  salary_kop: number; orders_count: number; orders_base_kop: number; orders_kop: number;
  reviews_count: number; reviews_kop: number; premium_gross_kop: number;
  fine_kop: number; fine_uncovered_kop: number; premium_kop: number; total_kop: number; min_payout_kop: number;
  quality: Quality;
  accrual: null | {
    id: number; salary_kop: number; bonus_kop: number; fine_kop: number; total_kop: number;
    reversed: boolean; created_at: string | null; salary_op_id: number | null; bonus_op_id: number | null;
  };
};
type PayrollConfig = {
  msg_rate_min_kop: number; msg_rate_max_kop: number; client_rate_min_kop: number; client_rate_max_kop: number;
  order_percent_bp: number; review_bonus_kop: number; min_payout_kop: number;
  fast_answer_sec: number; slow_answer_sec: number; full_answer_chars: number;
};
type PayrollReport = {
  period: string; config: PayrollConfig; rows: PayrollRow[];
  members: { telegram_id: number; display_name: string; role: string }[];
  total: Record<string, number>; counts: Record<string, number>;
};
type Review = {
  id: number; staff_id: number; staff_name: string; source: string; source_label: string;
  rating: number; order_id: number | null; comment: string | null; confirmed: boolean; created_at: string | null;
};

const currentMonth = () => new Date().toISOString().slice(0, 7);
const num = (value: number) => String(value).replace(".", ",");
const rubToKop = (value: string) => Math.round(Number(value.replace(",", ".")) * 100);
const kopToRub = (kop: number) => String(kop / 100).replace(".", ",");
const secText = (seconds: number | null) => {
  if (seconds === null) return "нет данных";
  if (seconds < 60) return `${Math.round(seconds)} сек`;
  return `${Math.round(seconds / 60)} мин`;
};

function SettingsDialog({ config, onClose }: { config: PayrollConfig; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({
    msg_rate_min: kopToRub(config.msg_rate_min_kop), msg_rate_max: kopToRub(config.msg_rate_max_kop),
    client_rate_min: kopToRub(config.client_rate_min_kop), client_rate_max: kopToRub(config.client_rate_max_kop),
    order_percent: String(config.order_percent_bp / 100).replace(".", ","),
    review_bonus: kopToRub(config.review_bonus_kop), min_payout: kopToRub(config.min_payout_kop),
    fast_answer_sec: String(config.fast_answer_sec), slow_answer_sec: String(config.slow_answer_sec),
    full_answer_chars: String(config.full_answer_chars),
  });
  const [err, setErr] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const save = useMutation({
    mutationFn: () => api("/api/finance/payroll/settings", json("PUT", {
      msg_rate_min_kop: rubToKop(f.msg_rate_min), msg_rate_max_kop: rubToKop(f.msg_rate_max),
      client_rate_min_kop: rubToKop(f.client_rate_min), client_rate_max_kop: rubToKop(f.client_rate_max),
      order_percent_bp: Math.round(Number(f.order_percent.replace(",", ".")) * 100),
      review_bonus_kop: rubToKop(f.review_bonus), min_payout_kop: rubToKop(f.min_payout),
      fast_answer_sec: Number(f.fast_answer_sec), slow_answer_sec: Number(f.slow_answer_sec),
      full_answer_chars: Number(f.full_answer_chars),
    })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["payroll"] }); toast("Ставки сохранены"); onClose(); },
    onError: (e: Error) => setErr(e.message),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); setErr(""); save.mutate(); };
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}><DialogContent className="app-dialog" aria-describedby={undefined}>
      <form className="form" onSubmit={submit}>
        <DialogTitle>Ставки расчёта</DialogTitle>
        <p className="muted">Ставки за сообщение и за нового клиента динамические: применяется коэффициент качества за месяц (скорость первого ответа и полнота ответов). Коэффициент 1 — верхняя ставка, 0 — нижняя.</p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 12 }}>
          <label>Сообщение от, ₽<Input inputMode="decimal" value={f.msg_rate_min} onChange={set("msg_rate_min")} /></label>
          <label>Сообщение до, ₽<Input inputMode="decimal" value={f.msg_rate_max} onChange={set("msg_rate_max")} /></label>
          <label>Новый клиент от, ₽<Input inputMode="decimal" value={f.client_rate_min} onChange={set("client_rate_min")} /></label>
          <label>Новый клиент до, ₽<Input inputMode="decimal" value={f.client_rate_max} onChange={set("client_rate_max")} /></label>
          <label>Процент с заказов, %<Input inputMode="decimal" value={f.order_percent} onChange={set("order_percent")} /></label>
          <label>Отзыв на максимальную оценку, ₽<Input inputMode="decimal" value={f.review_bonus} onChange={set("review_bonus")} /></label>
          <label>Минимальная выплата, ₽<Input inputMode="decimal" value={f.min_payout} onChange={set("min_payout")} /></label>
          <label>Полный ответ, символов<Input type="number" min="1" value={f.full_answer_chars} onChange={set("full_answer_chars")} /></label>
          <label>Быстрый ответ, сек<Input type="number" min="1" value={f.fast_answer_sec} onChange={set("fast_answer_sec")} /></label>
          <label>Медленный ответ, сек<Input type="number" min="1" value={f.slow_answer_sec} onChange={set("slow_answer_sec")} /></label>
        </div>
        {err && <p className="error">{err}</p>}
        <div className="row-actions">
          <Button type="button" className="ghost" onClick={onClose}>Отмена</Button>
          <Button disabled={save.isPending}>Сохранить</Button>
        </div>
      </form>
    </DialogContent></Dialog>
  );
}

function ReviewDialog({ members, onClose }: { members: PayrollReport["members"]; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ staff_id: members[0]?.telegram_id ? String(members[0].telegram_id) : "", source: "avito", rating: "5", comment: "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const create = useMutation({
    mutationFn: () => api("/api/finance/reviews", json("POST", {
      staff_id: Number(f.staff_id), source: f.source, rating: Number(f.rating), comment: f.comment || null,
    })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["payroll-reviews"] }); qc.invalidateQueries({ queryKey: ["payroll"] }); toast("Отзыв добавлен"); onClose(); },
    onError: (e: Error) => setErr(e.message),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); setErr(""); create.mutate(); };
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}><DialogContent className="app-dialog" aria-describedby={undefined}>
      <form className="form" onSubmit={submit}>
        <DialogTitle>Подтверждённый отзыв</DialogTitle>
        <p className="muted">Премия начисляется за каждый подтверждённый отзыв на максимальную оценку: Avito, Telegram, Яндекс.</p>
        <label>Сотрудник
          <CustomSelect value={f.staff_id} onChange={set("staff_id")}>
            {members.map((m) => <option key={m.telegram_id} value={m.telegram_id}>{m.display_name}</option>)}
          </CustomSelect>
        </label>
        <label>Источник
          <CustomSelect value={f.source} onChange={set("source")}>
            <option value="avito">Avito</option><option value="telegram">Telegram</option><option value="yandex">Яндекс</option>
          </CustomSelect>
        </label>
        <label>Оценка
          <CustomSelect value={f.rating} onChange={set("rating")}>
            {[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{n}{n === 5 ? " · максимальная" : ""}</option>)}
          </CustomSelect>
        </label>
        <label>Комментарий<Input value={f.comment} maxLength={1000} onChange={set("comment")} /></label>
        {err && <p className="error">{err}</p>}
        <div className="row-actions">
          <Button type="button" className="ghost" onClick={onClose}>Отмена</Button>
          <Button disabled={create.isPending || !f.staff_id}>Добавить</Button>
        </div>
      </form>
    </DialogContent></Dialog>
  );
}

function RowDetails({ row, onClose, canManage }: { row: PayrollRow; onClose: () => void; canManage: boolean }) {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const cancel = useMutation({
    mutationFn: () => api(`/api/finance/payroll/${row.staff_id}/cancel`, json("POST", { period: row.period })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["payroll"] }); qc.invalidateQueries({ queryKey: ["fin-ops"] }); toast("Начисление отменено"); onClose(); },
    onError: (e: Error) => setErr(e.message),
  });
  const q = row.quality;
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}><DialogContent className="app-dialog" aria-describedby={undefined}>
      <DialogTitle>{row.name} · {row.period}</DialogTitle>
      <div className="payroll-details">
        <h3>Качество работы</h3>
        <p className="muted">
          Коэффициент <b>{num(q.coefficient)}</b> = 0,5 × скорость ({num(q.speed)}) + 0,5 × полнота ({num(q.fullness)}).<br />
          Медиана первого ответа: {secText(q.median_answer_sec)} · средняя длина ответа: {q.avg_chars} симв.
        </p>
        <h3>Зарплата · {fmtRub(row.salary_kop)}</h3>
        <ul>
          <li>Оклад: {fmtRub(row.base_salary_kop)}</li>
          <li>Исходящие сообщения: {row.messages_count} × {fmtRub(row.message_rate_kop)} = {fmtRub(row.messages_kop)}</li>
          <li>Приёмка новых клиентов: {row.new_clients_count} × {fmtRub(row.client_rate_kop)} = {fmtRub(row.clients_kop)}</li>
        </ul>
        <h3>Премия · {fmtRub(row.premium_kop)}</h3>
        <ul>
          <li>Заказы: {row.orders_count} на {fmtRub(row.orders_base_kop)} → {fmtRub(row.orders_kop)}</li>
          <li>Отзывы на максимальную оценку: {row.reviews_count
            ? `${row.reviews_count} × ${fmtRub(Math.round(row.reviews_kop / row.reviews_count))} = ${fmtRub(row.reviews_kop)}`
            : "нет"}</li>
          <li>Штрафы за период: −{fmtRub(row.fine_kop)} <span className="muted">(уже в балансе отдельными операциями)</span></li>
        </ul>
        {row.fine_uncovered_kop > 0 && (
          <p className="error">
            Штрафы больше премии на {fmtRub(row.fine_uncovered_kop)}: премия обнулена, остаток штрафа остаётся в балансе.
          </p>
        )}
        <p><b>Итого за месяц: {fmtRub(row.total_kop)}</b></p>
        {row.accrual && (
          <p className={row.accrual.reversed ? "muted" : ""}>
            {row.accrual.reversed ? "Начисление отменено" : `Начислено ${fmtDate(row.accrual.created_at)}`} · операции {row.accrual.salary_op_id ? `№${row.accrual.salary_op_id}` : "—"} / {row.accrual.bonus_op_id ? `№${row.accrual.bonus_op_id}` : "—"}
          </p>
        )}
        {err && <p className="error">{err}</p>}
      </div>
      <div className="row-actions">
        <Button type="button" className="ghost" onClick={onClose}>Закрыть</Button>
        {canManage && row.accrual && !row.accrual.reversed && <Button className="danger" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Отменить начисление</Button>}
      </div>
    </DialogContent></Dialog>
  );
}

function Reviews({ period, members, canManage }: { period: string; members: PayrollReport["members"]; canManage: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const q = useQuery({ queryKey: ["payroll-reviews", period], queryFn: () => api<Review[]>(`/api/finance/reviews?period=${period}`) });
  const remove = useMutation({
    mutationFn: (id: number) => api(`/api/finance/reviews/${id}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["payroll-reviews"] }); qc.invalidateQueries({ queryKey: ["payroll"] }); toast("Отзыв удалён"); },
    onError: (e: Error) => setErr(e.message),
  });
  return (
    <section className="card payroll-reviews">
      <div className="head">
        <h2>Отзывы за {period}</h2>
        {canManage && <Button onClick={() => setOpen(true)}>+ Отзыв</Button>}
      </div>
      {err && <p className="error">{err}</p>}
      {q.data?.length ? (
        <Table>
          <TableHeader><TableRow><TableHead>Дата</TableHead><TableHead>Сотрудник</TableHead><TableHead>Источник</TableHead><TableHead>Оценка</TableHead><TableHead>Комментарий</TableHead>{canManage && <TableHead></TableHead>}</TableRow></TableHeader>
          <TableBody>
            {q.data.map((r) => <TableRow key={r.id}>
              <TableCell>{fmtDate(r.created_at)}</TableCell><TableCell>{r.staff_name}</TableCell>
              <TableCell>{r.source_label}</TableCell><TableCell>{r.rating}</TableCell>
              <TableCell className="wrap">{r.comment ?? "—"}</TableCell>
              {canManage && <TableCell><Button className="danger sm" disabled={remove.isPending} onClick={() => remove.mutate(r.id)}>Удалить</Button></TableCell>}
            </TableRow>)}
          </TableBody>
        </Table>
      ) : <p className="muted">Подтверждённых отзывов за этот месяц нет.</p>}
      {open && <ReviewDialog members={members} onClose={() => setOpen(false)} />}
    </section>
  );
}

export default function Payroll({ me }: { me: Me }) {
  const qc = useQueryClient();
  const [period, setPeriod] = useState(currentMonth());
  const [settings, setSettings] = useState(false);
  const [detail, setDetail] = useState<PayrollRow | null>(null);
  const [err, setErr] = useState("");
  const canManage = me.permissions.includes("finance.bonuses");
  const canSettings = me.permissions.includes("finance.analytics");
  const q = useQuery({ queryKey: ["payroll", period], queryFn: () => api<PayrollReport>(`/api/finance/payroll?period=${period}`) });
  const accrue = useMutation({
    mutationFn: (staffId?: number) => api("/api/finance/payroll/accrue", json("POST", { period, staff_id: staffId ?? null })),
    onSuccess: (data: { created: unknown[]; skipped: { reason: string }[] }) => {
      qc.invalidateQueries({ queryKey: ["payroll"] });
      qc.invalidateQueries({ queryKey: ["fin-ops"] });
      qc.invalidateQueries({ queryKey: ["fin-staff"] });
      setErr("");
      const skipped = data.skipped.map((s) => s.reason).join("; ");
      toast(`Начислено: ${data.created.length}${skipped ? ` · пропущено: ${skipped}` : ""}`);
    },
    onError: (e: Error) => setErr(e.message),
  });
  const rows = q.data?.rows ?? [];
  return (
    <>
      <div className="filters">
        <Input type="month" value={period} max={currentMonth()} onChange={(e) => setPeriod(e.target.value)} />
        <span className="muted">Период — календарный месяц (МСК)</span>
        <span style={{ flex: 1 }} />
        {canSettings && <Button className="ghost" onClick={() => setSettings(true)}>Ставки</Button>}
        {canManage && <Button disabled={accrue.isPending || !rows.length} onClick={() => accrue.mutate(undefined)}>Начислить всем</Button>}
      </div>
      {err && <p className="error" role="alert">{err}</p>}
      {q.error && <p className="error">{(q.error as Error).message}</p>}
      {q.data && (
        <p>
          Фонд за месяц: <b>{fmtRub(q.data.total.total_kop)}</b> · зарплата {fmtRub(q.data.total.salary_kop)}
          {" · "}премия {fmtRub(q.data.total.premium_kop)}
          {" · "}сообщений {q.data.counts.messages} · новых клиентов {q.data.counts.new_clients}
          {" · "}заказов {q.data.counts.orders} · отзывов {q.data.counts.reviews}
        </p>
      )}
      <Table>
        <TableHeader><TableRow>
          <TableHead>Сотрудник</TableHead><TableHead>Оклад</TableHead><TableHead>Диалоги</TableHead>
          <TableHead>Заказы</TableHead><TableHead>Отзывы</TableHead><TableHead>Штрафы</TableHead>
          <TableHead>Премия</TableHead><TableHead>Итого</TableHead><TableHead>Статус</TableHead><TableHead></TableHead>
        </TableRow></TableHeader>
        <TableBody>
          {q.isLoading && <TableSkeletonRows rows={5} cols={10} />}
          {rows.map((r) => (
            <TableRow key={r.staff_id}>
              <TableCell>{r.name}<div className="muted">коэффициент {num(r.quality.coefficient)}</div></TableCell>
              <TableCell>{fmtRub(r.base_salary_kop)}</TableCell>
              <TableCell>
                {fmtRub(r.dialogue_kop)}
                <div className="muted">{r.messages_count} сообщ. · {r.new_clients_count} клиентов</div>
              </TableCell>
              <TableCell>
                {fmtRub(r.orders_kop)}
                <div className="muted">{r.orders_count} на {fmtRub(r.orders_base_kop)}</div>
              </TableCell>
              <TableCell>{r.reviews_count ? fmtRub(r.reviews_kop) : "—"}</TableCell>
              <TableCell className={r.fine_kop ? "neg" : ""}>{r.fine_kop ? `−${fmtRub(r.fine_kop)}` : "—"}</TableCell>
              <TableCell>{fmtRub(r.premium_kop)}</TableCell>
              <TableCell><b>{fmtRub(r.total_kop)}</b></TableCell>
              <TableCell>
                {r.accrual ? (
                  <span className={`badge ${r.accrual.reversed ? "off" : "ok"}`}>{r.accrual.reversed ? "Отменено" : "Начислено"}</span>
                ) : <span className="badge wait">Не начислено</span>}
              </TableCell>
              <TableCell>
                <div className="row-actions" style={{ marginTop: 0 }}>
                  <Button className="ghost sm" onClick={() => setDetail(r)}>Расчёт</Button>
                  {canManage && (!r.accrual || r.accrual.reversed) && (
                    <Button className="sm" disabled={accrue.isPending} onClick={() => accrue.mutate(r.staff_id)}>Начислить</Button>
                  )}
                </div>
              </TableCell>
            </TableRow>
          ))}
          {q.data && !rows.length && <TableRow><TableCell colSpan={10} className="muted">За месяц нет данных для начисления</TableCell></TableRow>}
        </TableBody>
      </Table>
      <p className="muted">
        Состав: фиксированный оклад + оплата за диалоги (сообщения и приёмка новых клиентов по динамической ставке)
        + премия 2,5 % с оплаченных заказов, оформленных при участии сотрудника, + 100 ₽ за подтверждённый отзыв на максимальную оценку.
        Штрафы уменьшают премию, но не ниже нуля: они уже проведены отдельными операциями в балансе.
        Начисление создаёт операции «Зарплата» и «Премия» за месяц; повторное начисление за тот же месяц невозможно.
      </p>
      {detail && <RowDetails row={detail} onClose={() => setDetail(null)} canManage={canManage} />}
      {settings && q.data && <SettingsDialog config={q.data.config} onClose={() => setSettings(false)} />}
      {q.data && <Reviews period={period} members={q.data.members} canManage={canManage} />}
    </>
  );
}