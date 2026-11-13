import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json, fmtDate } from "../api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card } from "@/components/ui/card";
import { ListSkeleton } from "../Skeletons";
import { toast } from "../Toaster";
export type SupportTemplate = { id: number; title: string; body: string; is_active: boolean; updated_at: string | null };
const empty = { title: "", body: "", is_active: true };
export default function SupportTemplates() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [form, setForm] = useState(empty);
  const list = useQuery({ queryKey: ["support-templates", "manage"], queryFn: () => api<SupportTemplate[]>("/api/support/templates?manage=true") });
  const save = useMutation({
    mutationFn: () => api(`/api/support/templates${editing === null ? "" : `/${editing}`}`, json(editing === null ? "POST" : "PUT", form)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["support-templates"] }); setEditing(null); setForm(empty); toast("Шаблон сохранён"); },
  });
  const rows = (list.data ?? []).filter((t) => `${t.title} ${t.body}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  return <>
    <div className="head"><h1>Шаблоны ответов</h1><Button onClick={() => { setEditing(null); setForm(empty); }}>Новый шаблон</Button></div>
    <p className="muted">Общие ответы команды. Саппорты вставляют активные шаблоны в редактор и проверяют текст перед отправкой.</p>
    <div className="template-workspace">
      <Card className="card"><Input type="search" aria-label="Поиск шаблонов" placeholder="Найти шаблон" value={search} onChange={(e) => setSearch(e.target.value)} />
        {list.error && <p className="error">{list.error.message}</p>}
        {list.isLoading && <ListSkeleton rows={5} />}
        {rows.map((t) => <button className={`template-row ${editing === t.id ? "active" : ""}`} key={t.id} onClick={() => { setEditing(t.id); setForm({ title: t.title, body: t.body, is_active: t.is_active }); save.reset(); }}>
          <b>{t.title}</b><small className="muted">{t.is_active ? "Активен" : "В архиве"} · {fmtDate(t.updated_at)}</small><span>{t.body}</span>
        </button>)}
        {!list.isLoading && !rows.length && <p className="muted">Шаблоны не найдены</p>}
      </Card>
      <Card className="card"><form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <h2>{editing === null ? "Новый шаблон" : "Редактирование"}</h2>
        <label>Название<Input required maxLength={120} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
        <label>Текст<Textarea required rows={10} maxLength={3500} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} /></label>
        <p className="muted">Переменные: {"{client_name}, {order_id}, {order_title}, {order_price}, {order_status}"}. Для данных заказа сотрудник выбирает заказ в чате. Незаданные значения требуют заполнения.</p>
        <label className="row-actions"><input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> Доступен в чате</label>
        {save.error && <p className="error">{save.error.message}</p>}
        <Button disabled={save.isPending || !form.title.trim() || !form.body.trim()}>{save.isPending ? "Сохраняем…" : "Сохранить"}</Button>
      </form></Card>
    </div>
  </>;
}
