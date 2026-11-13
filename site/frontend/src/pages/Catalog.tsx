import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { CustomCheckbox } from "@/Controls";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { FormEvent, Fragment, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json } from "../api";
import { PageSkeleton, SkeletonCards, TableSkeletonRows } from "../Skeletons";
import { toast } from "../Toaster";

type Product = { id: number; article: string; title: string; price: number; description: string | null; available: boolean; has_photo: boolean };
type Page = { items: Product[]; total: number; page: number; pages: number };
type Box = { id: number; title: string; price: number; available: boolean };
type ImportResult = { added: number; updated: number; skipped: number; truncated: boolean; errors: { row: number; reason: string }[] };
type ImportProduct = Pick<Product, "article" | "title" | "price" | "description" | "available">;
type ImportPreview = { new_count: number; errors: { row: number; reason: string }[]; conflicts: { article: string; current: ImportProduct; incoming: ImportProduct }[] };
type Choice = "current" | "xlsx";
type Stats = {
  users: number; banned: number; products: number; boxes: number; orders: number;
  by_status: { status: string; label: string; count: number }[];
  covers: { slug: string; enabled: boolean; clients: number; orders: number; paid_orders: number }[];
};

function ProductForm({ initial, onDone }: { initial?: Product; onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({
    article: initial?.article ?? "", title: initial?.title ?? "", price: String(initial?.price ?? ""),
    description: initial?.description ?? "", available: initial?.available ?? true,
  });
  const [photo, setPhoto] = useState<File | null>(null);
  const created = useRef<Product | null>(null);
  const [err, setErr] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const save = useMutation({
    mutationFn: async () => {
      const body = { ...f, price: Number(f.price), description: f.description || null };
      const existing = initial ?? created.current;
      const p = await api<Product>(existing ? `/api/catalog/products/${existing.id}` : "/api/catalog/products", json(existing ? "PATCH" : "POST", body));
      if (!initial) created.current = p;
      if (photo) {
        const form = new FormData();
        form.append("file", photo);
        await api(`/api/catalog/products/${p.id}/photo`, { method: "POST", body: form });
      }
      return p;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["catalog-products"] }); onDone(); toast(initial ? "Товар сохранён" : "Товар добавлен"); },
    onError: (e: Error) => {
      qc.invalidateQueries({ queryKey: ["catalog-products"] });
      setErr(created.current ? `Товар уже создан, но сохранение фото не удалось: ${e.message}. Исправьте фото и повторите.` : e.message);
    },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(); };
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onDone(); }}><DialogContent className="app-dialog" aria-describedby={undefined}>
      <form className="form" onSubmit={submit}>
        <DialogTitle>{initial ? `Товар ${initial.article}` : "Новый товар"}</DialogTitle>
        <label>Артикул (6 цифр)<Input value={f.article} onChange={set("article")} pattern="\d{6}" required /></label>
        <label>Название<Input value={f.title} onChange={set("title")} maxLength={256} required /></label>
        <label>Цена, ₽<Input type="number" min={1} max={1000000000} value={f.price} onChange={set("price")} required /></label>
        <label>Описание<Textarea rows={3} value={f.description} onChange={set("description")} maxLength={2000} /></label>
        <label className="shift"><CustomCheckbox checked={f.available} onChange={(e) => setF({ ...f, available: e.target.checked })} /> В продаже</label>
        <label>Фото (JPEG, PNG, WebP до 10 МБ){initial?.has_photo && " — заменит текущее"}
          <input type="file" accept="image/jpeg,image/png,image/webp" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
        </label>
        {initial?.has_photo && <img className="product-photo" src={`/api/catalog/products/${initial.id}/photo?v=${Date.now()}`} alt="" onError={(e) => ((e.target as HTMLImageElement).style.display = "none")} />}
        {err && <p className="error">{err}</p>}
        <div className="row-actions">
          <Button type="button" className="ghost" onClick={onDone}>Отмена</Button>
          <Button disabled={save.isPending}>Сохранить</Button>
        </div>
      </form>
    </DialogContent></Dialog>
  );
}

function Products() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const tableTop = useRef<HTMLDivElement>(null);
  const [modal, setModal] = useState<"" | "new" | Product>("");
  const [err, setErr] = useState("");
  const [report, setReport] = useState<ImportResult | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [pendingImport, setPendingImport] = useState<{ file: File; preview: ImportPreview } | null>(null);
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [importError, setImportError] = useState("");
  const list = useQuery({
    queryKey: ["catalog-products", q, page],
    queryFn: () => api<Page>(`/api/catalog/products?q=${encodeURIComponent(q)}&page=${page}`),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["catalog-products"] });
  const act = useMutation({
    mutationFn: ({ p, del }: { p: Product; del?: boolean }) =>
      del ? api(`/api/catalog/products/${p.id}`, { method: "DELETE" }) : api(`/api/catalog/products/${p.id}`, json("PATCH", { available: !p.available })),
    onSuccess: (_data, variables) => { setErr(""); refresh(); toast(variables.del ? "Товар удалён" : "Наличие обновлено"); },
    onError: (e: Error) => setErr(e.message),
  });
  const applyImport = useMutation({
    mutationFn: ({ file, selected }: { file: File; selected: Record<string, Choice> }) => {
      const form = new FormData(); form.append("file", file); form.append("choices", JSON.stringify(selected));
      return api<ImportResult>("/api/catalog/import", { method: "POST", body: form });
    },
    onSuccess: (r) => { setErr(""); setImportError(""); setPendingImport(null); setReport(r); refresh(); toast(`Импорт применён: добавлено ${r.added}, обновлено ${r.updated}`); },
    onError: (e: Error) => { setImportError(e.message); setErr(e.message); },
  });
  const importFile = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData(); form.append("file", file);
      return { file, preview: await api<ImportPreview>("/api/catalog/import/preview", { method: "POST", body: form }) };
    },
    onSuccess: ({ file, preview }) => {
      setErr(""); setReport(null); setImportError(""); setChoices({});
      if (preview.conflicts.length) setPendingImport({ file, preview });
      else applyImport.mutate({ file, selected: {} });
    },
    onError: (e: Error) => { setReport(null); setErr(e.message); },
  });
  const d = list.data;
  const changePage = (next: number) => {
    setPage(next);
    tableTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  };
  const pageNumbers = d ? Array.from(new Set([1, d.pages, page - 1, page, page + 1])).filter((n) => n >= 1 && n <= d.pages).sort((a, b) => a - b) : [];
  return (
    <>
      <div className="filters">
        <Input type="search" placeholder="Артикул или название" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <span style={{ flex: 1 }} />
        <input ref={fileInput} type="file" accept=".xlsx" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile.mutate(f); e.target.value = ""; }} />
        <Button className="ghost" disabled={importFile.isPending || applyImport.isPending} onClick={() => fileInput.current?.click()}>Импорт XLSX</Button>
        <Button asChild className="ghost catalog-export"><a href="/api/catalog/export" download="catalog.xlsx">Экспорт XLSX</a></Button>
        <Button onClick={() => setModal("new")}>Добавить товар</Button>
      </div>
      <details className="card catalog-import-help">
        <summary>📥 Импорт артикулов из Excel</summary>
        <p>Загрузите .xlsx файл, до 500 строк за раз. Первая строка — заголовки, можно без.</p>
        <p><code>Артикул | Название | Цена | Описание | Доступен</code></p>
        <ul>
          <li>Артикул — 6 цифр, обязательный и уникальный.</li>
          <li>Название — обязательный текст. Цена — обязательное число в ₽.</li>
          <li>Описание — необязательно. Доступен — да/нет, 1/0, true/false, по умолчанию — да.</li>
        </ul>
        <p>Пример: <code>482915 | Куртка зимняя | 6900 | Тёплый пух, S–XL | да</code></p>
        <p className="muted">Фото добавляется отдельно через карточку товара. Совпавшие артикулы можно сравнить и выбрать перед импортом.</p>
      </details>
      {err && <p className="error">{err}</p>}
      {report && (
        <Card className="card">
          <b>Импорт:</b> добавлено {report.added}, обновлено {report.updated}, оставлено из базы {report.skipped}, ошибок {report.errors.length}
          {report.errors.length > 0 && <ul>{report.errors.map((e) => <li key={e.row}>Строка {e.row}: {e.reason}</li>)}</ul>}
          <Button className="link" style={{ marginLeft: 12 }} onClick={() => setReport(null)}>Скрыть</Button>
        </Card>
      )}
      <div ref={tableTop} />
      <Table>
        <TableHeader><TableRow><TableHead>Артикул</TableHead><TableHead>Название</TableHead><TableHead>Цена</TableHead><TableHead>Фото</TableHead><TableHead>В продаже</TableHead><TableHead /></TableRow></TableHeader>
        <TableBody>
          {list.isLoading && <TableSkeletonRows rows={6} cols={6} />}
          {d?.items.map((p) => (
            <TableRow key={p.id}>
              <TableCell><code>{p.article}</code></TableCell>
              <TableCell className="wrap">{p.title}{p.description && <div className="muted">{p.description.slice(0, 120)}</div>}</TableCell>
              <TableCell>{p.price.toLocaleString("ru-RU")} ₽</TableCell>
              <TableCell>{p.has_photo ? "есть" : <span className="muted">нет</span>}</TableCell>
              <TableCell>
                <Switch checked={p.available} onCheckedChange={() => act.mutate({ p })} aria-label={`В продаже: ${p.title}`} />
              </TableCell>
              <TableCell><div className="row-actions">
                <Button className="ghost sm" onClick={() => setModal(p)}>Изменить</Button>
                <Button className="danger sm" onClick={() => confirm(`Удалить товар ${p.article}?`) && act.mutate({ p, del: true })}>Удалить</Button>
              </div></TableCell>
            </TableRow>
          ))}
          {d && d.items.length === 0 && <TableRow><TableCell colSpan={6} className="muted">Товаров нет</TableCell></TableRow>}
        </TableBody>
      </Table>
      {d && d.pages > 1 && (
        <nav className="catalog-pagination" aria-label="Страницы товаров">
          <Button className="ghost" disabled={page <= 1} onClick={() => changePage(page - 1)}>← Назад</Button>
          {pageNumbers.map((number, index) => <Fragment key={number}>
            {index > 0 && number - pageNumbers[index - 1] > 1 && <span aria-hidden="true">…</span>}
            <Button className={number === page ? "catalog-page-current" : "ghost"} aria-label={`Страница ${number}`} aria-current={number === page ? "page" : undefined} onClick={() => changePage(number)}>{number}</Button>
          </Fragment>)}
          <Button className="ghost" disabled={page >= d.pages} onClick={() => changePage(page + 1)}>Далее →</Button>
          <span className="muted">Всего товаров: {d.total}</span>
        </nav>
      )}
      {pendingImport && <Dialog open onOpenChange={(open) => { if (!open && !applyImport.isPending) setPendingImport(null); }}>
        <DialogContent className="app-dialog catalog-import-dialog" aria-describedby="catalog-import-description" showCloseButton={!applyImport.isPending}>
          <DialogTitle>Совпавшие артикулы · {pendingImport.preview.conflicts.length}</DialogTitle>
          <p id="catalog-import-description" className="muted">Выберите вариант для каждого товара. Новых товаров: {pendingImport.preview.new_count}. Изменения сохранятся после нажатия «Импортировать».</p>
          <Button className="ghost" disabled={applyImport.isPending} onClick={() => setChoices(Object.fromEntries(pendingImport.preview.conflicts.map((c) => [c.article, "xlsx" as Choice])))}>Всё из XLSX</Button>
          <div className="catalog-import-conflicts">
            {pendingImport.preview.conflicts.map((conflict) => <section className="catalog-import-conflict" key={conflict.article}>
              <b>Артикул {conflict.article}</b>
              <div className="catalog-import-options">
                {(["current", "xlsx"] as const).map((source) => {
                  const product = source === "current" ? conflict.current : conflict.incoming;
                  return <div className={`catalog-import-option ${choices[conflict.article] === source ? "is-selected" : ""}`} key={source}>
                    <b>{source === "current" ? "Сейчас в базе" : "Из XLSX"}</b>
                    <strong>{product.title}</strong>
                    <span>{product.price.toLocaleString("ru-RU")} ₽</span>
                    <p>{product.description || "Без описания"}</p>
                    <span>Доступен: {product.available ? "да" : "нет"}</span>
                    <Button className="ghost" aria-pressed={choices[conflict.article] === source} disabled={applyImport.isPending} onClick={() => setChoices({ ...choices, [conflict.article]: source })}>
                      {choices[conflict.article] === source ? "✓ Выбрано" : source === "current" ? "Оставить из базы" : "Взять из XLSX"}
                    </Button>
                  </div>;
                })}
              </div>
            </section>)}
          </div>
          {pendingImport.preview.errors.length > 0 && <details><summary>Ошибки в строках: {pendingImport.preview.errors.length}</summary><ul>{pendingImport.preview.errors.map((e) => <li key={e.row}>Строка {e.row}: {e.reason}</li>)}</ul></details>}
          {importError && <p className="error" role="alert">{importError}</p>}
          <div className="row-actions">
            <span className="muted">Выбрано {Object.keys(choices).length} из {pendingImport.preview.conflicts.length}</span>
            <Button className="ghost" disabled={applyImport.isPending} onClick={() => setPendingImport(null)}>Отмена</Button>
            <Button disabled={applyImport.isPending || pendingImport.preview.conflicts.some((c) => !choices[c.article])} onClick={() => applyImport.mutate({ file: pendingImport.file, selected: choices })}>{applyImport.isPending ? "Импорт…" : "Импортировать"}</Button>
          </div>
        </DialogContent>
      </Dialog>}
      {modal === "new" && <ProductForm onDone={() => setModal("")} />}
      {modal && modal !== "new" && <ProductForm initial={modal} onDone={() => setModal("")} />}
    </>
  );
}

function Boxes() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["catalog-boxes"], queryFn: () => api<Box[]>("/api/catalog/boxes") });
  const [f, setF] = useState({ title: "", price: "" });
  const [err, setErr] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["catalog-boxes"] });
  const onError = (e: Error) => setErr(e.message);
  const create = useMutation({
    mutationFn: () => api("/api/catalog/boxes", json("POST", { title: f.title, price: Number(f.price) })),
    onSuccess: () => { setErr(""); setF({ title: "", price: "" }); refresh(); toast("Бокс добавлен"); }, onError,
  });
  const act = useMutation({
    mutationFn: ({ b, body, del }: { b: Box; body?: unknown; del?: boolean }) =>
      del ? api(`/api/catalog/boxes/${b.id}`, { method: "DELETE" }) : api(`/api/catalog/boxes/${b.id}`, json("PATCH", body)),
    onSuccess: (_data, variables) => { setErr(""); refresh(); toast(variables.del ? "Бокс удалён" : "Бокс обновлён"); }, onError,
  });
  return (
    <>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <Input placeholder="Название бокса" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} maxLength={256} required />
        <Input type="number" placeholder="Цена, ₽" min={1} value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} required />
        <Button disabled={create.isPending}>Добавить бокс</Button>
      </form>
      {err && <p className="error">{err}</p>}
      <Table>
        <TableHeader><TableRow><TableHead>Название</TableHead><TableHead>Цена</TableHead><TableHead>В продаже</TableHead><TableHead /></TableRow></TableHeader>
        <TableBody>
          {list.isLoading && <TableSkeletonRows rows={4} cols={4} />}
          {list.data?.map((b) => (
            <TableRow key={b.id}>
              <TableCell className="wrap">{b.title}</TableCell>
              <TableCell>{b.price.toLocaleString("ru-RU")} ₽</TableCell>
              <TableCell><Switch checked={b.available} onCheckedChange={() => act.mutate({ b, body: { available: !b.available } })} aria-label={`В продаже: ${b.title}`} /></TableCell>
              <TableCell>
                <div className="row-actions">
                  <Button className="ghost sm" onClick={() => {
                    const price = prompt("Новая цена, ₽", String(b.price));
                    if (price) act.mutate({ b, body: { price: Number(price) } });
                  }}>Цена</Button>
                  <Button className="danger sm" onClick={() => confirm(`Удалить бокс «${b.title}»?`) && act.mutate({ b, del: true })}>Удалить</Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
          {list.data?.length === 0 && <TableRow><TableCell colSpan={4} className="muted">Боксов нет</TableCell></TableRow>}
        </TableBody>
      </Table>
    </>
  );
}

function StatsTab() {
  const q = useQuery({ queryKey: ["catalog-stats"], queryFn: () => api<Stats>("/api/catalog/stats") });
  const s = q.data;
  if (!s) return <><SkeletonCards count={4} /><PageSkeleton rows={4} cols={2} title={false} /></>;
  return (
    <>
      <div className="cards">
        <Card className="card stat"><small>Клиентов</small><b>{s.users}</b><small>забанено: {s.banned}</small></Card>
        <Card className="card stat"><small>Товаров</small><b>{s.products}</b></Card>
        <Card className="card stat"><small>Боксов</small><b>{s.boxes}</b></Card>
        <Card className="card stat"><small>Заказов</small><b>{s.orders}</b></Card>
      </div>
      <h2>Заказы по статусам</h2>
      <Table><TableBody>{s.by_status.map((r) => <TableRow key={r.status}><TableCell>{r.label}</TableCell><TableCell>{r.count}</TableCell></TableRow>)}</TableBody></Table>
      <h2>По обложкам</h2>
      <Table>
        <TableHeader><TableRow><TableHead>Обложка</TableHead><TableHead>Клиентов</TableHead><TableHead>Заказов</TableHead><TableHead>Оплачено</TableHead></TableRow></TableHeader>
        <TableBody>{s.covers.map((c) => <TableRow key={c.slug}><TableCell>{c.slug}{!c.enabled && <span className="muted"> · выключена</span>}</TableCell><TableCell>{c.clients}</TableCell><TableCell>{c.orders}</TableCell><TableCell>{c.paid_orders}</TableCell></TableRow>)}</TableBody>
      </Table>
    </>
  );
}

export default function Catalog() {
  const [tab, setTab] = useState<"products" | "boxes" | "stats">("products");
  return (
    <>
      <h1>Каталог</h1>
      <Tabs value={tab} onValueChange={(value) => setTab(value as typeof tab)}><TabsList className="app-tabs"><TabsTrigger value="products">Товары</TabsTrigger><TabsTrigger value="boxes">Мистери-боксы</TabsTrigger><TabsTrigger value="stats">Статистика</TabsTrigger></TabsList></Tabs>
      {tab === "products" && <Products />}
      {tab === "boxes" && <Boxes />}
      {tab === "stats" && <StatsTab />}
    </>
  );
}
