import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Switch } from "@/components/ui/switch";
import { CustomSelect } from "../Controls";
import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json, Me, RoleInfo, StaffMember } from "../api";
import { TableSkeletonRows } from "../Skeletons";
import { ShiftLogBlock } from "../Shift";
import { toast } from "../Toaster";

type Invite = { tg_start: string; token: string; role_label: string; position: string | null; display_name: string | null; expires_at: string };
// Real mode: a Telegram deep link, the login bot learns the invitee's id. Dev (no bot): a web page with a manual id field.
const inviteUrl = (i: Pick<Invite, "token" | "tg_start">, bot?: string) =>
  bot ? `https://t.me/${bot}?start=${i.tg_start}` : `${window.location.origin}/invite/${i.token}`;
const useBot = () =>
  useQuery({ queryKey: ["auth-config"], queryFn: () => api<{ bot_username: string }>("/api/auth/config") }).data?.bot_username;

function AddModal({ roles, onClose }: { roles: RoleInfo[]; onClose: () => void }) {
  const qc = useQueryClient();
  const bot = useBot();
  const assignable = roles.filter((r) => r.assignable);
  const [f, setF] = useState({ role: assignable[0]?.code ?? "", display_name: "", position: "" });
  const [err, setErr] = useState("");
  const [link, setLink] = useState("");
  const [copied, setCopied] = useState(false);
  const create = useMutation({
    mutationFn: () => api<Invite>("/api/staff/invites", json("POST", { ...f, display_name: f.display_name || null, position: f.position || null })),
    onSuccess: (inv) => { setLink(inviteUrl(inv, bot)); qc.invalidateQueries({ queryKey: ["invites"] }); toast("Ссылка-приглашение создана"); },
    onError: (e: Error) => setErr(e.message),
  });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const submit = (e: FormEvent) => { e.preventDefault(); setErr(""); create.mutate(); };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setErr("");
      toast("Ссылка скопирована");
    } catch {
      setCopied(false);
      setErr("Не удалось скопировать ссылку. Выделите её в поле выше и скопируйте вручную.");
    }
  };
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}><DialogContent className="app-dialog" aria-describedby={undefined}>
      <form className="form" onSubmit={submit}>
        <DialogTitle>Пригласить сотрудника</DialogTitle>
        {link ? (
          <>
            <p className="muted">Отправьте ссылку сотруднику. Он откроет её, Telegram запустит бота — и сотрудник добавится сам, ID подтянется из Telegram. Ссылка одноразовая, действует 7 дней.</p>
            <Input readOnly value={link} onFocus={(e) => e.target.select()} />
            {err && <p className="error">{err}</p>}
            <div className="row-actions">
              <Button type="button" className="ghost" onClick={onClose}>Закрыть</Button>
              <Button type="button" onClick={copy}>{copied ? "Скопировано ✓" : "Копировать"}</Button>
            </div>
          </>
        ) : (
          <>
            <label>Роль
              <CustomSelect value={f.role} onChange={set("role")}>
                {assignable.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
              </CustomSelect>
            </label>
            <label>Имя (необязательно, иначе возьмём из Telegram)<Input value={f.display_name} onChange={set("display_name")} maxLength={128} /></label>
            <label>Должность<Input value={f.position} onChange={set("position")} maxLength={128} /></label>
            {err && <p className="error">{err}</p>}
            <div className="row-actions">
              <Button type="button" className="ghost" onClick={onClose}>Отмена</Button>
              <Button disabled={create.isPending || !f.role}>Создать ссылку</Button>
            </div>
          </>
        )}
      </form>
    </DialogContent></Dialog>
  );
}

function PendingInvites() {
  const qc = useQueryClient();
  const bot = useBot();
  const q = useQuery({ queryKey: ["invites"], queryFn: () => api<Invite[]>("/api/staff/invites") });
  const revoke = useMutation({
    mutationFn: (t: string) => api(`/api/staff/invites/${t}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["invites"] }); toast("Приглашение отозвано"); },
  });
  if (!q.data?.length) return null;
  return (
    <>
      <h2>Ожидают входа</h2>
      <Table>
        <TableHeader><TableRow><TableHead>Роль</TableHead><TableHead>Имя / должность</TableHead><TableHead>Ссылка</TableHead><TableHead></TableHead></TableRow></TableHeader>
        <TableBody>
          {q.data.map((i) => (
            <TableRow key={i.token}>
              <TableCell>{i.role_label}</TableCell>
              <TableCell className="wrap">{[i.display_name, i.position].filter(Boolean).join(" · ") || "—"}</TableCell>
              <TableCell><Button className="ghost sm" onClick={() => navigator.clipboard?.writeText(inviteUrl(i, bot))}>Копировать ссылку</Button></TableCell>
              <TableCell><Button className="danger sm" onClick={() => revoke.mutate(i.token)}>Отозвать</Button></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
}

export default function Staff() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/auth/me") }).data;
  const [role, setRole] = useState("");
  const [active, setActive] = useState("");
  const [shift, setShift] = useState("");
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<StaffMember | null>(null);
  const [editName, setEditName] = useState("");
  const [editPosition, setEditPosition] = useState("");
  const [editSalary, setEditSalary] = useState("");
  const canManage = !!me?.permissions.includes("staff.manage");
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<RoleInfo[]>("/api/roles") });
  const params = new URLSearchParams();
  if (role) params.set("role", role);
  if (active) params.set("active", active);
  if (shift) params.set("on_shift", shift);
  if (search.trim()) params.set("q", search.trim());
  const list = useQuery({ queryKey: ["staff", params.toString()], queryFn: () => api<StaffMember[]>(`/api/staff?${params}`) });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: object }) => api(`/api/staff/${id}`, json("PATCH", body)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["staff"] });
      qc.invalidateQueries({ queryKey: ["shifts"] });
      toast("Сотрудник обновлён");
    },
  });
  const remove = useMutation({
    mutationFn: (id: number) => api(`/api/staff/${id}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["staff"] }); toast("Сотрудник удалён"); },
  });
  const label = (code: string) => roles.data?.find((r) => r.code === code)?.label ?? code;
  return (
    <>
      <div className="head">
        <h1>Сотрудники</h1>
        {canManage && <Button onClick={() => setAdding(true)}>+ Пригласить</Button>}
      </div>
      <div className="filters">
        <CustomSelect value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="">Все роли</option>
          {roles.data?.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
        </CustomSelect>
        <CustomSelect value={active} onChange={(e) => setActive(e.target.value)}>
          <option value="">Любой статус доступа</option>
          <option value="true">С доступом</option>
          <option value="false">Отключённые</option>
        </CustomSelect>
        <CustomSelect value={shift} onChange={(e) => setShift(e.target.value)}>
          <option value="">Любой статус смены</option>
          <option value="true">На смене</option>
          <option value="false">Не на смене</option>
        </CustomSelect>
        <Input type="search" placeholder="Имя, @username или ID" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>
      {patch.error && <p className="error">{(patch.error as Error).message}</p>}
      {remove.error && <p className="error">{(remove.error as Error).message}</p>}
      {list.error && <p className="error">{(list.error as Error).message}</p>}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Имя</TableHead>
            <TableHead>@username</TableHead>
            <TableHead>Telegram ID</TableHead>
            <TableHead>Роль</TableHead>
            <TableHead>Должность</TableHead>
            <TableHead>Доступ</TableHead>
            <TableHead>На смене</TableHead>
            {canManage && <TableHead></TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {list.isLoading && <TableSkeletonRows rows={6} cols={canManage ? 8 : 7} />}
          {list.data?.map((s) => {
            const locked = !s.manageable;
            const opts = roles.data?.filter((r) => r.assignable || r.code === s.role) ?? [];
            return (
              <TableRow key={s.telegram_id}>
                <TableCell>{s.display_name}</TableCell>
                <TableCell>{s.username ? `@${s.username}` : "—"}</TableCell>
                <TableCell>{s.telegram_id}</TableCell>
                <TableCell title={s.manage_hint ?? undefined}>
                  {canManage ? (
                    <CustomSelect value={s.role} disabled={locked || patch.isPending}
                      onChange={(e) => patch.mutate({ id: s.telegram_id, body: { role: e.target.value } })}>
                      {opts.map((r) => <option key={r.code} value={r.code} disabled={!r.assignable}>{r.label}</option>)}
                    </CustomSelect>
                  ) : label(s.role)}
                </TableCell>
                <TableCell className="wrap">{s.position || "—"}</TableCell>
                <TableCell title={locked ? s.manage_hint ?? undefined : undefined}>
                  <Switch
                    checked={s.is_active}
                    disabled={locked || !canManage || patch.isPending}
                    onCheckedChange={(checked) =>
                      patch.mutate({ id: s.telegram_id, body: { is_active: checked } })
                    }
                    aria-label={`Доступ к панели: ${s.display_name}`}
                  />
                </TableCell>
                <TableCell>
                  <span
                    className={`badge ${s.on_shift ? "ok" : "off"}`}
                    title={s.on_shift ? "Сотрудник на смене" : "Сотрудник не на смене"}
                  >
                    {s.on_shift ? "Активен" : "Не активен"}
                  </span>
                </TableCell>
                {canManage && (
                  <TableCell title={s.manage_hint ?? undefined}>
                    <div className="row-actions">
                      <Button className="ghost sm" disabled={locked} onClick={() => { setEditing(s); setEditName(s.display_name); setEditPosition(s.position ?? ""); setEditSalary(s.base_salary_kop ? String(s.base_salary_kop / 100).replace(".", ",") : ""); }}>Изменить</Button>
                      <Button className="danger sm" disabled={locked || remove.isPending}
                        onClick={() => confirm(`Удалить сотрудника «${s.display_name}»? Его ветки вернутся в очередь.`) && remove.mutate(s.telegram_id)}>
                        Удалить
                      </Button>
                    </div>
                  </TableCell>
                )}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {canManage && <PendingInvites />}
      <ShiftLogBlock title="Смены поддержки" all />
      {adding && roles.data && <AddModal roles={roles.data} onClose={() => setAdding(false)} />}
      {editing && <Dialog open onOpenChange={(open) => { if (!open && !patch.isPending) setEditing(null); }}><DialogContent className="app-dialog" aria-describedby={undefined}>
        <form className="form" onSubmit={(e) => { e.preventDefault(); patch.mutate({ id: editing.telegram_id, body: { display_name: editName, position: editPosition, base_salary: Number(editSalary.replace(",", ".") || 0) } }, { onSuccess: () => setEditing(null) }); }}>
          <DialogTitle>Изменить данные сотрудника</DialogTitle>
          <label>Имя<Input value={editName} onChange={(e) => setEditName(e.target.value)} maxLength={128} required /></label>
          <label>Должность<Input value={editPosition} onChange={(e) => setEditPosition(e.target.value)} maxLength={128} /></label>
          <label>Оклад, ₽ в месяц<Input inputMode="decimal" value={editSalary} placeholder="0" onChange={(e) => setEditSalary(e.target.value)} /></label>
          <p className="muted">Оклад входит в месячный расчёт зарплаты саппорта (раздел «Финансы»).</p>
          <p className="muted">Telegram ID и username определяются аккаунтом Telegram.</p>
          {patch.error && <p className="error">{(patch.error as Error).message}</p>}
          <div className="row-actions"><Button type="button" className="ghost" onClick={() => setEditing(null)}>Отмена</Button><Button disabled={patch.isPending || !editName.trim()}>Сохранить</Button></div>
        </form>
      </DialogContent></Dialog>}
    </>
  );
}
