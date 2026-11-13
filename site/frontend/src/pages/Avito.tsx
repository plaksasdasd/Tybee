import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { CustomCheckbox } from "@/Controls";
import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json } from "../api";
import { TableSkeletonRows } from "../Skeletons";
import { toast } from "../Toaster";

type Profile = {
  id: number; slug: string; title: string; client_id: string; account_id: number | null; enabled: boolean;
  last_error: string | null; clients: number; webhook_subscribed: boolean; updated_at: string | null;
};
type List = { key_configured: boolean; webhook_base: string | null; profiles: Profile[] };
type Secret = { slug: string; webhook_secret: string; webhook_url: string };

function ProfileForm({ initial, onDone }: { initial?: Profile; onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ slug: "", title: initial?.title ?? "", client_id: initial?.client_id ?? "", client_secret: "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const save = useMutation({
    mutationFn: () => {
      if (!initial) return api("/api/avito/profiles", json("POST", f));
      const body: Record<string, string> = { title: f.title, client_id: f.client_id };
      if (f.client_secret) body.client_secret = f.client_secret;
      return api(`/api/avito/profiles/${initial.id}`, json("PATCH", body));
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["avito"] }); onDone(); toast(initial ? "Профиль обновлён" : "Профиль создан"); },
    onError: (e: Error) => setErr(e.message),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(); };
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onDone(); }}><DialogContent className="app-dialog" aria-describedby={undefined}>
      <form className="form" onSubmit={submit}>
        <DialogTitle>{initial ? `Ключи профиля ${initial.slug}` : "Новый профиль Avito"}</DialogTitle>
        {!initial && <label>Slug (a-z, 0-9, _ -)<Input value={f.slug} onChange={set("slug")} pattern="[a-z0-9_-]{1,32}" required /></label>}
        <label>Название<Input value={f.title} onChange={set("title")} maxLength={128} required /></label>
        <label>client_id<Input value={f.client_id} onChange={set("client_id")} maxLength={128} required /></label>
        <label>client_secret{initial && " (пусто — не менять)"}
          <Input type="password" autoComplete="new-password" value={f.client_secret} onChange={set("client_secret")} maxLength={256} required={!initial} />
        </label>
        {err && <p className="error">{err}</p>}
        <div className="row-actions">
          <Button type="button" className="ghost" onClick={onDone}>Отмена</Button>
          <Button disabled={save.isPending}>Сохранить</Button>
        </div>
      </form>
    </DialogContent></Dialog>
  );
}

export default function Avito() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["avito"], queryFn: () => api<List>("/api/avito/profiles") });
  const [modal, setModal] = useState<"" | "new" | Profile>("");
  const [err, setErr] = useState("");
  const [secret, setSecret] = useState<Secret | null>(null);
  const act = useMutation({
    mutationFn: ({ p, path, body }: { p: Profile; path: string; body?: unknown }) =>
      body !== undefined ? api(`/api/avito/profiles/${p.id}${path}`, json("PATCH", body)) : api(`/api/avito/profiles/${p.id}${path}`, { method: "POST" }),
    onSuccess: (data: any, variables) => {
      setErr("");
      if (data?.webhook_secret) setSecret({ slug: data.slug, webhook_secret: data.webhook_secret, webhook_url: data.webhook_url });
      qc.invalidateQueries({ queryKey: ["avito"] });
      if (!data?.webhook_secret) toast(variables.path.endsWith("/check") ? "Проверка выполнена" : "Профиль обновлён");
    },
    onError: (e: Error) => { setErr(e.message); qc.invalidateQueries({ queryKey: ["avito"] }); },
  });
  const d = q.data;
  return (
    <>
      <div className="head">
        <h1>Avito</h1>
        <Button disabled={!d?.key_configured} onClick={() => setModal("new")}>Добавить профиль</Button>
      </div>
      {d && !d.key_configured && (
        <p className="error">AVITO_SECRET_KEY не задан или неверный: профили нельзя создавать и использовать. Задайте ключ Fernet в .env обоих процессов (bot и site).</p>
      )}
      {d && !d.webhook_base && <p className="muted">AVITO_WEBHOOK_URL не задан — подписать вебхук не получится.</p>}
      {err && <p className="error">{err}</p>}
      {secret && (
        <Card className="card">
          <b>Вебхук профиля {secret.slug} подписан.</b>
          <p className="muted">Секрет показывается один раз и нигде не хранится (в базе — только его хэш).</p>
          <pre className="details">{secret.webhook_url}</pre>
          <Button className="ghost" onClick={() => setSecret(null)}>Скрыть</Button>
        </Card>
      )}
      <Table>
        <TableHeader><TableRow><TableHead>Профиль</TableHead><TableHead>Аккаунт</TableHead><TableHead>Клиентов</TableHead><TableHead>Вебхук</TableHead><TableHead>Включён</TableHead><TableHead /></TableRow></TableHeader>
        <TableBody>
          {q.isLoading && <TableSkeletonRows rows={4} cols={6} />}
          {d?.profiles.map((p) => (
            <TableRow key={p.id}>
              <TableCell><b>{p.title}</b><div className="muted">{p.slug} · client_id {p.client_id}</div>
                {p.last_error && <div className="err-text">{p.last_error}</div>}</TableCell>
              <TableCell>{p.account_id ?? <span className="muted">не проверен</span>}</TableCell>
              <TableCell>{p.clients}</TableCell>
              <TableCell>{p.webhook_subscribed ? <span className="badge ok">подписан</span> : <span className="badge off">нет</span>}</TableCell>
              <TableCell>
                <label className="switch">
                  <CustomCheckbox checked={p.enabled}
                    onChange={(e) => (e.target.checked || confirm(`Выключить профиль ${p.slug}? Ответы его клиентам перестанут доставляться.`)) && act.mutate({ p, path: "", body: { enabled: e.target.checked } })} />
                  <span />
                </label>
              </TableCell>
              <TableCell>
                <div className="row-actions">
                  <Button className="ghost" disabled={act.isPending} onClick={() => act.mutate({ p, path: "/check" })}>Проверить</Button>
                  <Button className="ghost" disabled={act.isPending || !d.webhook_base}
                    onClick={() => (!p.webhook_subscribed || confirm("Подписать заново? Старый адрес вебхука перестанет работать.")) && act.mutate({ p, path: "/subscribe" })}>
                    Подписать вебхук
                  </Button>
                  <Button className="ghost" onClick={() => setModal(p)}>Изменить ключи</Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
          {d && d.profiles.length === 0 && <TableRow><TableCell colSpan={6} className="muted">Профилей нет</TableCell></TableRow>}
        </TableBody>
      </Table>
      {modal === "new" && <ProfileForm onDone={() => setModal("")} />}
      {modal && modal !== "new" && <ProfileForm initial={modal} onDone={() => setModal("")} />}
    </>
  );
}
