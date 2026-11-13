import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShiftLogBlock } from "../Shift";
import { api, auditLabel, auditTarget, AuditEntry, FinOp, fmtDate, fmtRub, json, Me, Payout, PAYOUT_STATUS, permLabel } from "../api";
import { SkeletonCards, SkeletonText, PageSkeleton, TableSkeletonRows } from "../Skeletons";
import { browserPermission, notificationsAvailable, requestBrowserPermission, useNotifyPrefs } from "../notify";
import { toast } from "../Toaster";

type CabinetData = { profile: Me; created_at: string | null; recent_actions: AuditEntry[] };
type MyFinance = {
  balance_kop: number; reserved_kop: number; available_kop: number; requisites: string; has_requisites: boolean;
  operations: FinOp[]; payouts: Payout[];
};
type MyThread = {
  id: number; unread: number; cover: string | null; client: { id: number; name: string; username: string | null };
  orders: { id: number; title: string | null; price: number | null; status: string; status_label: string }[];
};

function FinanceBlock() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["my-finance"], queryFn: () => api<MyFinance>("/api/me/finance") });
  const [req, setReq] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [comment, setComment] = useState("");
  const [err, setErr] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["my-finance"] });
  const onError = (e: Error) => setErr(e.message);
  const save = useMutation({
    mutationFn: () => api("/api/me/requisites", json("PUT", { requisites: req })),
    onSuccess: () => { setReq(null); setErr(""); refresh(); toast("Реквизиты сохранены"); }, onError,
  });
  const request = useMutation({
    mutationFn: () => api("/api/me/payouts", json("POST", { amount: amount.replace(",", "."), comment: comment || null })),
    onSuccess: () => { setAmount(""); setComment(""); setErr(""); refresh(); toast("Запрос на выплату отправлен"); }, onError,
  });
  const cancel = useMutation({
    mutationFn: (id: number) => api(`/api/me/payouts/${id}/cancel`, { method: "POST" }), onSuccess: () => { refresh(); toast("Запрос отменён"); }, onError,
  });
  if (!q.data) return q.error ? <p className="error">{(q.error as Error).message}</p> : (
    <>
      <h2>Деньги</h2>
      <SkeletonCards count={3} />
      <div className="cabinet-finance-forms">
        <Card className="card form"><SkeletonText lines={4} /></Card>
        <Card className="card form"><SkeletonText lines={4} /></Card>
      </div>
    </>
  );
  const d = q.data;
  const submit = (e: FormEvent) => { e.preventDefault(); setErr(""); request.mutate(); };
  return (
    <>
      <h2>Деньги</h2>
      <div className="cards money-cards">
        <Card className="card stat"><small>Баланс</small><b>{fmtRub(d.balance_kop)}</b></Card>
        <Card className="card stat"><small>Доступно к выводу</small><b>{fmtRub(d.available_kop)}</b></Card>
        <Card className="card stat"><small>В резерве</small><b>{fmtRub(d.reserved_kop)}</b></Card>
      </div>
      {err && <p className="error">{err}</p>}
      <div className="cabinet-finance-forms"><Card className="card form">
        <b>Реквизиты для выплат</b>
        {req === null ? (
          <>
            <div style={{ whiteSpace: "pre-wrap" }}>{d.requisites || <span className="muted">Не заполнены</span>}</div>
            <div><Button className="ghost sm" onClick={() => setReq(d.requisites)}>Изменить</Button></div>
          </>
        ) : (
          <>
            <Textarea rows={3} maxLength={500} value={req} onChange={(e) => setReq(e.target.value)} placeholder="Банк, номер карты или телефон (СБП), ФИО" />
            <div className="row-actions">
              <Button className="ghost" onClick={() => setReq(null)}>Отмена</Button>
              <Button disabled={save.isPending} onClick={() => save.mutate()}>Сохранить</Button>
            </div>
          </>
        )}
      </Card>
      <form className="card form" onSubmit={submit}>
        <b>Запросить выплату</b>
        {!d.has_requisites && <span className="muted">Сначала заполните реквизиты.</span>}
        <label>Сумма, ₽<Input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={`до ${fmtRub(Math.max(d.available_kop, 0))}`} /></label>
        <label>Комментарий<Input value={comment} maxLength={1000} onChange={(e) => setComment(e.target.value)} /></label>
        <div><Button disabled={!d.has_requisites || !amount.trim() || request.isPending}>Запросить</Button></div>
      </form></div>
      <h3>Мои запросы</h3>
      <Table>
        <TableHeader><TableRow><TableHead>№</TableHead><TableHead>Когда</TableHead><TableHead>Сумма</TableHead><TableHead>Статус</TableHead><TableHead></TableHead></TableRow></TableHeader>
        <TableBody>
          {d.payouts.map((p) => (
            <TableRow key={p.id}>
              <TableCell>{p.id}</TableCell><TableCell>{fmtDate(p.created_at)}</TableCell><TableCell>{fmtRub(p.amount_kop)}</TableCell>
              <TableCell className="wrap">
                <span className={`badge ${PAYOUT_STATUS[p.status]?.cls}`}>{PAYOUT_STATUS[p.status]?.label ?? p.status}</span>
                {p.decision_comment && <div className="muted">{p.decision_comment}</div>}
                {p.paid_reference && <div className="muted">Перевод: {p.paid_reference}</div>}
              </TableCell>
              <TableCell>{p.status === "requested" && <Button className="ghost" onClick={() => cancel.mutate(p.id)}>Отменить</Button>}</TableCell>
            </TableRow>
          ))}
          {!d.payouts.length && <TableRow><TableCell colSpan={5} className="muted">Запросов нет</TableCell></TableRow>}
        </TableBody>
      </Table>
      <h3>Журнал операций</h3>
      <Table>
        <TableHeader><TableRow><TableHead>Когда</TableHead><TableHead>Вид</TableHead><TableHead>Сумма</TableHead><TableHead>Комментарий</TableHead></TableRow></TableHeader>
        <TableBody>
          {d.operations.map((o) => (
            <TableRow key={o.id}>
              <TableCell>{fmtDate(o.created_at)}</TableCell>
              <TableCell>{o.kind_label}{o.period && <span className="muted"> · {o.period}</span>}</TableCell>
              <TableCell className={o.amount_kop < 0 ? "neg" : "pos"}>{o.amount_kop > 0 ? "+" : ""}{fmtRub(o.amount_kop)}</TableCell>
              <TableCell className="wrap">{o.comment ?? "—"}</TableCell>
            </TableRow>
          ))}
          {!d.operations.length && <TableRow><TableCell colSpan={4} className="muted">Операций пока нет</TableCell></TableRow>}
        </TableBody>
      </Table>
    </>
  );
}

function Threads() {
  const q = useQuery({ queryKey: ["my-threads"], queryFn: () => api<MyThread[]>("/api/me/threads") });
  if (!q.data) return (
    <>
      <h2>Мои ветки</h2>
      <Table>
        <TableHeader><TableRow><TableHead>Клиент</TableHead><TableHead>Обложка</TableHead><TableHead>Заказы</TableHead></TableRow></TableHeader>
        <TableBody><TableSkeletonRows rows={3} cols={3} /></TableBody>
      </Table>
    </>
  );
  return (
    <>
      <h2>Мои ветки</h2>
      <Table>
        <TableHeader><TableRow><TableHead>Клиент</TableHead><TableHead>Обложка</TableHead><TableHead>Заказы</TableHead></TableRow></TableHeader>
        <TableBody>
          {q.data.map((t) => (
            <TableRow key={t.id}>
              <TableCell><Link to={`/support/${t.id}`}>{t.client.name}</Link>{t.client.username && <span className="muted"> @{t.client.username}</span>}
                {t.unread > 0 && <span className="badge bad nav-badge">{t.unread}</span>}</TableCell>
              <TableCell>{t.cover ?? "—"}</TableCell>
              <TableCell className="wrap">{t.orders.map((o) => <div key={o.id}>#{o.id} {o.title ?? ""} · <span className="muted">{o.status_label}</span></div>)}{!t.orders.length && <span className="muted">—</span>}</TableCell>
            </TableRow>
          ))}
          {!q.data.length && <TableRow><TableCell colSpan={3} className="muted">Открытых веток нет</TableCell></TableRow>}
        </TableBody>
      </Table>
    </>
  );
}

function NotifySettings() {
  const [prefs, save] = useNotifyPrefs();
  const [permission, setPermission] = useState<NotificationPermission>(browserPermission);
  const available = notificationsAvailable();
  const enableBrowser = async () => {
    const result = await requestBrowserPermission();
    setPermission(result);
    if (result === "granted") { save({ ...prefs, browser: true }); toast("Уведомления браузера включены"); }
    else if (result === "denied") toast("Браузер запретил уведомления. Разрешите их в настройках сайта", { tone: "error" });
  };
  return (
    <Card className="card form" style={{ maxWidth: 640 }} data-guide="notify">
      <b>Уведомления</b>
      <div className="notify-row">
        <span>Звук при новом сообщении<small>Короткий сигнал, когда вкладка не на экране.</small></span>
        <Switch checked={prefs.sound} onCheckedChange={(value) => save({ ...prefs, sound: value })} aria-label="Звук новых сообщений" />
      </div>
      <div className="notify-row">
        <span>Уведомления браузера
          <small>{!available ? "Доступны только по HTTPS." : permission === "denied" ? "Запрещены в настройках этого сайта." : "Всплывающее окно, когда вкладка свёрнута."}</small>
        </span>
        {!available
          ? <span className="muted">Недоступно</span>
          : permission === "granted"
            ? <Switch checked={prefs.browser} onCheckedChange={(value) => save({ ...prefs, browser: value })} aria-label="Уведомления браузера" />
            : permission === "denied"
              ? <span className="muted">Запрещено</span>
              : <Button className="ghost sm" onClick={enableBrowser}>Разрешить</Button>}
      </div>
      <small className="muted">Счётчик непрочитанных всегда виден в заголовке вкладки.</small>
    </Card>
  );
}

export default function Cabinet({ me }: { me: Me }) {
  const q = useQuery({ queryKey: ["cabinet"], queryFn: () => api<CabinetData>("/api/me/cabinet") });
  if (q.error) return <p className="error">{(q.error as Error).message}</p>;
  if (!q.data) return (
    <>
      <h1>Кабинет</h1>
      <Card className="card form" style={{ maxWidth: 640 }}><SkeletonText lines={5} /></Card>
      <PageSkeleton rows={5} cols={3} title={false} />
    </>
  );
  const { profile: p, recent_actions } = q.data;
  return (
    <>
      <h1>Кабинет</h1>
      <Card className="card profile-card" style={{ maxWidth: 640 }}>
        <div className="profile-header">
          <div className="profile-avatar-wrap">
            <span className="profile-avatar">{p.display_name.slice(0, 2).toUpperCase()}</span>
            <img src="/cat.png" alt="Кот Тайби" className="profile-cat-badge" />
          </div>
          <div className="profile-info">
            <div className="profile-name-row">
              <span className="profile-display-name">{p.display_name}</span>
              {p.username && <span className="muted">@{p.username}</span>}
              <span className="badge ok">{p.role_label}</span>
            </div>
            <div className="profile-sub">
              {p.position && <span>{p.position} · </span>}
              <span className="muted">ID: {p.telegram_id} · с нами с {fmtDate(q.data.created_at)}</span>
            </div>
          </div>
        </div>
        <div style={{ marginTop: 14 }}>
          <div className="muted" style={{ marginBottom: 6, fontSize: 13, fontWeight: 500 }}>Права доступа</div>
          <div className="perms">{p.permissions.map((x) => <span key={x} title={x}>{permLabel(x)}</span>)}</div>
        </div>
      </Card>
      {me.permissions.includes("support.chat") && <NotifySettings />}
      <FinanceBlock />
      {me.permissions.includes("support.chat") && <Threads />}
      {me.permissions.includes("support.chat") && <ShiftLogBlock title="Мои смены" staffId={me.telegram_id} />}
      <h2>Мои последние действия</h2>
      <Table>
        <TableHeader><TableRow><TableHead>Когда</TableHead><TableHead>Действие</TableHead><TableHead>Объект</TableHead></TableRow></TableHeader>
        <TableBody>
          {recent_actions.map((a) => (
            <TableRow key={a.id}><TableCell>{fmtDate(a.created_at)}</TableCell><TableCell title={a.action}>{auditLabel(a.action, a.details)}</TableCell><TableCell title={a.target ?? undefined}>{auditTarget(a)}</TableCell></TableRow>
          ))}
          {!recent_actions.length && <TableRow><TableCell colSpan={3} className="muted">Пока ничего</TableCell></TableRow>}
        </TableBody>
      </Table>
    </>
  );
}
