import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, json } from "../api";

export default function CoverNew() {
  const [f, setF] = useState({ slug: "", token: "", brand_name: "", template: "default" });
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const qc = useQueryClient();
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErrors([]);
    try {
      await api("/api/covers", json("POST", f));
      await qc.invalidateQueries({ queryKey: ["covers"] });
      nav(`/covers/${f.slug}`);
    } catch (err) {
      const a = err as ApiError;
      setErrors(a.errors?.length ? a.errors : [a.message]);
    } finally { setBusy(false); }
  };

  return (
    <form className="card form" onSubmit={submit}>
      <h1>Новая обложка</h1>
      <label>Slug (a-z, 0-9, _ и -, до 32)<Input value={f.slug} onChange={set("slug")} required pattern="[a-z0-9_-]{1,32}" /></label>
      <label>Токен бота (из @BotFather)<Input value={f.token} onChange={set("token")} required autoComplete="off" /></label>
      <label>Название бренда<Input value={f.brand_name} onChange={set("brand_name")} required /></label>
      <label>Шаблон (slug существующей обложки)<Input value={f.template} onChange={set("template")} required /></label>
      {errors.map((m, i) => <p key={i} className="error">{m}</p>)}
      <Button disabled={busy}>{busy ? "Создаю…" : "Создать"}</Button>
      <p className="muted">Тексты, кнопки и баннеры копируются из шаблона; потом их можно изменить. Бот запустится в течение ~10 секунд.</p>
    </form>
  );
}
