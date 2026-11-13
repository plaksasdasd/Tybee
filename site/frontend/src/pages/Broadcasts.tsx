import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { CustomCheckbox } from "@/Controls";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, fmtDate } from "../api";
import { TableSkeletonRows } from "../Skeletons";

type CoverOpt = { id: number; slug: string; enabled: boolean; bot_username: string | null };
type Row = {
  id: number; status: string; body: string; has_image: boolean; covers: string[]; total: number; sent: number; failed: number;
  fail_reasons: Record<string, number>; created_at: string | null; finished_at: string | null;
};
const STATUS: Record<string, { label: string; cls: string }> = {
  queued: { label: "В очереди", cls: "wait" }, running: { label: "Идёт", cls: "wait" }, done: { label: "Завершена", cls: "ok" },
  cancelled: { label: "Отменена", cls: "off" }, failed: { label: "Ошибка", cls: "bad" },
};

export default function Broadcasts() {
  const qc = useQueryClient();
  const covers = useQuery({ queryKey: ["bc-covers"], queryFn: () => api<CoverOpt[]>("/api/broadcasts/covers") });
  const list = useQuery({ queryKey: ["broadcasts"], queryFn: () => api<Row[]>("/api/broadcasts"), refetchInterval: 3000 });
  const [body, setBody] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [picked, setPicked] = useState<number[]>([]);
  const [info, setInfo] = useState("");
  const [err, setErr] = useState("");
  const [previewed, setPreviewed] = useState<number | null>(null);
  const form = () => {
    const f = new FormData();
    f.append("body", body);
    f.append("cover_ids", picked.join(","));
    if (image) f.append("image", image);
    return f;
  };
  const call = (path: string) => api(`/api/broadcasts${path}`, { method: "POST", body: form() });
  const onError = (e: Error) => { setInfo(""); setErr(e.message); };
  const preview = useMutation({
    mutationFn: () => call("/preview"),
    onSuccess: (r: any) => { setErr(""); setPreviewed(r.recipients); setInfo(`Получателей: ${r.recipients} (обложек: ${r.covers}). Одно сообщение на человека.`); },
    onError,
  });
  const test = useMutation({ mutationFn: () => call("/test"), onSuccess: () => { setErr(""); setInfo("Тестовое сообщение отправлено вам через бот входа."); }, onError });
  const start = useMutation({
    mutationFn: () => call(""),
    onSuccess: () => { setErr(""); setInfo("Рассылка поставлена в очередь."); setBody(""); setImage(null); setPreviewed(null); qc.invalidateQueries({ queryKey: ["broadcasts"] }); },
    onError,
  });
  const cancel = useMutation({
    mutationFn: (id: number) => api(`/api/broadcasts/${id}/cancel`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["broadcasts"] }), onError,
  });
  const changed = () => setPreviewed(null);
  const toggle = (id: number) => { setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]); changed(); };
  return (
    <>
      <h1>Рассылки</h1>
      <Card className="card form" style={{ maxWidth: 720 }}>
        <label>Текст (HTML Telegram: b, i, u, s, a, code…; до 4096 символов, с картинкой — до 1024)
          <Textarea rows={6} value={body} onChange={(e) => { setBody(e.target.value); changed(); }} />
        </label>
        <label>Картинка (необязательно)
          <input className="styled-file" type="file" accept="image/jpeg,image/png,image/webp" onChange={(e) => { setImage(e.target.files?.[0] ?? null); changed(); }} />
        </label>
        <div>
          <span className="muted">Обложки (ничего не выбрано — все включённые):</span>
          <div className="perms">
            {covers.data?.map((c) => (
              <label key={c.id} className="shift">
                <CustomCheckbox checked={picked.includes(c.id)} onChange={() => toggle(c.id)} />
                {c.slug}{!c.enabled && " (выключена)"}
              </label>
            ))}
          </div>
        </div>
        {info && <p className="ok-text">{info}</p>}
        {err && <p className="error">{err}</p>}
        <div className="row-actions">
          <Button className="ghost" disabled={!body.trim() || preview.isPending} onClick={() => preview.mutate()}>Предпросмотр</Button>
          <Button className="ghost" disabled={!body.trim() || test.isPending} onClick={() => test.mutate()}>Отправить себе</Button>
          <Button disabled={previewed === null || previewed === 0 || start.isPending}
            onClick={() => confirm(`Запустить рассылку на ${previewed} получателей?`) && start.mutate()}>Запустить</Button>
        </div>
      </Card>
      <h2>История</h2>
      <Table>
        <TableHeader><TableRow><TableHead>#</TableHead><TableHead>Текст</TableHead><TableHead>Обложки</TableHead><TableHead>Прогресс</TableHead><TableHead>Статус</TableHead><TableHead /></TableRow></TableHeader>
        <TableBody>
          {list.isLoading && <TableSkeletonRows rows={4} cols={6} />}
          {list.data?.map((b) => (
            <TableRow key={b.id}>
              <TableCell>{b.id}<div className="muted">{fmtDate(b.created_at)}</div></TableCell>
              <TableCell className="wrap">{b.has_image && "🖼 "}{b.body.slice(0, 100)}</TableCell>
              <TableCell>{b.covers.length ? b.covers.join(", ") : "все"}</TableCell>
              <TableCell>
                {b.sent + b.failed} / {b.total} · доставлено {b.sent}, ошибок {b.failed}
                {Object.entries(b.fail_reasons).map(([r, n]) => <div key={r} className="err-text">{r}: {n}</div>)}
              </TableCell>
              <TableCell><span className={`badge ${STATUS[b.status]?.cls ?? "off"}`}>{STATUS[b.status]?.label ?? b.status}</span></TableCell>
              <TableCell>{(b.status === "queued" || b.status === "running") && (
                <Button className="ghost" onClick={() => confirm("Отменить рассылку?") && cancel.mutate(b.id)}>Отменить</Button>
              )}</TableCell>
            </TableRow>
          ))}
          {list.data?.length === 0 && <TableRow><TableCell colSpan={6} className="muted">Рассылок ещё не было</TableCell></TableRow>}
        </TableBody>
      </Table>
    </>
  );
}
