import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Link } from "react-router-dom";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { CustomSelect } from "./Controls";
// Support shift: sidebar control, presence heartbeat, "are you there?" prompt and the shift log.
// The server ends a shift on its own when the tab is closed, nobody touches the
// mouse/keyboard, or a client waits unopened too long (see app/support/shift.py).
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, fmtDate, json } from "./api";
import { useRealtimeState } from "./realtime";
import { Skeleton, SkeletonText, TableSkeletonRows } from "./Skeletons";
import { setTitleOverride } from "./notify";

type ShiftState = {
  started_at: string | null; elapsed_sec: number; idle_after_sec: number;
  auto_ended: { reason: string; label: string; at: string | null } | null;
};
export type SupportSummary = { unread: number; queue: number; on_shift: boolean; shift: ShiftState };

export const useSupportSummary = (enabled: boolean) => {
  // With a live socket the badge updates on push; the timer is only a safety net.
  const { connected } = useRealtimeState();
  return useQuery({
    queryKey: ["support-summary"],
    queryFn: () => api<SupportSummary>("/api/support/summary"),
    enabled,
    refetchInterval: connected ? 60_000 : 15_000,
    // The counter drives the tab badge and the new-message alert, so it must keep
    // polling while the tab is in the background — exactly when alerts matter.
    refetchIntervalInBackground: true,
  });
};

const BEAT_MS = 30_000;
const WARN_BEFORE_MS = 2 * 60_000;
const INPUT_EVENTS = ["mousemove", "mousedown", "keydown", "wheel", "touchstart", "scroll"] as const;

export const fmtDuration = (sec: number) => {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const parts: string[] = [];
  if (h) parts.push(`${h} ч`);
  if (h || m) parts.push(`${String(m).padStart(h ? 2 : 1, "0")} мин`);
  parts.push(`${String(s).padStart(2, "0")} с`);
  return parts.join(" ");
};
const clock = (sec: number) => {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;
};

/** Pings the server every 30 s; `active` = there was real input since the previous ping. */
function usePresence() {
  const lastInput = useRef(Date.now());
  const lastBeat = useRef(0);
  const beat = () => {
    const active = lastInput.current > lastBeat.current;
    lastBeat.current = Date.now();
    api("/api/me/presence", json("POST", { active })).catch(() => {});
  };
  useEffect(() => {
    const mark = () => { lastInput.current = Date.now(); };
    INPUT_EVENTS.forEach((e) => window.addEventListener(e, mark, { passive: true, capture: true }));
    beat();
    const t = window.setInterval(beat, BEAT_MS);
    return () => {
      window.clearInterval(t);
      INPUT_EVENTS.forEach((e) => window.removeEventListener(e, mark, { capture: true }));
    };
  }, []);
  return { lastInput, beatNow: () => { lastInput.current = Date.now(); beat(); } };
}

function IdlePrompt({ leftSec, onHere }: { leftSec: number; onHere: () => void }) {
  return (
    <div className="modal-bg">
      <Card className="card modal idle-prompt" role="alertdialog" aria-labelledby="idle-title">
        <h2 id="idle-title">Вы на месте?</h2>
        <p>Давно не было активности в панели. Если не подтвердить, смена завершится через <b>{leftSec} с</b>,
          а диалоги, ожидающие ответа, вернутся в очередь.</p>
        <div className="row-actions"><Button onClick={onHere} autoFocus>Я здесь</Button></div>
      </Card>
    </div>
  );
}

/** "Начать смену?" — shown whenever an action needs an active shift.

    The caller keeps the original request (and its Idempotency-Key) and repeats it
    in ``onStarted`` once the shift is running. */
export function ShiftRequiredDialog({
  open, action, onClose, onStarted,
}: { open: boolean; action: string; onClose: () => void; onStarted: () => void }) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: () => api("/api/me/shift", json("PATCH", { on_shift: true })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["support-summary"] });
      qc.invalidateQueries({ queryKey: ["support-threads"] });
      qc.invalidateQueries({ queryKey: ["support-thread"] });
      onStarted();
    },
  });
  return (
    <Dialog open={open} onOpenChange={(value) => { if (!value && !toggle.isPending) onClose(); }}>
      <DialogContent className="app-dialog" aria-describedby={undefined}>
        <DialogTitle>Начать смену?</DialogTitle>
        <p>Чтобы {action}, нужна активная смена. Пока смена не начата, брать диалоги и отвечать клиентам нельзя.</p>
        {toggle.error && <p className="error">{(toggle.error as Error).message}</p>}
        <div className="row-actions">
          <Button type="button" className="ghost" disabled={toggle.isPending} onClick={onClose}>Отмена</Button>
          <Button type="button" disabled={toggle.isPending} onClick={() => toggle.mutate()}>
            {toggle.isPending ? "Начинаем…" : "Начать смену"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ShiftControl() {
  const qc = useQueryClient();
  const summary = useSupportSummary(true);
  const { lastInput, beatNow } = usePresence();
  const [now, setNow] = useState(Date.now());
  const [endOpen, setEndOpen] = useState(false);
  const handoff = useQuery({ queryKey: ["shift-handoff"], queryFn: () => api<{ id: number; client: { name: string }; needs_reply: boolean; next_step: string | null; due_at: string | null }[]>("/api/support/handoff"), enabled: endOpen, refetchInterval: endOpen ? 5000 : false });
  const s = summary.data;
  const onShift = !!s?.on_shift;

  const toggle = useMutation({
    mutationFn: (on_shift: boolean) => api("/api/me/shift", json("PATCH", { on_shift })),
    onSuccess: () => { setEndOpen(false); qc.invalidateQueries({ queryKey: ["support-summary"] }); qc.invalidateQueries({ queryKey: ["shifts"] }); qc.invalidateQueries({ queryKey: ["support-threads"] }); qc.invalidateQueries({ queryKey: ["support-thread"] }); },
  });

  const idleMs = s ? s.shift.idle_after_sec * 1000 : Infinity;
  const idleFor = now - lastInput.current;
  const warn = onShift && idleFor > idleMs - WARN_BEFORE_MS;
  // Tick once a second while the prompt is up or a shift is running (the timer shows seconds),
  // otherwise every 10 s is enough.
  useEffect(() => {
    if (!onShift) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [onShift, warn]);
  useEffect(() => {
    if (!warn) return;
    setTitleOverride("⚠ Вы на месте? — Тайби");
    return () => setTitleOverride(null);
  }, [warn]);

  if (!s) return (
    <div className="shift off" role="status" aria-busy="true" data-guide="shift">
      <div className="shift-status"><Skeleton style={{ width: 108, height: 14 }} /></div>
      <Skeleton style={{ width: 96, height: 30, borderRadius: 8 }} />
    </div>
  );
  const end = () => {
    setEndOpen(true);
  };
  // Count locally from the server's start time, so the seconds tick smoothly between polls.
  const elapsed = s.shift.started_at
    ? Math.max(0, Math.floor((now - new Date(s.shift.started_at).getTime()) / 1000))
    : s.shift.elapsed_sec;
  return (
    <div className={`shift ${onShift ? "on" : "off"}`} data-guide="shift">
      <div className="shift-status">
        <span className="shift-dot" />
        <div>
          <div>{onShift ? "На смене" : "Не на смене"}</div>
          {onShift && <small title="Время на смене">{clock(elapsed)}</small>}
        </div>
      </div>
      {onShift
        ? <Button className="shift-btn" disabled={toggle.isPending} onClick={end}>Завершить</Button>
        : <Button className="shift-btn start" disabled={toggle.isPending} onClick={() => toggle.mutate(true)}>Начать смену</Button>}
      {!onShift && s.shift.auto_ended && (
        <small className="shift-note" title={fmtDate(s.shift.auto_ended.at)}>
          Завершена автоматически: {s.shift.auto_ended.label}
        </small>
      )}
      {toggle.error && <small className="shift-note">{(toggle.error as Error).message}</small>}
      <Dialog open={endOpen} onOpenChange={setEndOpen}><DialogContent className="app-dialog" aria-describedby={undefined}>
        <DialogTitle>Завершение смены</DialogTitle>
        <p>Ожидающие ответа диалоги и незавершённые шаги вернутся в общую очередь. Контекст и обещания сохранятся.</p>
        {handoff.isLoading && <SkeletonText lines={2} />}
        {handoff.error && <p className="error">{handoff.error.message}</p>}
        <div className="shift-handoff-list">{handoff.data?.map((t) => <Link key={t.id} to={`/support/${t.id}`} onClick={() => setEndOpen(false)}><b>{t.client.name}</b><div>{t.needs_reply ? "Нужен ответ" : "Незавершённый шаг"}</div>{t.next_step && <div>{t.next_step}{t.due_at && ` · ${fmtDate(t.due_at)}`}</div>}</Link>)}</div>
        {handoff.data?.length === 0 && <p className="muted">Ожидающих ответа и незавершённых шагов нет.</p>}
        {toggle.error && <p className="error">{toggle.error.message}</p>}
        <div className="row-actions"><Button className="ghost" onClick={() => setEndOpen(false)}>Продолжить смену</Button><Button disabled={toggle.isPending || !handoff.data} onClick={() => toggle.mutate(false)}>Завершить и вернуть в очередь</Button></div>
      </DialogContent></Dialog>
      {warn && <IdlePrompt leftSec={Math.max(0, Math.ceil((idleMs - idleFor) / 1000))} onHere={() => { beatNow(); setNow(Date.now()); }} />}
    </div>
  );
}

type ShiftRow = {
  id: number; staff_id: number; staff_name: string; started_at: string | null; ended_at: string | null;
  duration_sec: number; end_reason: string | null; end_label: string | null; auto: boolean;
};
type ShiftTotal = { staff_id: number; staff_name: string; shifts: number; total_sec: number; auto_ended: number };
type ShiftLog = { days: number; all: boolean; rows: ShiftRow[]; totals: ShiftTotal[] };

export function ShiftLogBlock({ title, all, staffId }: { title: string; all?: boolean; staffId?: number }) {
  const [days, setDays] = useState(7);
  const q = useQuery({
    queryKey: ["shifts", days, all, staffId],
    queryFn: () => api<ShiftLog>(`/api/support/shifts?days=${days}${!all && staffId != null ? `&staff_id=${staffId}` : ""}`),
    refetchInterval: 60_000,
  });
  const d = q.data;
  const mine = !all && d?.totals[0];
  return (
    <>
      <div className="head" style={{ marginTop: 28 }}>
        <h2 style={{ margin: 0 }}>{title}</h2>
        <CustomSelect value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ width: "auto" }}>
          <option value={1}>За сутки</option>
          <option value={7}>За 7 дней</option>
          <option value={30}>За 30 дней</option>
        </CustomSelect>
      </div>
      {q.error && <p className="error">{(q.error as Error).message}</p>}
      {!d && <Table><TableBody><TableSkeletonRows rows={3} cols={all ? 5 : 4} /></TableBody></Table>}
      {mine && (
        <p className="muted">Всего {fmtDuration(mine.total_sec)} за {mine.shifts} смен
          {mine.auto_ended ? `, автоматически завершено: ${mine.auto_ended}` : ""}.</p>
      )}
      {all && d && d.totals.length > 0 && (
        <Table>
          <TableHeader><TableRow><TableHead>Сотрудник</TableHead><TableHead>Смен</TableHead><TableHead>Отработано</TableHead><TableHead>Завершено системой</TableHead></TableRow></TableHeader>
          <TableBody>
            {d.totals.map((t) => (
              <TableRow key={t.staff_id}>
                <TableCell>{t.staff_name}</TableCell><TableCell>{t.shifts}</TableCell><TableCell>{fmtDuration(t.total_sec)}</TableCell>
                <TableCell>{t.auto_ended ? <span className="badge wait">{t.auto_ended}</span> : <span className="muted">0</span>}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {d && (
        <Table style={{ marginTop: all ? 12 : 0 }}>
          <TableHeader><TableRow>{all && <TableHead>Сотрудник</TableHead>}<TableHead>Начало</TableHead><TableHead>Конец</TableHead><TableHead>Длительность</TableHead><TableHead>Как завершена</TableHead></TableRow></TableHeader>
          <TableBody>
            {d.rows.map((r) => (
              <TableRow key={r.id}>
                {all && <TableCell>{r.staff_name}</TableCell>}
                <TableCell>{fmtDate(r.started_at)}</TableCell>
                <TableCell>{r.ended_at ? fmtDate(r.ended_at) : <span className="badge ok">идёт</span>}</TableCell>
                <TableCell>{fmtDuration(r.duration_sec)}</TableCell>
                <TableCell>{r.auto ? <span className="badge wait">{r.end_label}</span> : (r.end_label ?? "—")}</TableCell>
              </TableRow>
            ))}
            {!d.rows.length && <TableRow><TableCell colSpan={all ? 5 : 4} className="muted">Смен за период нет</TableCell></TableRow>}
          </TableBody>
        </Table>
      )}
    </>
  );
}
