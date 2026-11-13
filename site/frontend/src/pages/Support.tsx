import { Dialog, DialogClose, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { CustomSelect } from "../Controls";
import { CSSProperties, DragEvent, FormEvent, KeyboardEvent, PointerEvent, ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useParams } from "react-router-dom";
import { InfiniteData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, fmtDate, json, Me } from "../api";
import { subscribeTopic, useRealtimeState } from "../realtime";
import { ShiftRequiredDialog, useSupportSummary } from "../Shift";
import { ChatSkeleton, SupportListSkeleton } from "../Skeletons";
import { useSupportDraft, newRequestKey } from "../supportDraft";
import type { SupportTemplate } from "./SupportTemplates";
import { Icon } from "../icons";
import { toast, toastError } from "../Toaster";
import { TybeeEmptyState } from "../TybeeLogo";

type Row = {
  id: number; status: string; channel: string; channel_label: string | null; cover: string | null; unread: number;
  route_key: string; needs_reply: boolean; waiting_since: string | null; handoff_note: string | null; next_step: string | null; due_at: string | null;
  assigned_to: number | null; assigned_name: string | null; last_message: string; last_message_at: string | null;
  client: { id: number; name: string; username: string | null; is_banned: boolean; source?: string | null };
  client_orders: ClientOrders;
};
// Hover preview in the thread list: the client's newest order in full plus the orders before it.
type ClientOrderBrief = {
  id: number; title: string | null; status: string; status_label: string;
  price: number | null; created_at: string | null;
};
type ClientOrder = ClientOrderBrief & {
  article: string | null; type: string; type_label: string; description: string | null;
  updated_at: string | null; last_ping_at: string | null;
  operations: { paid_amount: number | null; paid_at: string | null; refund_amount: number | null; refunded_at: string | null } | null;
};
type ClientOrders = { total: number; last: ClientOrder | null; others: ClientOrderBrief[] };
type OrderRow = {
  id: number; type: string; status: string; title: string | null; price: number | null;
  created_at: string | null;
  next_ping_at: string | null; allowed_transitions: { code: string; label: string }[];
  last_ping_at: string | null; can_ping: boolean;
  operations: { paid_amount: number | null; paid_at: string | null; refund_amount: number | null; refunded_at: string | null } | null;
};
type Detail = Row & {
  covers: string[]; orders: OrderRow[]; statuses: { code: string; label: string }[]; labels: Record<string, string>;
  can_reply: boolean; can_refund: boolean;
};
type Stale = {
  order_id: number; title: string | null; price: number | null; status: string; status_label: string;
  updated_at: string | null; last_ping_at: string | null; thread_id: number; client: string; can_ping: boolean;
};
type Attachment = { id: number; kind: string; mime: string; name: string; size: number; is_image: boolean; url: string; ephemeral: boolean; available: boolean; direction: "in" | "out" };
type Msg = {
  id: number; direction: "client" | "staff" | "system" | "internal"; kind: string; body: string; staff_name: string | null;
  created_at: string | null; delivery: "pending" | "sent" | "failed" | "uncertain" | null; can_retry: boolean; error: string | null;
  attachments: Attachment[]; channel_label: string | null; cover: string | null;
};

const fmtSize = (n: number) =>
  n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} МБ` : n >= 1024 ? `${Math.round(n / 1024)} КБ` : `${n} Б`;
const ACCEPT = "image/jpeg,image/png,image/webp,application/pdf";

function stabilizeSafariComposer(input: HTMLTextAreaElement) {
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (!ios || /CriOS|FxiOS|EdgiOS|OPiOS/.test(navigator.userAgent)) return;
  // Keep native focus and keyboard activation. During Safari's focus event,
  // suppress its visible-input centring heuristic without changing geometry.
  const opacity = input.style.opacity;
  input.style.opacity = "0";
  requestAnimationFrame(() => requestAnimationFrame(() => { input.style.opacity = opacity; }));
}

function AttachmentView({ a }: { a: Attachment }) {
  const [open, setOpen] = useState(false);
  const [blobUrl, setBlobUrl] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => () => { if (blobUrl) URL.revokeObjectURL(blobUrl); }, [blobUrl]);
  const download = (url: string) => {
    const link = document.createElement("a"); link.href = url; link.download = a.name || "file"; link.click();
  };
  const show = async (action: "view" | "download") => {
    if (blobUrl) { if (a.is_image && action === "view") setOpen(true); else download(blobUrl); return; }
    setLoading(true); setError("");
    try {
      const response = await fetch(a.url, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "Не удалось загрузить файл");
      }
      const url = URL.createObjectURL(await response.blob());
      setBlobUrl(url);
      if (a.is_image && action === "view") setOpen(true);
      else download(url);
    } catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  };
  return (
    <div className="att-file">
      <Icon name="clip" /><span>{a.name || "файл"}</span>
      {a.size > 0 && <small className="muted">{fmtSize(a.size)}</small>}
      {(a.available || blobUrl) ? <div className="att-actions">
        {a.is_image && <Button type="button" className="ghost" disabled={loading} onClick={() => show("view")}>{loading ? "Загрузка…" : "Посмотреть"}</Button>}
        <Button type="button" className="ghost" disabled={loading} onClick={() => show("download")}>{loading && !a.is_image ? "Загрузка…" : "Скачать"}</Button>
      </div> : <small className="muted">Файл недоступен</small>}
      {error && <small className="error">{error}</small>}
      {a.is_image && blobUrl && <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="app-dialog image-viewer" aria-describedby={undefined} showCloseButton={false}>
          <DialogTitle className="sr-only">{a.name || "Фотография"}</DialogTitle>
          <img src={blobUrl} alt={a.name || "Фотография"} />
          <DialogClose className="image-viewer-close" aria-label="Закрыть изображение"><span aria-hidden="true">×</span></DialogClose>
        </DialogContent>
      </Dialog>}
    </div>
  );
}
type Agent = { telegram_id: number; display_name: string; on_shift: boolean; open: number };

const DELIVERY = { pending: "ожидает отправки…", sent: "✓ отправлено в канал", failed: "не отправлено", uncertain: "результат неизвестен" } as const;
const messageTime = (value: string | null) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) + " · " + date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
};
const waitingLabel = (at: string | null) => {
  if (!at) return "Нужен ответ";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(at).getTime()) / 1000));
  if (seconds < 60) return `Ждёт ответа ${seconds} с`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `Ждёт ответа ${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `Ждёт ответа ${hours} ч${minutes % 60 ? ` ${minutes % 60} мин` : ""}`;
  const days = Math.floor(hours / 24);
  return `Ждёт ответа ${days} дн${hours % 24 ? ` ${hours % 24} ч` : ""}`;
};
// Order prices are stored in rubles (not kopecks like the finance ledger).
const rub = (value: number) => `${value.toLocaleString("ru-RU")} ₽`;
const ordersWord = (n: number) => {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "заказ";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "заказа";
  return "заказов";
};
const orderTone = (status: string) =>
  ["rejected", "cancelled", "refund_pending", "refunded"].includes(status) ? "bad"
    : ["new", "awaiting_payment", "payment_review", "in_dialog"].includes(status) ? "wait" : "ok";

// One confirmation text per support action, so the toast never says "Готово".
const DONE: [RegExp, string][] = [
  [/\/claim$/, "Диалог взят в работу"],
  [/\/close$/, "Диалог закрыт"],
  [/\/transfer$/, "Диалог передан"],
  [/\/notes$/, "Заметка сохранена"],
  [/\/retry$/, "Сообщение отправлено повторно"],
  [/\/invoice$/, "Счёт выставлен"],
  [/\/status$/, "Статус заказа обновлён"],
];
const doneText = (path: string) => DONE.find(([pattern]) => pattern.test(path))?.[1] ?? "Готово";
type ThreadPage = { items: Row[]; next_cursor: string | null; total: number };

type ClientHit = {
  id: number; name: string; username: string | null; is_banned: boolean;
  last_seen_at: string | null; covers: string[]; orders_count: number;
  thread: { id: number; status: string; assigned_to: number | null; assigned_name: string | null; needs_reply: boolean } | null;
};

const threadLabel = (t: ClientHit["thread"]) =>
  !t ? "нет диалога"
    : t.assigned_to ? `ведёт ${t.assigned_name ?? "сотрудник"}`
      : t.status === "closed" ? "закрыт" : "в очереди";

const pickDialogFile = (picked: File, setFile: (f: File | null) => void, setErr: (v: string) => void) => {
  if (!ACCEPT.split(",").includes(picked.type)) {
    setErr("Можно прикрепить JPEG, PNG, WebP или PDF");
    return;
  }
  const limit = picked.type === "application/pdf" ? 20 * 1024 * 1024 : 10 * 1024 * 1024;
  if (picked.size > limit) {
    setErr(`Файл больше ${Math.round(limit / 1024 / 1024)} МБ`);
    return;
  }
  setErr("");
  setFile(picked);
};

/** Search any client who has a live bot, then send the first message of a new dialog. */
function StartDialogModal({ me, onClose, onOpen }: { me: Me; onClose: () => void; onOpen: (threadId: number) => void }) {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [picked, setPicked] = useState<ClientHit | null>(null);
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState("");
  const [shiftOpen, setShiftOpen] = useState(false);
  const requestKey = useRef(newRequestKey());
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => setTerm(q.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [q]);
  const results = useQuery({
    queryKey: ["support-clients", term],
    queryFn: () => api<{ items: ClientHit[]; total: number }>(`/api/support/clients?q=${encodeURIComponent(term)}`),
    enabled: term.length >= 2,
    staleTime: 15_000,
    placeholderData: (previous) => previous,
  });
  // The summary is already polled by the sidebar; reuse the cache to ask for a
  // shift before uploading a file that the server would refuse anyway.
  const summary = useSupportSummary(true);
  const send = useMutation({
    mutationFn: async () => {
      if (!picked) throw new Error("Выберите клиента");
      const form = new FormData();
      form.append("text", text);
      if (file) form.append("file", file);
      return api<{ thread_id: number; message_id: number }>(`/api/support/clients/${picked.id}/dialog`, {
        method: "POST", body: form, headers: { "Idempotency-Key": requestKey.current },
      });
    },
    onSuccess: (data) => { toast("Диалог начат"); onOpen(data.thread_id); },
    onError: (e: Error) => {
      if (e instanceof ApiError && e.reason === "shift_required") { setShiftOpen(true); return; }
      setErr(e.message);
    },
  });
  const ownedByOther = !!picked?.thread?.assigned_to && picked.thread.assigned_to !== me.telegram_id;
  const canSend = !!picked && !picked.is_banned && !ownedByOther && !send.isPending && (!!text.trim() || !!file);
  const submit = () => {
    if (!canSend) return;
    if (summary.data && !summary.data.on_shift) { setShiftOpen(true); return; }
    setErr("");
    send.mutate();
  };
  return (
    <>
      <Dialog open onOpenChange={(open) => { if (!open && !send.isPending) onClose(); }}>
        <DialogContent className="app-dialog sup-start" aria-describedby={undefined}>
          <DialogTitle>Написать клиенту</DialogTitle>
          {!picked ? (
            <>
              <Input type="search" autoFocus placeholder="Имя, @username, ID или #заказ" value={q} onChange={(e) => setQ(e.target.value)} maxLength={120} />
              <small className="muted">Минимум 2 символа. Ищем только клиентов, которые открывали бота.</small>
              {results.error && <p className="error">{results.error.message}</p>}
              {results.isFetching && <small className="muted">Ищем…</small>}
              {term.length >= 2 && !results.isFetching && results.data?.items.length === 0 && (
                <p className="muted">Клиент не найден. Проверьте имя, @username, ID или номер заказа.</p>
              )}
              <div className="sup-start-results">
                {results.data?.items.map((c) => (
                  <button type="button" key={c.id} className="sup-start-hit" onClick={() => { setPicked(c); setErr(""); }}>
                    <span className="sup-start-avatar" aria-hidden="true">{c.name.trim().slice(0, 1).toUpperCase()}</span>
                    <span className="sup-start-who">
                      <b>{c.name}</b>
                      {c.username && <small>@{c.username}</small>}
                    </span>
                    <span className="sup-start-meta">
                      {threadLabel(c.thread)}
                      {c.orders_count > 0 && ` · ${c.orders_count} ${ordersWord(c.orders_count)}`}
                    </span>
                    {c.is_banned && <span className="badge bad">заблокирован</span>}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <form className="form" onSubmit={(e) => { e.preventDefault(); submit(); }}>
              <div className="sup-start-picked">
                <div><b>{picked.name}</b>{picked.username && <span className="muted"> @{picked.username}</span>}</div>
                <small className="muted">
                  ID {picked.id}
                  {picked.last_seen_at && ` · был(а) в боте ${fmtDate(picked.last_seen_at)}`}
                  {picked.orders_count > 0 && ` · ${picked.orders_count} ${ordersWord(picked.orders_count)}`}
                </small>
                <Button type="button" className="link" onClick={() => { setPicked(null); setErr(""); }}>Другой клиент</Button>
              </div>
              {ownedByOther ? (
                <p className="error">Диалог уже ведёт {picked.thread?.assigned_name ?? "другой сотрудник"}. Перехватывать чужую работу нельзя.</p>
              ) : (
                <>
                  {picked.is_banned && <p className="error">Клиент заблокирован: сначала разблокируйте его в диалоге.</p>}
                  <label>Сообщение
                    <Textarea rows={4} maxLength={3500} autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="Первое сообщение клиенту" />
                  </label>
                  <input ref={fileInput} type="file" hidden accept={ACCEPT} onChange={(e) => {
                    const chosen = e.target.files?.[0] ?? null;
                    e.currentTarget.value = "";
                    if (chosen) pickDialogFile(chosen, setFile, setErr);
                  }} />
                  <div className="row-actions sup-start-file">
                    <Button type="button" className="ghost" onClick={() => fileInput.current?.click()}><Icon name="clip" /> Файл</Button>
                    {file && <span className="muted">{file.name} · {fmtSize(file.size)} <Button type="button" className="link" onClick={() => setFile(null)}>Убрать</Button></span>}
                  </div>
                </>
              )}
              {err && <p className="error">{err}</p>}
              <div className="row-actions">
                <Button type="button" className="ghost" disabled={send.isPending} onClick={onClose}>Отмена</Button>
                {ownedByOther
                  ? <Button type="button" onClick={() => onOpen(picked.thread!.id)}>Открыть диалог</Button>
                  : <Button type="submit" disabled={!canSend}>{send.isPending ? "Отправляем…" : "Отправить"}</Button>}
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>
      {shiftOpen && (
        <ShiftRequiredDialog
          open action="написать клиенту"
          onClose={() => setShiftOpen(false)}
          onStarted={() => { setShiftOpen(false); send.mutate(); }}
        />
      )}
    </>
  );
}


function StaleOrders() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { connected: live } = useRealtimeState();
  const [days, setDays] = useState(3);
  const list = useQuery({
    queryKey: ["support-stale", days],
    queryFn: () => api<Stale[]>(`/api/support/stale-orders?days=${days}`),
    refetchInterval: live ? 60_000 : 30_000,
  });
  const ping = useMutation({
    mutationFn: (id: number) => api(`/api/support/orders/${id}/ping`, { method: "POST" }),
    onSuccess: (_, id) => { toast(`Напоминание по заказу #${id} отправлено`); qc.invalidateQueries({ queryKey: ["support-stale"] }); },
    onError: (e: Error) => toastError(e.message),
  });
  return (
    <>
      <label className="muted">Без изменений дольше{" "}
        <CustomSelect value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[1, 3, 7, 14, 30].map((d) => <option key={d} value={d}>{d} дн.</option>)}
        </CustomSelect>
      </label>
      {list.isLoading && <SupportListSkeleton rows={3} />}
      {list.data?.length === 0 && <p className="muted">Зависших заказов нет</p>}
      {list.data?.map((o) => (
        <div key={o.order_id} className="sup-row" onClick={() => nav(`/support/${o.thread_id}`)}>
          <div className="sup-row-head"><b>#{o.order_id}</b><small className="muted">{o.status_label}</small></div>
          <div className="sup-last">{o.client} · {o.title ?? "—"}{o.price != null && ` · ${o.price} ₽`}</div>
          <small className="muted">изменён {fmtDate(o.updated_at)}{o.last_ping_at && ` · напоминали ${fmtDate(o.last_ping_at)}`}</small>
          {o.can_ping && (
            <Button className="ghost" disabled={ping.isPending} onClick={(e) => { e.stopPropagation(); ping.mutate(o.order_id); }}>🔔 Напомнить</Button>
          )}
        </div>
      ))}
    </>
  );
}

function ChatPeek({ row, top, left }: { row: Row; top: number; left: number }) {
  const orders = row.client_orders;
  const last = orders?.last ?? null;
  const others = orders?.others ?? [];
  const hidden = Math.max(0, (orders?.total ?? 0) - 1 - others.length);
  const box = useRef<HTMLDivElement>(null);
  // Keep the card inside the viewport: measure it once and pull it up if it hangs off the bottom.
  const [shift, setShift] = useState(0);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const overflow = rect.bottom - (window.innerHeight - 12);
    setShift(overflow > 0 ? Math.max(-rect.top + 8, -overflow) : 0);
  }, [row.id]);
  const line = (label: string, value: ReactNode) => (
    <div className="sup-peek-line"><span>{label}</span><span>{value}</span></div>
  );
  return (
    <div ref={box} className="sup-peek" style={{ top, left, transform: `translateY(${shift}px)` }} aria-hidden="true">
      <div className="sup-peek-head">
        <b>{row.client.name}</b>
        <small className="muted">{row.channel === "avito" ? row.channel_label ?? "Avito" : row.cover ?? "Telegram"}</small>
      </div>
      {last ? (
        <>
          <div className="sup-peek-label">Последний заказ</div>
          <div className="sup-peek-title"><b>#{last.id}</b>{last.title ? ` · ${last.title}` : " · без названия"}</div>
          <div className="sup-peek-meta">
            <span className={`peek-status ${orderTone(last.status)}`}>{last.status_label}</span>
            {last.price != null && <span className="sup-peek-price">{rub(last.price)}</span>}
          </div>
          <div className="sup-peek-lines">
            {last.article && line("Артикул", last.article)}
            {line("Тип", last.type_label)}
            {last.created_at && line("Создан", fmtDate(last.created_at))}
            {last.updated_at && line("Обновлён", fmtDate(last.updated_at))}
            {last.operations && line("Оплата", last.operations.paid_at
              ? `${rub(last.operations.paid_amount ?? 0)} · ${fmtDate(last.operations.paid_at)}`
              : "не подтверждена")}
            {last.operations?.refunded_at && line("Возврат", `${rub(last.operations.refund_amount ?? 0)} · ${fmtDate(last.operations.refunded_at)}`)}
            {last.last_ping_at && line("Напоминание", fmtDate(last.last_ping_at))}
          </div>
          {last.description && (
            <>
              <div className="sup-peek-label sup-peek-desc-label">Описание</div>
              <div className="sup-peek-desc">{last.description}</div>
            </>
          )}
        </>
      ) : <div className="sup-peek-more">Заказов ещё нет</div>}
      {others.length > 0 && (
        <div className="sup-peek-others">
          <div className="sup-peek-label">Ещё заказы <span className="sup-peek-total">всего {orders.total}</span></div>
          {others.map((order) => (
            <div key={order.id} className="sup-peek-other">
              <div className="sup-peek-other-title"><b>#{order.id}</b> {order.title || "без названия"}</div>
              <small className={`tone-${orderTone(order.status)}`}>
                {order.status_label}{order.price != null ? ` · ${rub(order.price)}` : ""}
              </small>
            </div>
          ))}
          {hidden > 0 && <div className="muted">и ещё {hidden} {ordersWord(hidden)}</div>}
        </div>
      )}
      {row.needs_reply && <div className="sup-peek-wait">{waitingLabel(row.waiting_since)}</div>}
    </div>
  );
}

function List({ me, current }: { me: Me; current?: number }) {
  const nav = useNavigate();
  const { connected: live } = useRealtimeState();
  const canAll = me.permissions.includes("support.view_all");
  // Desktop hover preview: show the client's last order next to the row after a short pause.
  const [peek, setPeek] = useState<{ row: Row; top: number; left: number } | null>(null);
  const peekTimer = useRef<number | null>(null);
  const canHover = useRef(false);
  useEffect(() => {
    canHover.current = window.matchMedia?.("(hover: hover) and (pointer: fine)").matches ?? false;
    return () => { if (peekTimer.current) window.clearTimeout(peekTimer.current); };
  }, []);
  const hidePeek = () => {
    if (peekTimer.current) { window.clearTimeout(peekTimer.current); peekTimer.current = null; }
    setPeek(null);
  };
  const showPeek = (row: Row, node: HTMLElement) => {
    if (!canHover.current) return;
    if (peekTimer.current) window.clearTimeout(peekTimer.current);
    peekTimer.current = window.setTimeout(() => {
      const box = node.getBoundingClientRect();
      const width = 300;
      const left = Math.min(box.right + 10, window.innerWidth - width - 12);
      const top = Math.min(Math.max(8, box.top - 6), window.innerHeight - 210);
      setPeek({ row, top, left });
    }, 200);
  };
  const [scope, setScope] = useState<"mine" | "queue" | "all" | "stale" | "waiting">("waiting");
  const [q, setQ] = useState("");
  const [startOpen, setStartOpen] = useState(false);
  const qc = useQueryClient();
  const list = useInfiniteQuery({
    queryKey: ["support-threads", scope, q],
    initialPageParam: "",
    queryFn: ({ pageParam }) => api<ThreadPage>(`/api/support/threads?paginated=true&scope=${scope}&q=${encodeURIComponent(q)}&cursor=${encodeURIComponent(pageParam)}`),
    getNextPageParam: (page) => page.next_cursor || undefined,
    refetchInterval: live ? 60_000 : 15_000,
    enabled: scope !== "stale",
  });
  const rows = [...new Map((list.data?.pages.flatMap((p) => p.items) ?? []).map((t) => [t.id, t])).values()];
  const tabs = (
    <><div className="sup-list-heading" data-guide="support-list"><div><h1>Обращения</h1><p>Диалоги с клиентами</p></div><Button data-guide="start-dialog" onClick={() => setStartOpen(true)}>Написать клиенту</Button></div><Tabs value={scope} onValueChange={(value) => setScope(value as typeof scope)}><TabsList className="app-tabs"><TabsTrigger value="waiting">Нужен ответ</TabsTrigger><TabsTrigger value="mine">Мои</TabsTrigger><TabsTrigger value="queue">Очередь</TabsTrigger>{canAll && <TabsTrigger value="all">Все</TabsTrigger>}<TabsTrigger value="stale">Зависшие заказы</TabsTrigger></TabsList></Tabs>
      {startOpen && <StartDialogModal me={me} onClose={() => setStartOpen(false)} onOpen={(threadId) => {
        qc.invalidateQueries({ queryKey: ["support-threads"] });
        qc.invalidateQueries({ queryKey: ["support-summary"] });
        qc.invalidateQueries({ queryKey: ["support-thread", threadId] });
        setStartOpen(false);
        nav(`/support/${threadId}`);
      }} />}</>
  );
  if (scope === "stale") return <div className="sup-list">{tabs}<StaleOrders /></div>;
  return (
    <div className="sup-list" onScroll={hidePeek}>
      {tabs}
      <Input type="search" placeholder="Поиск клиента" value={q} onChange={(e) => setQ(e.target.value)} />
      {list.error && <p className="error">{list.error.message}</p>}
      {list.isLoading && <SupportListSkeleton rows={6} />}
      {list.data && list.data.pages[0].total > 0 && <small className="muted">Показано {rows.length} из {list.data.pages[0].total}</small>}
      {!list.isLoading && rows.length === 0 && <p className="muted">Обращений нет</p>}
      {rows.map((t) => (
        <div key={t.id} className={`sup-row ${t.id === current ? "active" : ""} ${t.status}`}
          onPointerEnter={(e) => { if (e.pointerType === "mouse") showPeek(t, e.currentTarget); }} onPointerLeave={hidePeek}
          onClick={() => { hidePeek(); nav(`/support/${t.id}`); }}>
          <div className="sup-row-avatar" aria-hidden="true">{t.client.name.trim().slice(0, 1).toUpperCase()}</div>
          <div className="sup-row-content">
          <div className="sup-row-head">
            <b>{t.client.name}</b>
            {t.unread > 0 && <span className="badge bad">{t.unread}</span>}
          </div>
          <small className="muted">
            {t.channel === "avito" ? t.channel_label ?? "Avito" : t.cover ?? "—"}
            {scope === "all" && ` · ${t.assigned_name ?? "в очереди"}`}
            {t.status === "closed" && " · закрыта"}
          </small>
          <div className="sup-last">{t.last_message}</div>
          <small className={t.needs_reply ? "sup-waiting" : "muted"}>{t.needs_reply ? waitingLabel(t.waiting_since) : fmtDate(t.last_message_at)}</small>
          {t.next_step && <small className="muted">Следующий шаг: {t.next_step}{t.due_at && ` · ${fmtDate(t.due_at)}`}</small>}
          </div>
        </div>
      ))}
      {list.hasNextPage && <Button className="ghost" disabled={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>{list.isFetchingNextPage ? "Загрузка…" : "Загрузить ещё обращения"}</Button>}
      {peek && createPortal(<ChatPeek row={peek.row} top={peek.top} left={peek.left} />, document.body)}
    </div>
  );
}

function Chat({ me, id }: { me: Me; id: number }) {
  const qc = useQueryClient();
  const { connected: live } = useRealtimeState();
  const detail = useQuery({ queryKey: ["support-thread", id], queryFn: () => api<Detail>(`/api/support/threads/${id}`), refetchInterval: live ? 60_000 : 15_000, placeholderData: (previous) => previous });
  const [mode, setMode] = useState<"reply" | "note">("reply");
  // Drag & drop of a file onto the chat panel.
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const replyDraft = useSupportDraft(me.telegram_id, id, "reply", detail.data?.route_key ?? "loading");
  const noteDraft = useSupportDraft(me.telegram_id, id, "note", detail.data?.route_key ?? "loading");
  const draft = mode === "reply" ? replyDraft : noteDraft;
  const { text, setText, file } = draft;
  const { nextStep, setNextStep, dueAt, setDueAt } = noteDraft;
  const [selectedOrder, setSelectedOrder] = useState("");
  const [noteTaskOpen, setNoteTaskOpen] = useState(false);
  const [templateOpen, setTemplateOpen] = useState(false);
  const [templateDismissed, setTemplateDismissed] = useState(false);
  const [templateSearch, setTemplateSearch] = useState("");
  const [messageSearch, setMessageSearch] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [jumping, setJumping] = useState(false);
  const [highlightedMessage, setHighlightedMessage] = useState<number | null>(null);
  const pendingJump = useRef<number | null>(null);
  const searchResults = useInfiniteQuery({
    queryKey: ["support-message-search", id, messageSearch], initialPageParam: 0,
    queryFn: ({ pageParam }) => api<Msg[]>(`/api/support/threads/${id}/messages?q=${encodeURIComponent(messageSearch)}${pageParam ? `&before_id=${pageParam}` : ""}`),
    getNextPageParam: (page) => page.length === 200 ? page[0].id : undefined,
    enabled: searchOpen && !!messageSearch.trim(),
  });
  const templates = useQuery({ queryKey: ["support-templates", "active"], queryFn: () => api<SupportTemplate[]>("/api/support/templates") });
  const historyScroll = useRef<{ height: number; top: number } | null>(null);
  const [preview, setPreview] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState("");
  const [shiftPrompt, setShiftPrompt] = useState<null | { action: string; retry: () => void }>(null);
  const [statusChange, setStatusChange] = useState<{ order: OrderRow; target: string } | null>(null);
  const [refundOrder, setRefundOrder] = useState<OrderRow | null>(null);
  const [modal, setModal] = useState<"" | "transfer" | "invoice">("");
  const invoiceKey = useRef(newRequestKey());
  const [clientOpen, setClientOpen] = useState(false);
  const [orderSearch, setOrderSearch] = useState("");
  const clientTitle = useRef<HTMLDivElement>(null);
  const [showLatest, setShowLatest] = useState(false);
  const [headerExpanded, setHeaderExpanded] = useState(false);
  const [composerExpanded, setComposerExpanded] = useState(false);
  const followLatest = useRef(true);
  const touchingMessages = useRef(false);
  const messagesBox = useRef<HTMLDivElement>(null);
  const messagesContent = useRef<HTMLDivElement>(null);
  const finishMessageTouch = () => {
    touchingMessages.current = false;
    const box = messagesBox.current;
    if (box) followLatest.current = box.scrollHeight - box.scrollTop - box.clientHeight <= 48;
  };
  const animatedMessages = useRef<{ thread: number; lastId: number } | null>(null);
  const scrollToLatest = () => {
    const box = messagesBox.current;
    followLatest.current = true;
    historyScroll.current = null;
    pendingJump.current = null;
    if (box) {
      const bottom = Math.max(0, box.scrollHeight - box.clientHeight);
      if (Math.abs(box.scrollTop - bottom) > 1) box.scrollTop = bottom;
    }
    setShowLatest(false);
  };
  const chatBox = useRef<HTMLDivElement>(null);
  const history = useInfiniteQuery({
    queryKey: ["support-msgs", id], initialPageParam: 0,
    queryFn: ({ pageParam }) => api<Msg[]>(`/api/support/threads/${id}/messages${pageParam ? `?before_id=${pageParam}` : ""}`),
    getNextPageParam: (page) => page.length === 200 ? page[0].id : undefined,
    refetchInterval: (query) => query.state.data?.pages[0]?.some((m) => m.delivery === "pending") ? 2000 : false,
    // The open history is refreshed by appending (pullNew), never by refetching.
    staleTime: Infinity,
  });
  const messageCache = useRef<{ source: unknown; value: Msg[] }>({ source: null, value: [] });
  if (messageCache.current.source !== history.data) {
    messageCache.current = { source: history.data, value: [...new Map([...(history.data?.pages ?? [])].reverse().flat().map((m) => [m.id, m])).values()].sort((a,b) => a.id-b.id) };
  }
  const msgs = { data: history.data ? messageCache.current.value : undefined, isPlaceholderData: false };
  // New messages are appended by id; the history is never re-downloaded on a push.
  const lastMsgId = useRef(0);
  lastMsgId.current = msgs.data?.[msgs.data.length - 1]?.id ?? lastMsgId.current;
  const pullNew = useCallback(async () => {
    const after = lastMsgId.current;
    if (!after) return;
    try {
      const fresh = await api<Msg[]>(`/api/support/threads/${id}/messages?after_id=${after}`);
      if (!fresh.length) return;
      qc.setQueryData<InfiniteData<Msg[]>>(["support-msgs", id], (old) => {
        if (!old?.pages.length) return old;
        const newest = old.pages[0];
        const known = new Set(newest.map((m) => m.id));
        const added = fresh.filter((m) => !known.has(m.id));
        if (!added.length) return old;
        return { ...old, pages: [[...newest, ...added].sort((a, b) => a.id - b.id), ...old.pages.slice(1)] };
      });
    } catch { /* the next push or the fallback poll retries */ }
  }, [id, qc]);
  useEffect(() => subscribeTopic((topic) => { if (topic === "support") void pullNew(); }), [pullNew]);
  useEffect(() => {
    const timer = window.setInterval(() => void pullNew(), live ? 120_000 : 20_000);
    const onVisible = () => { if (document.visibilityState === "visible") void pullNew(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [pullNew, live]);
  const switching = detail.isPlaceholderData || msgs.isPlaceholderData || !detail.data || (!msgs.data && !history.isError);
  useEffect(() => { if (chatBox.current) chatBox.current.inert = switching; }, [switching]);
  useLayoutEffect(() => {
    followLatest.current = true;
    setShowLatest(false);
    setClientOpen(false);
    setOrderSearch("");
  }, [id]);
  useLayoutEffect(() => {
    if (pendingJump.current && messagesContent.current) {
      const found = messagesContent.current.querySelector(`[data-message-id="${pendingJump.current}"]`);
      if (found && messagesBox.current) { const box = messagesBox.current; box.scrollTop += found.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientHeight / 2 + found.getBoundingClientRect().height / 2; setHighlightedMessage(pendingJump.current); pendingJump.current = null; return; }
    }
    if (historyScroll.current && messagesBox.current && !history.isFetchingNextPage) {
      const previous = historyScroll.current;
      messagesBox.current.scrollTop = previous.top + messagesBox.current.scrollHeight - previous.height;
      historyScroll.current = null;
    } else if (!switching && followLatest.current) scrollToLatest();
  }, [msgs.data, switching]);
  useLayoutEffect(() => {
    if (switching || !msgs.data) return;
    const previous = animatedMessages.current;
    const lastId = msgs.data[msgs.data.length - 1]?.id ?? 0;
    animatedMessages.current = { thread: id, lastId };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const timing = { duration: 260, easing: "cubic-bezier(.22,1,.36,1)" };
    if (!previous || previous.thread !== id) {
      chatBox.current?.querySelector(".sup-main")?.animate(
        [{ opacity: .5, transform: "translateY(6px)" }, { opacity: 1, transform: "translateY(0)" }], timing,
      );
      return;
    }
    // Animate only newly appended messages, never the history on a refresh.
    for (const message of msgs.data) {
      if (message.id <= previous.lastId) continue;
      const node = messagesContent.current?.querySelector(`[data-message-id="${message.id}"]`);
      node?.animate([
        { opacity: 0, transform: `translate(${message.direction === "staff" ? 8 : -8}px, 8px)` },
        { opacity: 1, transform: "translate(0, 0)" },
      ], timing);
    }
  }, [id, msgs.data, switching]);
  useEffect(() => {
    const box = messagesBox.current;
    const content = messagesContent.current;
    if (!box || !content || switching) return;
    // Images and layout changes can move the bottom after messages have rendered.
    const observer = new ResizeObserver(() => {
      if (touchingMessages.current) return;
      if (followLatest.current) scrollToLatest();
      else setShowLatest(box.scrollHeight - box.scrollTop - box.clientHeight > 2);
    });
    observer.observe(content);
    observer.observe(box);
    return () => observer.disconnect();
  }, [switching]);

  useEffect(() => {
    const box = messagesBox.current;
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent)
      || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    if (!box || switching || !ios) return;
    let active = false;
    let moved = false;
    let y = 0;
    let time = 0;
    let velocity = 0;
    let frame = 0;
    const cancelMomentum = () => { cancelAnimationFrame(frame); frame = 0; };
    const start = (event: TouchEvent) => {
      cancelMomentum();
      active = event.touches.length === 1
        && window.matchMedia("(max-width: 1000px)").matches
        && !!document.activeElement?.matches("input, textarea, [contenteditable='true']");
      moved = false;
      velocity = 0;
      if (!active) return;
      y = event.touches[0].clientY;
      time = performance.now();
      followLatest.current = false;
    };
    const move = (event: TouchEvent) => {
      if (!active || event.touches.length !== 1) { active = false; return; }
      const nextY = event.touches[0].clientY;
      const delta = y - nextY;
      if (!moved && Math.abs(delta) < 5) return;
      if (!event.cancelable) { active = false; return; }
      moved = true;
      // Safari can hand a nested scroll to the visual viewport with the keyboard
      // open. Own this gesture only; never cancel taps, pinch zoom or other areas.
      if (event.cancelable) event.preventDefault();
      const now = performance.now();
      velocity = Math.max(-2.5, Math.min(2.5, delta / Math.max(8, now - time)));
      box.scrollTop += delta;
      followLatest.current = false;
      y = nextY;
      time = now;
    };
    const end = () => {
      if (!active || !moved || performance.now() - time > 100) { active = false; return; }
      active = false;
      let previous = performance.now();
      const step = (now: number) => {
        const elapsed = Math.min(32, now - previous);
        previous = now;
        const before = box.scrollTop;
        box.scrollTop += velocity * elapsed;
        velocity *= Math.exp(-elapsed / 180);
        if (Math.abs(velocity) > .025 && Math.abs(box.scrollTop - before) > .1) frame = requestAnimationFrame(step);
        else frame = 0;
      };
      frame = requestAnimationFrame(step);
    };
    const cancel = () => { active = false; cancelMomentum(); };
    box.addEventListener("touchstart", start, { passive: true });
    box.addEventListener("touchmove", move, { passive: false });
    box.addEventListener("touchend", end, { passive: true });
    box.addEventListener("touchcancel", cancel, { passive: true });
    return () => {
      cancel();
      box.removeEventListener("touchstart", start);
      box.removeEventListener("touchmove", move);
      box.removeEventListener("touchend", end);
      box.removeEventListener("touchcancel", cancel);
    };
  }, [switching]);

  useEffect(() => {
    if (!file || !file.type.startsWith("image/")) { setPreview(""); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const pickFile = (f: File | null) => {
    draft.setFile(f);
    if (fileInput.current) fileInput.current.value = "";
  };
  const sendReply = useMutation({
    mutationFn: async () => {
      if (mode === "reply" && /\{(?:client_name|order_id|order_title|order_price|order_status)\}/.test(text)) throw new Error("Заполните переменные шаблона перед отправкой");
      if (mode === "note") return api(`/api/support/threads/${id}/notes`, {
        ...json("POST", { text, ...(nextStep.trim() ? { next_step: nextStep, due_at: dueAt ? new Date(dueAt).toISOString() : null } : {}) }),
        headers: { "Idempotency-Key": draft.requestKey },
      });
      const form = new FormData();
      form.append("text", text);
      if (file) form.append("file", file);
      return api(`/api/support/threads/${id}/reply`, { method: "POST", body: form, headers: { "Idempotency-Key": draft.requestKey, "X-Support-Route": detail.data?.route_key ?? "" } });
    },
    onSuccess: () => { followLatest.current = true; setErr(""); draft.clear(); refresh(); if (mode === "note") toast("Заметка сохранена"); },
    onError: (e: Error) => {
      if (e instanceof ApiError && e.reason === "shift_required") {
        setShiftPrompt({ action: "ответить клиенту", retry: () => sendReply.mutate() });
        return;
      }
      setErr(e.message);
    },
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["support-thread", id] });
    // Append only what is new instead of re-downloading the whole history.
    void pullNew();
    qc.invalidateQueries({ queryKey: ["support-threads"] });
    qc.invalidateQueries({ queryKey: ["support-summary"] });
  };
  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      api(`/api/support/${path}`, { method: "POST", ...(body !== undefined ? { body: JSON.stringify(body) } : {}), ...(path.endsWith("/invoice") ? { headers: { "Idempotency-Key": invoiceKey.current } } : {}) }),
    onSuccess: (_data, variables) => { invoiceKey.current = newRequestKey(); setErr(""); setModal(""); refresh(); toast(doneText(variables.path)); },
    onError: (e: Error, variables) => {
      if (e instanceof ApiError && e.reason === "shift_required") {
        setShiftPrompt({
          action: variables.path.endsWith("/claim") ? "взять диалог в работу" : "выполнить действие",
          retry: () => act.mutate(variables),
        });
        return;
      }
      setErr(e.message); refresh();
    },
  });
  const readSent = useRef(0);
  useEffect(() => {
    const markRead = () => {
      const box = messagesBox.current;
      const last = msgs.data?.[msgs.data.length - 1]?.id ?? 0;
      if (!detail.data || detail.data.assigned_to !== me.telegram_id || document.visibilityState !== "visible" || !document.hasFocus() || !box || box.scrollHeight - box.scrollTop - box.clientHeight > 64 || !last || readSent.current >= last) return;
      readSent.current = last;
      api(`/api/support/threads/${id}/read`, json("POST", { through_id: last })).then(() => {
        qc.invalidateQueries({ queryKey: ["support-summary"] }); qc.invalidateQueries({ queryKey: ["support-threads"] });
      }).catch(() => { readSent.current = 0; });
    };
    markRead();
    window.addEventListener("focus", markRead);
    document.addEventListener("visibilitychange", markRead);
    messagesBox.current?.addEventListener("scroll", markRead);
    const box = messagesBox.current;
    return () => { window.removeEventListener("focus", markRead); document.removeEventListener("visibilitychange", markRead); box?.removeEventListener("scroll", markRead); };
  }, [msgs.data, detail.data?.assigned_to, id, me.telegram_id, qc]);
  const d = detail.data;
  if (detail.error) return <Card className="card"><p className="error">{(detail.error as Error).message}</p></Card>;
  if (!d) return <div className="sup-chat"><ChatSkeleton /></div>;
  const mine = d.can_reply;
  // Drag & drop: a file dropped anywhere on the chat becomes the attachment.
  const canDrop = mine && mode === "reply" && !sendReply.isPending;
  const acceptFile = (picked: File | null) => {
    if (!picked) return;
    if (!ACCEPT.split(",").includes(picked.type)) {
      setErr("Можно прикрепить JPEG, PNG, WebP или PDF");
      return;
    }
    const limit = picked.type === "application/pdf" ? 20 * 1024 * 1024 : 10 * 1024 * 1024;
    if (picked.size > limit) {
      setErr(`Файл больше ${Math.round(limit / 1024 / 1024)} МБ`);
      return;
    }
    setErr("");
    pickFile(picked);
  };
  const hasFiles = (event: DragEvent<HTMLDivElement>) => Array.from(event.dataTransfer.types ?? []).includes("Files");
  const onDragEnter = (event: DragEvent<HTMLDivElement>) => {
    if (!canDrop || !hasFiles(event)) return;
    event.preventDefault();
    dragDepth.current += 1;
    setDragging(true);
  };
  const onDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!canDrop || !hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };
  const onDragLeave = () => {
    if (!canDrop) return;
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (!dragDepth.current) setDragging(false);
  };
  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!canDrop) return;
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    acceptFile(event.dataTransfer.files?.[0] ?? null);
  };
  const canAssign = mine || me.permissions.includes("support.assign");
  const canInvoice = me.permissions.includes("orders.invoice");
  const search = orderSearch.trim().toLocaleLowerCase("ru-RU").replace(/^#/, "");
  const orders = [...(d.orders ?? [])]
    .sort((a, b) => (Date.parse(b.created_at ?? "") || 0) - (Date.parse(a.created_at ?? "") || 0) || b.id - a.id)
    .filter((o) => `${o.id} ${o.title ?? ""} ${d.labels?.[o.status] ?? o.status}`.toLocaleLowerCase("ru-RU").includes(search));
  const jumpToMessage = async (target: number) => {
    if (jumping) return;
    setSearchOpen(false);
    followLatest.current = false;
    pendingJump.current = target;
    setHighlightedMessage(target);
    const found = messagesContent.current?.querySelector(`[data-message-id="${target}"]`);
    if (found && messagesBox.current) { const box = messagesBox.current; box.scrollTop += found.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientHeight / 2 + found.getBoundingClientRect().height / 2; pendingJump.current = null; return; }
    setJumping(true);
    try {
      let more = history.hasNextPage;
      while (more) {
        const result = await history.fetchNextPage();
        if (result.isFetchNextPageError) throw new Error("Не удалось загрузить фрагмент. Повторите переход");
        if (result.data?.pages.some((page) => page.some((m) => m.id === target))) return;
        more = result.hasNextPage;
      }
      setErr("Сообщение не найдено в истории");
    } catch (e) { setErr(e instanceof Error ? e.message : "Не удалось открыть сообщение"); }
    finally { setJumping(false); }
  };
  const chooseTemplate = (t: SupportTemplate) => {
    if (sendReply.isPending) return;
    const order = d.orders.find((o) => String(o.id) === selectedOrder);
    const values: Record<string, string | undefined> = { client_name: d.client.name, order_id: order ? String(order.id) : undefined,
      order_title: order?.title ?? undefined, order_price: order?.price != null ? `${order.price} ₽` : undefined,
      order_status: order ? d.labels[order.status] ?? order.status : undefined };
    const expanded = t.body.replace(/\{([^{}]+)\}/g, (match, key) => values[key] ?? match);
    setText(text.startsWith("/") ? expanded : [text, expanded].filter(Boolean).join("\n"));
    setTemplateOpen(false);
  };
  const send = (e: FormEvent) => {
    e.preventDefault();
    if (sendReply.isPending || draft.missingFile || (!text.trim() && !file)) return;
    sendReply.mutate();
  };
  return (
    <div ref={chatBox} className={`sup-chat${switching ? " is-switching" : ""}`} aria-busy={switching}
      onDragEnter={onDragEnter} onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}>
      {dragging && (
        <div className="sup-drop" aria-hidden="true">
          <span>Отпустите файл, чтобы прикрепить</span>
          <small>JPEG, PNG, WebP до 10 МБ · PDF до 20 МБ</small>
        </div>
      )}
      <Card className="card sup-main" data-guide="chat">
        <div className={`head${headerExpanded ? " is-expanded" : ""}`} data-guide="chat-head">
          <div className="sup-chat-avatar" aria-hidden="true">{d.client.name.trim().slice(0, 1).toUpperCase()}</div>
          <div className="sup-chat-identity">
            <b>{d.client.name}</b> {d.client.username && <span className="muted">@{d.client.username}</span>}
            <div className="muted">{d.assigned_name ? `Ведёт: ${d.assigned_name}` : "В очереди"}{d.status === "closed" && " · закрыта"}</div>
            {d.needs_reply && <small className="sup-waiting">{waitingLabel(d.waiting_since)}</small>}
          </div>
          {/* On a phone the actions live behind "⋮", but claiming a waiting dialog is the main action. */}
          {d.assigned_to === null && <Button className="sup-claim-mobile" disabled={act.isPending} onClick={() => act.mutate({ path: `threads/${id}/claim` })}>Взять</Button>}
          <button type="button" className="sup-header-toggle" aria-label="Действия с диалогом" aria-expanded={headerExpanded} onClick={() => setHeaderExpanded(!headerExpanded)}>⋮</button>
          <div className="row-actions">
            <Button className="ghost" onClick={() => setSearchOpen(!searchOpen)}>Поиск в чате</Button>
            <Button className="ghost" data-guide="client-button" onClick={() => setClientOpen(true)}>Клиент и заказы ({d.orders?.length ?? 0})</Button>
            {d.assigned_to === null && <Button onClick={() => act.mutate({ path: `threads/${id}/claim` })}>Взять</Button>}
            {canAssign && <Button className="ghost" onClick={() => setModal("transfer")}>Передать</Button>}
            {mine && d.status === "open" && <Button className="ghost" onClick={() => confirm("Закрыть диалог? Клиент получит уведомление.") && act.mutate({ path: `threads/${id}/close` })}>Закрыть</Button>}
          </div>
        </div>
        {searchOpen && <div className="sup-chat-search">
          <Input type="search" maxLength={200} aria-label="Поиск в переписке" placeholder="Найти фразу в полной истории" value={messageSearch} onChange={(e) => setMessageSearch(e.target.value)} />
          {searchResults.error && <p className="error">{searchResults.error.message}</p>}
          {messageSearch.trim() && <div className="sup-search-results">{searchResults.data?.pages.flat().map((m) => <Button className="ghost" disabled={jumping} key={m.id} onClick={() => jumpToMessage(m.id)}><small>{fmtDate(m.created_at)}</small><span>{m.body}</span></Button>)}</div>}
          {searchResults.isFetching && <small className="muted">Ищем…</small>}
          {messageSearch.trim() && searchResults.data?.pages[0]?.length === 0 && <small className="muted">Совпадений нет</small>}
          {searchResults.hasNextPage && <Button className="ghost" disabled={searchResults.isFetchingNextPage} onClick={() => searchResults.fetchNextPage()}>Ещё совпадения</Button>}
          {jumping && <small role="status">Загружаем историю до выбранного сообщения…</small>}
        </div>}
        {(d.handoff_note || d.next_step) && <div className={`sup-handoff${headerExpanded ? " is-expanded" : ""}`}><b>Контекст передачи / следующий шаг</b>{d.handoff_note && <p>{d.handoff_note}</p>}{d.next_step && <p>{d.next_step}{d.due_at && ` · до ${fmtDate(d.due_at)}`}</p>}
          {mine && d.next_step && <Button className="ghost" disabled={act.isPending} onClick={() => act.mutate({ path: `threads/${id}/notes`, body: { text: `Шаг выполнен: ${d.next_step}`, next_step: "" } })}>Шаг выполнен</Button>}
        </div>}
        <div className="sup-messages-area">
        <div className="sup-msgs" ref={messagesBox}
          onTouchStart={() => { touchingMessages.current = true; followLatest.current = false; }}
          onTouchEnd={finishMessageTouch}
          onTouchCancel={finishMessageTouch}
          onWheel={(e) => { if (e.deltaY < 0) followLatest.current = false; }}
          onScroll={() => {
          const box = messagesBox.current;
          if (!box) return;
          const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight <= 48;
          followLatest.current = !touchingMessages.current && nearBottom;
          setShowLatest(!nearBottom);
        }}>
          <div className="sup-messages-content" ref={messagesContent}>
          {history.error && <p className="error">{history.error.message}<Button className="ghost" onClick={() => history.refetch()}>Повторить загрузку</Button></p>}
          {history.hasNextPage && <Button className="ghost sup-history" disabled={history.isFetchingNextPage} onClick={() => {
            const box = messagesBox.current;
            followLatest.current = false;
            if (box) historyScroll.current = { height: box.scrollHeight, top: box.scrollTop };
            history.fetchNextPage();
          }}>{history.isFetchingNextPage ? "Загрузка истории…" : "Загрузить предыдущие сообщения"}</Button>}
          {msgs.data?.map((m) => (
            <div key={m.id} data-message-id={m.id} className={`msg ${m.direction}${highlightedMessage === m.id ? " is-highlighted" : ""}`}>
              {(m.direction === "staff" || m.direction === "internal") && <small className="muted">{m.staff_name}</small>}
              {m.direction === "internal" && <small className="sup-note-label">Внутренняя заметка · клиент её не видит</small>}
              {m.channel_label && <small className="muted">{m.channel_label}</small>}
              {(m.body || !m.attachments?.length) && <div className="bubble">{m.body}</div>}
              {(m.attachments ?? []).map((a) => <AttachmentView key={a.id} a={a} />)}
              <small className="muted msg-meta">
                {m.direction === "client" && m.cover && <span className="msg-service" title="Обложка, через которую писал клиент">{m.cover}</span>}
                <time title={m.created_at ? fmtDate(m.created_at) : undefined}>{messageTime(m.created_at)}</time>{" "}
                {m.delivery && <span className={m.delivery === "failed" || m.delivery === "uncertain" ? "error" : ""} title={m.error ?? ""}>{DELIVERY[m.delivery]}</span>}
              </small>
              {(m.delivery === "failed" || m.delivery === "uncertain") && <div className="sup-delivery-error"><p>{m.error || "Канал не подтвердил отправку"}</p>
                {mine && m.can_retry && <Button className="ghost" disabled={act.isPending} onClick={() => {
                  const uncertain = m.delivery === "uncertain";
                  if (uncertain && !confirm("Канал не подтвердил результат, но сообщение могло уйти. Повтор может создать дубликат. Повторить?")) return;
                  act.mutate({ path: `threads/${id}/messages/${m.id}/retry`, body: { confirm_uncertain: uncertain } });
                }}>Повторить отправку</Button>}
              </div>}
            </div>
          ))}
          </div>
        </div>
        {showLatest && <Button type="button" className="sup-latest" aria-label="К последнему сообщению" title="К последнему сообщению" onClick={scrollToLatest}><span aria-hidden="true">↓</span><span className="sup-latest-label"> К последнему сообщению</span></Button>}
        </div>
        {err && <p className="error">{err}</p>}
        {d.can_reply ? (
          <form className={`sup-input-wrap${mode === "note" ? " is-note" : ""}`} onSubmit={send} data-guide="composer">
            <div className={`sup-composer-options${composerExpanded ? " is-expanded" : ""}`}>
            <div className="sup-composer-tools">
              <Button type="button" className={mode === "reply" ? "" : "ghost"} disabled={sendReply.isPending} onClick={() => { setMode("reply"); setComposerExpanded(false); }}>Ответ клиенту</Button>
              <Button type="button" className={mode === "note" ? "" : "ghost"} disabled={sendReply.isPending} onClick={() => { setMode("note"); setComposerExpanded(false); }}>Внутренняя заметка</Button>
              {mode === "reply" && <Button type="button" className="ghost" data-guide="templates" onClick={() => { setTemplateOpen(true); setTemplateDismissed(false); setComposerExpanded(false); }}>Шаблоны ответов</Button>}
            </div>
            {mode === "reply" && <div className="sup-route muted">Ответ уйдёт: {d.channel_label || `Telegram · ${d.cover || "бот клиента"}`}</div>}
            {mode === "note" && <div className="sup-note-extra"><button type="button" className="sup-note-extra-toggle" aria-expanded={noteTaskOpen} onClick={() => setNoteTaskOpen(!noteTaskOpen)}>{noteTaskOpen ? "− Скрыть следующий шаг" : nextStep || dueAt ? "+ Изменить следующий шаг" : "+ Добавить следующий шаг"}</button>
              {noteTaskOpen && <div className="sup-note-task"><label>Следующий шаг<Input disabled={sendReply.isPending} placeholder="Что нужно сделать" value={nextStep} onChange={(e) => setNextStep(e.target.value)} maxLength={1000} /></label><label>Срок (необязательно)<Input disabled={sendReply.isPending} aria-label="Срок следующего шага" type="datetime-local" value={dueAt} onChange={(e) => setDueAt(e.target.value)} /></label></div>}
            </div>}
            </div>
            {mode === "reply" && !composerExpanded && (templateOpen || (text.startsWith("/") && !templateDismissed)) && <div className="sup-template-picker">
              <div className="sup-picker-heading"><b>Шаблоны ответов</b><button type="button" aria-label="Закрыть шаблоны" onClick={() => { setTemplateOpen(false); setTemplateDismissed(true); }}>×</button></div>
              <Input aria-label="Найти шаблон ответа" placeholder="Поиск шаблона" value={text.startsWith("/") ? text.slice(1) : templateSearch} onChange={(e) => text.startsWith("/") ? setText(`/${e.target.value}`) : setTemplateSearch(e.target.value)} />
              {d.orders.length > 0 && <CustomSelect aria-label="Заказ для шаблона" value={selectedOrder} onChange={(e) => setSelectedOrder(e.target.value)}><option value="">Без заказа</option>{d.orders.map((o) => <option key={o.id} value={o.id}>#{o.id} · {o.title}</option>)}</CustomSelect>}
              {templates.error && <p className="error">{templates.error.message}</p>}
              {(templates.data ?? []).filter((t) => `${t.title} ${t.body}`.toLocaleLowerCase().includes((text.startsWith("/") ? text.slice(1) : templateSearch).toLocaleLowerCase())).map((t) => <Button type="button" className="ghost" key={t.id} onClick={() => chooseTemplate(t)}>{t.title}</Button>)}
              {templates.data?.length === 0 && <small className="muted">Активных шаблонов нет. Их добавляет администратор или руководитель поддержки.</small>}
            </div>}
            {draft.missingFile && <p className="error">Текст восстановлен. Прикрепите файл заново: браузер не сохраняет файл после перезагрузки. <Button type="button" className="ghost" onClick={() => draft.setFile(null)}>Отправить без прежнего файла</Button></p>}
            {draft.storageError ? <small className="error">Не удалось сохранить черновик на устройстве. Не закрывайте вкладку.</small> : !!draft.savedAt && <small className="muted sup-draft-saved">Черновик сохранён на этом устройстве · {new Date(draft.savedAt).toLocaleTimeString("ru-RU")}</small>}
            {mode === "reply" && file && (
              <div className="att-pending">
                {preview ? <img src={preview} alt="" /> : <Icon name="clip" />}
                <span>{file.name} · {fmtSize(file.size)}</span>
                <Button type="button" className="link" disabled={sendReply.isPending} onClick={() => pickFile(null)}>Убрать</Button>
              </div>
            )}
            {mode === "note" && <div className="sup-note-indicator">Внутренняя заметка · клиент её не увидит</div>}
            <div className="sup-input">
              <input ref={fileInput} type="file" disabled={sendReply.isPending} accept={ACCEPT} hidden onChange={(e) => pickFile(e.target.files?.[0] ?? null)} />
              <button type="button" className="sup-composer-toggle" aria-label="Настройки сообщения" aria-expanded={composerExpanded} onClick={() => { setTemplateOpen(false); setTemplateDismissed(true); setComposerExpanded(!composerExpanded); }}>＋</button>
              {mode === "reply" && <Button type="button" disabled={sendReply.isPending} className="ghost clip" title="Прикрепить файл (JPEG, PNG, WebP, PDF)" onClick={() => fileInput.current?.click()}><Icon name="clip" /></Button>}
              <Textarea rows={1} maxLength={3500} value={text} disabled={sendReply.isPending} onFocus={(e) => stabilizeSafariComposer(e.currentTarget)} placeholder={mode === "note" ? "Заметка для команды" : file ? "Подпись к файлу" : "Сообщение"} onChange={(e) => { setText(e.target.value); setTemplateDismissed(false); }}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && window.matchMedia("(hover: hover) and (pointer: fine)").matches) send(e as any); }} />
              <Button className="sup-send" onPointerDown={(e) => e.preventDefault()} aria-label={mode === "note" ? "Добавить заметку" : "Отправить сообщение"} disabled={sendReply.isPending || draft.missingFile || (!text.trim() && !file)}><span className="sup-send-label">{sendReply.isPending ? "Сохраняем…" : mode === "note" ? "Добавить заметку" : "Отправить"}</span><span className="sup-send-icon" aria-hidden="true">↑</span></Button>
            </div>
          </form>
        ) : (
          <p className="muted">{d.assigned_to === null ? "Возьмите ветку, чтобы ответить." : "Ветка закреплена за другим сотрудником."}</p>
        )}
      </Card>
      <Dialog open={clientOpen} onOpenChange={setClientOpen}>
      <DialogContent className="app-dialog sup-client-drawer" aria-describedby={undefined} onOpenAutoFocus={(e) => { e.preventDefault(); clientTitle.current?.focus(); }}>
        <div className="sup-client-heading" ref={clientTitle} tabIndex={-1}>
          <DialogTitle>Клиент и заказы</DialogTitle>
          <p className="muted">{d.client.name}</p>
        </div>
        <div className="sup-side" data-guide="client-card">
        {err && <p className="error">{err}</p>}
        <h2>Клиент</h2>
        <p className="muted">ID {d.client.id}{d.client.source ? ` · ${d.client.source}` : ""}<br />Обложки: {d.covers?.join(", ") || "—"}{d.channel_label && <><br />Канал: {d.channel_label}</>}</p>
        {mine && (
          <Button className={d.client.is_banned ? "ghost" : "bad"} onClick={() => act.mutate({ path: `threads/${id}/ban`, body: { banned: !d.client.is_banned } })}>
            {d.client.is_banned ? "Разбанить" : "Забанить"}
          </Button>
        )}
        {d.client.is_banned && <p className="error">Клиент забанен</p>}
        <h2>Заказы</h2>
        {canInvoice && (mine || d.can_refund) && <Button onClick={() => { setClientOpen(false); setModal("invoice"); }}>Выставить счёт</Button>}
        <Input type="search" aria-label="Поиск по заказам" placeholder="Номер заказа, название или статус" value={orderSearch} onChange={(e) => setOrderSearch(e.target.value)} />
        <small className="muted">Сначала новые · {orders.length} из {d.orders?.length ?? 0}</small>
        {!d.orders?.length && <p className="muted">Заказов нет</p>}
        {!!d.orders?.length && !orders.length && <p className="muted">Заказы не найдены</p>}
        {orders.map((o) => (
          <div key={o.id} className="sup-order">
            <b>#{o.id}</b> {o.title} {o.price != null && `· ${o.price} ₽`}
            <small className="muted">Создан {fmtDate(o.created_at)}</small>
            <div className="muted">{d.labels?.[o.status] ?? o.status}</div>
            {o.operations && <div className="muted">Оплата: {o.operations.paid_at ? `${o.operations.paid_amount ?? 0} ₽ · ${fmtDate(o.operations.paid_at)}` : "не подтверждена"}<br />Возврат: {o.operations.refunded_at ? `${o.operations.refund_amount ?? 0} ₽ · ${fmtDate(o.operations.refunded_at)}` : "не зарегистрирован"}</div>}
            {(mine || d.can_refund || me.permissions.includes("buyout.manage")) && (
              <CustomSelect disabled={act.isPending || !o.allowed_transitions?.length} value="" onChange={(e) => {
                const v = e.target.value;
                if (!v) return;
                if (v === "refunded") { setRefundOrder(o); return; }
                setStatusChange({ order: o, target: v });
              }}>
                <option value="">Сменить статус…</option>
                {(o.allowed_transitions ?? []).map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}
              </CustomSelect>
            )}
            {mine && !o.can_ping && o.next_ping_at && new Date(o.next_ping_at).getTime() > Date.now() && <small className="muted">Повторить напоминание после {fmtDate(o.next_ping_at)}</small>}
            {mine && o.can_ping && (
              <Button className="ghost" disabled={act.isPending} title={o.last_ping_at ? `Последнее напоминание: ${fmtDate(o.last_ping_at)}` : ""}
                onClick={() => act.mutate({ path: `orders/${o.id}/ping` })}>🔔 Напомнить</Button>
            )}
          </div>
        ))}
        </div>
      </DialogContent>
      </Dialog>
      {statusChange && <StatusModal order={statusChange.order} target={statusChange.target} label={d.labels[statusChange.target]} pending={act.isPending} err={err} onClose={() => setStatusChange(null)} onSubmit={(reason) => act.mutate({ path: `orders/${statusChange.order.id}/status`, body: { status: statusChange.target, expected_status: statusChange.order.status, reason } }, { onSuccess: () => setStatusChange(null) })} />}
      {refundOrder && <RefundModal order={refundOrder} onClose={() => setRefundOrder(null)} pending={act.isPending} err={err} onSubmit={(body) => act.mutate({ path: `orders/${refundOrder.id}/status`, body }, { onSuccess: () => setRefundOrder(null) })} />}
      {modal === "transfer" && <TransferModal onClose={() => setModal("")} pending={act.isPending} onPick={(body) => act.mutate({ path: `threads/${id}/transfer`, body })} err={err} />}
      {modal === "invoice" && <InvoiceModal onClose={() => setModal("")} onSubmit={(b) => act.mutate({ path: `threads/${id}/invoice`, body: b })} err={err} pending={act.isPending} />}
      {shiftPrompt && (
        <ShiftRequiredDialog
          open action={shiftPrompt.action}
          onClose={() => setShiftPrompt(null)}
          onStarted={() => { const retry = shiftPrompt.retry; setShiftPrompt(null); retry(); }}
        />
      )}
    </div>
  );
}

function TransferModal({ onClose, onPick, err, pending }: { onClose: () => void; onPick: (body: unknown) => void; err: string; pending: boolean }) {
  const agents = useQuery({ queryKey: ["support-agents"], queryFn: () => api<Agent[]>("/api/support/agents") });
  const [staffId, setStaffId] = useState("");
  const [summary, setSummary] = useState("");
  const [step, setStep] = useState("");
  const [due, setDue] = useState("");
  return <Dialog open onOpenChange={(open) => { if (!open && !pending) onClose(); }}><DialogContent className="app-dialog" aria-describedby={undefined}>
    <form className="form" onSubmit={(e) => { e.preventDefault(); onPick({ staff_id: Number(staffId), summary, next_step: step, due_at: due ? new Date(due).toISOString() : null }); }}>
      <DialogTitle>Передать диалог с контекстом</DialogTitle>
      {agents.error && <p className="error">{agents.error.message}</p>}
      <label>Получатель<CustomSelect value={staffId} onChange={(e) => setStaffId(e.target.value)} required><option value="">Выберите сотрудника</option>{agents.data?.map((a) => <option key={a.telegram_id} value={a.telegram_id} disabled={!a.on_shift}>{a.display_name} · {a.on_shift ? `на смене · ${a.open} диалогов` : "вне смены"}</option>)}</CustomSelect></label>
      <label>Что случилось и что уже проверено<Textarea required maxLength={2000} value={summary} onChange={(e) => setSummary(e.target.value)} /></label>
      <label>Что нужно сделать / что обещано<Input required maxLength={1000} value={step} onChange={(e) => setStep(e.target.value)} /></label>
      <label>Срок (необязательно)<Input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} /></label>
      {err && <p className="error">{err}</p>}
      <div className="row-actions"><Button type="button" className="ghost" disabled={pending} onClick={onClose}>Отмена</Button><Button disabled={pending || !staffId || !summary.trim() || !step.trim()}>Передать</Button></div>
    </form>
  </DialogContent></Dialog>;
}

function StatusModal({ order, target, label, onClose, onSubmit, pending, err }: { order: OrderRow; target: string; label: string; onClose: () => void; onSubmit: (reason: string) => void; pending: boolean; err: string }) {
  const [reason, setReason] = useState("");
  return <Dialog open onOpenChange={(v) => { if (!v && !pending) onClose(); }}><DialogContent className="app-dialog" aria-describedby={undefined}><form className="form" onSubmit={(e) => { e.preventDefault(); onSubmit(reason); }}>
    <DialogTitle>Изменить статус заказа #{order.id}</DialogTitle><p>Новый статус: <b>{label || target}</b>. Клиент получит уведомление об изменении.</p>
    <label>Причина / основание<Textarea required maxLength={1000} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
    {err && <p className="error">{err}</p>}<div className="row-actions"><Button type="button" className="ghost" onClick={onClose} disabled={pending}>Отмена</Button><Button disabled={pending || !reason.trim()}>Подтвердить изменение</Button></div>
  </form></DialogContent></Dialog>;
}

function RefundModal({ order, onClose, onSubmit, pending, err }: { order: OrderRow; onClose: () => void; onSubmit: (body: unknown) => void; pending: boolean; err: string }) {
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  return <Dialog open onOpenChange={(v) => { if (!v && !pending) onClose(); }}><DialogContent className="app-dialog" aria-describedby={undefined}><form className="form" onSubmit={(e) => { e.preventDefault(); onSubmit({ status: "refunded", expected_status: order.status, refund_amount: Number(amount), refund_reference: reference, reason: "Подтверждён выполненный полный возврат" }); }}>
    <DialogTitle>Подтвердить возврат по #{order.id}</DialogTitle><p>Укажите уже выполненный перевод. Эта отметка не переводит деньги; клиент получит уведомление о возврате.</p>
    <label>Фактически возвращено, ₽<Input type="number" min={0} required value={amount} onChange={(e) => setAmount(e.target.value)} /></label>
    <label>Подтверждение / номер операции<Input required maxLength={500} value={reference} onChange={(e) => setReference(e.target.value)} /></label>
    {err && <p className="error">{err}</p>}<div className="row-actions"><Button type="button" className="ghost" onClick={onClose} disabled={pending}>Отмена</Button><Button disabled={pending || !reference.trim() || !amount}>Подтвердить выполненный возврат</Button></div>
  </form></DialogContent></Dialog>;
}

function InvoiceModal({ onClose, onSubmit, err, pending }: { onClose: () => void; onSubmit: (b: unknown) => void; err: string; pending: boolean }) {
  const [f, setF] = useState({ title: "", description: "", price: "" });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}><DialogContent className="app-dialog" aria-describedby={undefined}>
      <form className="form"
        onSubmit={(e) => { e.preventDefault(); onSubmit({ title: f.title, description: f.description, price: Number(f.price) }); }}>
        <DialogTitle>Выставить счёт</DialogTitle>
        <label>Название<Input value={f.title} onChange={set("title")} maxLength={256} required /></label>
        <label>Описание<Textarea rows={3} value={f.description} onChange={set("description")} /></label>
        <label>Цена, ₽<Input type="number" min={1} value={f.price} onChange={set("price")} required /></label>
        {err && <p className="error">{err}</p>}
        <div className="row-actions">
          <Button type="button" className="ghost" onClick={onClose}>Отмена</Button>
          <Button disabled={pending || !f.title || !Number(f.price)}>Выставить</Button>
        </div>
      </form>
    </DialogContent></Dialog>
  );
}

export default function Support({ me }: { me: Me }) {
  // The panel-wide socket (App.tsx) already refreshes these caches on push.
  const { connected, syncedAt } = useRealtimeState();
  const connection = connected ? "Онлайн" : "Переподключаемся · резервное обновление каждые 15 с";
  const qc = useQueryClient();
  const { id } = useParams();
  const tid = id ? Number(id) : undefined;
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 1000px)").matches);
  const [mobileList, setMobileList] = useState(!tid);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 1000px)");
    const update = () => setMobile(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => { setMobileList(!tid); }, [tid]);
  useLayoutEffect(() => {
    if (!mobile) return;
    const viewport = window.visualViewport;
    const root = document.documentElement;
    // Chrome reports its own keyboard animation; Safari's delayed close needs
    // prediction. Do not run both animations in Chromium.
    const safari = (/iPad|iPhone|iPod/.test(navigator.userAgent)
      || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1))
      && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(navigator.userAgent);
    let lastHeight = 0;
    let lastTop = -1;
    let restHeight = Math.round(viewport?.height ?? window.innerHeight);
    let closingTarget = 0;
    let closingUntil = 0;
    let settleTimer = 0;
    let focusFrame = 0;
    const editable = () => document.activeElement?.matches("input, textarea, [contenteditable='true']");
    const apply = (height: number, top: number, animate = false) => {
      root.classList.toggle("support-keyboard-closing", safari && animate);
      // Browser toolbar/bottom bounce can alternate by one CSS pixel. Avoid
      // relayout and scroll-to-bottom work for that measurement noise.
      if (!lastHeight || Math.abs(height - lastHeight) > 1) {
        root.style.setProperty("--support-viewport-height", `${height}px`);
        lastHeight = height;
      }
      if (lastTop < 0 || Math.abs(top - lastTop) > 1) {
        root.style.setProperty("--support-viewport-top", `${top}px`);
        lastTop = top;
      }
    };
    const update = () => {
      if (viewport && Math.abs(viewport.scale - 1) > 0.01) return;
      const height = Math.round(viewport?.height ?? window.innerHeight);
      const top = Math.max(0, Math.round(viewport?.offsetTop ?? 0));
      // Safari may report the old keyboard height throughout its closing animation.
      // Keep the predicted end height until the browser catches up.
      if (closingTarget && performance.now() < closingUntil && height < closingTarget - 40) return;
      const animate = (height >= lastHeight && root.classList.contains("support-keyboard-closing")) || (lastHeight > 0 && height - lastHeight > 80);
      apply(height, top, animate);
      if (!editable() && !closingTarget) restHeight = height;
    };
    const focusIn = () => {
      cancelAnimationFrame(focusFrame);
      clearTimeout(settleTimer);
      closingTarget = 0;
      closingUntil = 0;
      root.classList.remove("support-keyboard-closing");
      update();
    };
    const focusOut = () => {
      cancelAnimationFrame(focusFrame);
      focusFrame = requestAnimationFrame(() => {
        if (!safari || editable() || restHeight - lastHeight < 100) return;
        closingTarget = restHeight;
        closingUntil = performance.now() + 500;
        apply(restHeight, 0, true);
        clearTimeout(settleTimer);
        settleTimer = window.setTimeout(() => {
          closingTarget = 0;
          update();
          root.classList.remove("support-keyboard-closing");
        }, 600);
      });
    };
    const orientation = () => {
      closingTarget = 0;
      restHeight = Math.round(viewport?.height ?? window.innerHeight);
      lastHeight = 0;
      root.classList.remove("support-keyboard-closing");
      update();
    };
    update();
    viewport?.addEventListener("resize", update);
    viewport?.addEventListener("scroll", update);
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", orientation);
    document.addEventListener("focusin", focusIn);
    document.addEventListener("focusout", focusOut);
    return () => {
      viewport?.removeEventListener("resize", update);
      viewport?.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", orientation);
      document.removeEventListener("focusin", focusIn);
      document.removeEventListener("focusout", focusOut);
      cancelAnimationFrame(focusFrame);
      clearTimeout(settleTimer);
      root.classList.remove("support-keyboard-closing");
      root.style.removeProperty("--support-viewport-height");
      root.style.removeProperty("--support-viewport-top");
    };
  }, [mobile]);
  const [leftWidth, setLeftWidth] = useState(() => storedWidth("support-left-width", 320));
  const [showLeft, setShowLeft] = useState(() => storedVisible("support-left-visible"));
  useEffect(() => { localStorage.setItem("support-left-width", String(leftWidth)); }, [leftWidth]);
  useEffect(() => { localStorage.setItem("support-left-visible", String(showLeft)); }, [showLeft]);
  const listOpen = mobile ? mobileList : showLeft;
  return (
    <div className={`support-workspace${mobile && mobileList ? " mobile-list-open" : ""}${mobile && tid && !mobileList ? " mobile-chat-open" : ""}`}>
      <div className="sup-toolbar"><small role="status" className="muted">{connection}{syncedAt && ` · сигнал ${new Date(syncedAt).toLocaleTimeString("ru-RU")}`}</small>
        <Button type="button" className="ghost" aria-label={mobile ? (mobileList && tid ? "Вернуться в чат" : "Показать диалоги") : undefined} title={mobile ? (mobileList && tid ? "Вернуться в чат" : "Показать диалоги") : undefined} aria-expanded={listOpen} aria-controls="support-list-panel" onClick={() => mobile ? setMobileList(!mobileList) : setShowLeft(!showLeft)}>{mobile ? (mobileList && tid ? "← Вернуться в чат" : "‹") : (showLeft ? "◀ Скрыть диалоги" : "▶ Показать диалоги")}</Button>
      </div>
      <div className="sup" style={{ "--sup-left-width": `${leftWidth}px` } as CSSProperties}>
        <div className="sup-mobile-list-wrap" onClick={(e) => { if (mobile && (e.target as HTMLElement).closest(".sup-row") && !(e.target as HTMLElement).closest("button")) setMobileList(false); }}><AnimatedPanel id="support-list-panel" className="sup-list-panel" open={listOpen}><List me={me} current={tid} /></AnimatedPanel></div>
        <ResizeHandle side="left" width={leftWidth} onResize={setLeftWidth} open={showLeft} />
        {tid ? (
          <Chat key={tid} me={me} id={tid} />
        ) : (
          <div className="sup-empty">
            <TybeeEmptyState
              title={`Выберите диалог ${showLeft ? "слева" : "в списке диалогов"}`}
              description="Кликните на любого клиента, чтобы посмотреть историю сообщений и заказов"
            />
          </div>
        )}
      </div>
    </div>
  );
}

function AnimatedPanel({ id, className, open, children }: { id: string; className: string; open: boolean; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (ref.current) ref.current.inert = !open; }, [open]);
  return <div ref={ref} id={id} className={`${className}${open ? "" : " is-closed"}`} aria-hidden={!open}>{children}</div>;
}

function storedWidth(key: string, fallback: number) {
  const n = Number(localStorage.getItem(key));
  return Number.isFinite(n) && n >= 180 && n <= 900 ? Math.max(280, Math.min(480, n)) : fallback;
}

function storedVisible(key: string) { return localStorage.getItem(key) !== "false"; }

function ResizeHandle({ side, width, onResize, open }: { side: "left" | "right"; width: number; onResize: (width: number) => void; open: boolean }) {
  const drag = useRef<{ x: number; width: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const update = (value: number) => onResize(Math.max(280, Math.min(480, value)));
  // Measure the panel itself: it may sit inside a `display: contents` wrapper whose
  // box has no width, and the panel is the element the flex-basis actually clamps.
  const panelOf = (handle: HTMLElement) =>
    handle.parentElement?.querySelector<HTMLElement>(side === "left" ? ".sup-list-panel" : ".sup-side-panel") ?? null;
  const down = (e: PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const panel = panelOf(e.currentTarget);
    const measured = panel?.getBoundingClientRect().width;
    drag.current = { x: e.clientX, width: measured && measured > 0 ? measured : width };
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
    e.preventDefault();
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (drag.current) update(drag.current.width + (e.clientX - drag.current.x) * (side === "left" ? 1 : -1));
  };
  const stop = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = null;
    setDragging(false);
    if (e.currentTarget.hasPointerCapture?.(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  };
  // The grab cursor and "no layout transitions" flags live on <html>, so a fast drag
  // that leaves the 12px strip keeps behaving like a native splitter.
  useEffect(() => {
    const root = document.documentElement;
    if (dragging) root.classList.add("sup-dragging"); else root.classList.remove("sup-dragging");
    return () => root.classList.remove("sup-dragging");
  }, [dragging]);
  const key = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    update(width + (e.key === "ArrowRight" ? 20 : -20) * (side === "left" ? 1 : -1));
  };
  return <div className={`sup-resize${open ? "" : " is-closed"}${dragging ? " is-dragging" : ""}`} role="separator" aria-hidden={!open} aria-label={side === "left" ? "Изменить ширину списка диалогов" : "Изменить ширину панели клиента"} aria-orientation="vertical" aria-valuemin={280} aria-valuemax={480} aria-valuenow={width} tabIndex={open ? 0 : -1}
    onPointerDown={down} onPointerMove={move} onPointerUp={stop} onPointerCancel={stop} onLostPointerCapture={stop} onKeyDown={key} />;
}
