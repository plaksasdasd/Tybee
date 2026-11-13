import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Switch } from "@/components/ui/switch";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Cover, json } from "../api";
import { Button } from "@/components/ui/button";
import { TableSkeletonRows } from "../Skeletons";
import { toast } from "../Toaster";

export default function CoversList() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["covers"], queryFn: () => api<Cover[]>("/api/covers"), refetchInterval: 10000 });
  const toggle = useMutation({
    mutationFn: (c: Cover) => api(`/api/covers/${c.slug}`, json("PATCH", { enabled: !c.enabled })),
    onSuccess: (_data, c) => { qc.invalidateQueries({ queryKey: ["covers"] }); toast(`Обложка ${c.slug} ${c.enabled ? "выключена" : "включена"}`); },
  });
  const remove = useMutation({
    mutationFn: (slug: string) => api(`/api/covers/${slug}`, { method: "DELETE" }),
    onSuccess: (_data, slug) => { qc.invalidateQueries({ queryKey: ["covers"] }); toast(`Обложка ${slug} удалена`); },
  });
  return (
    <>
      <div className="head">
        <h1>Обложки</h1>
        <Link className="btn" to="/covers/new">+ Создать</Link>
      </div>
      {q.error && <p className="error">{(q.error as Error).message}</p>}
      <Table>
        <TableHeader>
          <TableRow><TableHead>Обложка</TableHead><TableHead>Бот</TableHead><TableHead>Статус</TableHead><TableHead>Клиенты</TableHead><TableHead>Заказы</TableHead><TableHead>Включена</TableHead><TableHead></TableHead></TableRow>
        </TableHeader>
        <TableBody>
          {q.isLoading && <TableSkeletonRows rows={5} cols={7} />}
          {q.data?.length === 0 && <TableRow><TableCell colSpan={7} className="muted">Обложек пока нет</TableCell></TableRow>}
          {q.data?.map((c) => (
            <TableRow key={c.slug}>
              <TableCell><Link to={`/covers/${c.slug}`}>{c.slug}</Link></TableCell>
              <TableCell className="wrap">{c.bot_username ? `@${c.bot_username}` : "—"}</TableCell>
              <TableCell>
                {c.last_error ? <span className="badge bad" title={c.last_error}>ошибка</span>
                  : c.running ? <span className="badge ok">работает</span>
                  : c.enabled ? <span className="badge wait">запускается</span>
                  : <span className="badge off">выключена</span>}
                {c.last_error && <div className="err-text">{c.last_error}</div>}
              </TableCell>
              <TableCell>{c.clients}</TableCell>
              <TableCell>{c.orders}</TableCell>
              <TableCell>
                <Switch checked={c.enabled} onCheckedChange={() => toggle.mutate(c)} aria-label={`Включить обложку ${c.slug}`} />
              </TableCell>
              <TableCell><Button className="danger sm" disabled={remove.isPending} onClick={() => confirm(`Удалить обложку «${c.slug}»? Бот остановится, история и файлы сохранятся.`) && remove.mutate(c.slug)}>Удалить</Button></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {toggle.error && <p className="error">{(toggle.error as Error).message}</p>}
      {remove.error && <p className="error">{(remove.error as Error).message}</p>}
      <p className="muted">Состояние обновляется каждые 10 секунд; бот подхватывает изменения сам.</p>
    </>
  );
}
