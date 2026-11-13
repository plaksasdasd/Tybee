import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { CustomCheckbox } from "@/Controls";
import { CustomSelect } from "../Controls";
import { DragEvent, ReactNode, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, CoverConfig, json, KeySpec, Spec } from "../api";
import { PageSkeleton } from "../Skeletons";
import {
  BANNER_LABELS, bannerUsage, BUTTON_LABELS, buttonUsage, KEYBOARDS, SAMPLE, STATUS_LABELS, TEXT_GROUPS, TEXT_META,
} from "../coverMeta";
import { fillPlain, fillPlaceholders, phChip, TgButton, TgEditHandle, TgMessage, visibleLength } from "../tg";
import { EmojiPicker } from "../emoji";

type Tab = "visual" | "main" | "texts" | "buttons" | "statuses" | "banners";
type Section = "texts" | "buttons" | "statuses";
const SECTIONS: Section[] = ["texts", "buttons", "statuses"];
const TABS: [Tab, string][] = [["visual", "Интерфейс бота"], ["main", "Основное"], ["texts", "Тексты"], ["buttons", "Кнопки"], ["statuses", "Статусы"], ["banners", "Баннеры"]];

// Server errors look like "config.yaml: texts.WELCOME: ..." — map them to fields.
const errorsFor = (errors: string[], section: string, key: string) =>
  errors.filter((e) => e.includes(`${section}.${key}:`)).map((m) => m.replace(/^config\.yaml: [a-z]+\.\w+: /, ""));
const fieldId = (section: string, key: string) => `f-${section}-${key}`;

// ------------------------------------------------------------- drag & drop
// A drag that started inside the page (moving a selection between fields) keeps
// the browser's native behaviour; only things dragged in from outside replace.
let internalDrag = false;
if (typeof document !== "undefined") {
  document.addEventListener("dragstart", () => { internalDrag = true; });
  document.addEventListener("dragend", () => { internalDrag = false; });
  document.addEventListener("drop", () => { internalDrag = false; });
}

type DragKind = "image" | "file" | "text";
function dragKind(dt: DataTransfer | null): DragKind | null {
  if (!dt) return null;
  const file = Array.from(dt.items ?? []).find((i) => i.kind === "file");
  if (file) return file.type.startsWith("image/") ? "image" : "file";
  if (dt.types.includes("Files")) return "file";
  if (dt.types.includes("text/plain") && !internalDrag) return "text";
  return null;
}

function useDrop(accept: (k: DragKind) => boolean, onDrop: (dt: DataTransfer, k: DragKind) => void) {
  const [over, setOver] = useState<DragKind | null>(null);
  const depth = useRef(0);
  const ok = (e: DragEvent) => { const k = dragKind(e.dataTransfer); return k && accept(k) ? k : null; };
  return {
    over,
    props: {
      onDragEnter: (e: DragEvent) => { const k = ok(e); if (!k) return; e.preventDefault(); e.stopPropagation(); depth.current++; setOver(k); },
      onDragOver: (e: DragEvent) => { if (!ok(e)) return; e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = "copy"; },
      onDragLeave: (e: DragEvent) => {
        if (depth.current === 0) return;
        e.stopPropagation();
        depth.current--;
        if (!depth.current) setOver(null);
      },
      onDrop: (e: DragEvent) => {
        const k = ok(e);
        depth.current = 0; setOver(null);
        if (!k) return;
        e.preventDefault(); e.stopPropagation();
        onDrop(e.dataTransfer, k);
      },
    },
  };
}

async function droppedText(dt: DataTransfer, kind: DragKind): Promise<string> {
  if (kind === "text") return dt.getData("text/plain");
  const file = dt.files[0];
  if (!file) throw new Error("файл не найден");
  if (file.size > 256 * 1024) throw new Error("файл слишком большой для текста");
  const text = await file.text();
  if (text.includes("\u0000")) throw new Error("это не текстовый файл");
  return text.replace(/\r\n?/g, "\n").replace(/\n$/, "");
}

/** Replace a field's whole value through the editing stack, so Ctrl+Z brings the old text back. */
function replaceAll(el: HTMLTextAreaElement | HTMLInputElement | null, text: string, fallback: (v: string) => void) {
  if (el) {
    el.focus();
    el.select();
    if (document.execCommand("insertText", false, text)) return;
  }
  fallback(text);
}

// ------------------------------------------------------------- banners
async function toPng(file: Blob): Promise<Blob> {
  const bmp = await createImageBitmap(file);
  const c = document.createElement("canvas");
  c.width = bmp.width; c.height = bmp.height;
  c.getContext("2d")!.drawImage(bmp, 0, 0);
  return new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("не удалось перевести в PNG"))), "image/png"));
}

type BannerState = { url?: string; busy?: boolean; error?: string; done?: number };

function useBanners(slug: string, maxBytes: number) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["banners", slug],
    queryFn: () => api<{ name: string; version: string }[]>(`/api/covers/${slug}/banners`),
  });
  const [local, setLocal] = useState<Record<string, BannerState>>({});
  const version = Object.fromEntries((list.data ?? []).map((b) => [b.name, b.version]));
  const src = (n: string) => local[n]?.url ?? `/api/covers/${slug}/banners/${n}?v=${version[n] ?? "0"}`;
  const set = (n: string, s: BannerState) => setLocal((cur) => ({ ...cur, [n]: s }));

  const upload = async (name: string, file: File) => {
    const prevUrl = local[name]?.url;
    try {
      if (!file.type.startsWith("image/")) throw new Error("это не картинка");
      const blob = file.type === "image/png" ? file : await toPng(file);
      if (blob.size > maxBytes) throw new Error(`файл больше ${Math.round(maxBytes / 1048576)} МБ`);
      const url = URL.createObjectURL(blob);
      set(name, { url, busy: true });
      const fd = new FormData();
      fd.append("file", blob, `${name}.png`);
      await api(`/api/covers/${slug}/banners/${name}`, { method: "PUT", body: fd });
      set(name, { url, done: Date.now() });
      if (prevUrl) URL.revokeObjectURL(prevUrl);
      qc.invalidateQueries({ queryKey: ["banners", slug] });
    } catch (e: any) {
      set(name, { url: prevUrl, error: e.message });
    }
  };
  return { src, state: local, upload };
}
type Banners = ReturnType<typeof useBanners>;

/** "Welcome (1).png" -> "welcome"; longest banner name the file name starts with. */
function matchBanner(fileName: string, names: string[]) {
  const base = fileName.toLowerCase().replace(/\.[a-z0-9]+$/, "").replace(/[\s-]+/g, "_");
  return [...names].sort((a, b) => b.length - a.length).find((n) => base === n || base.startsWith(n)) ?? null;
}

// ------------------------------------------------------------- text field
type Tool = { title: string; label: string; cls?: string; open: string; close: string; hint?: string; key?: string; cmd?: string };
const TOOLS: Tool[] = [
  { title: "Жирный (Ctrl+B)", label: "B", cls: "b", open: "<b>", close: "</b>", key: "b", cmd: "bold" },
  { title: "Курсив (Ctrl+I)", label: "I", cls: "i", open: "<i>", close: "</i>", key: "i", cmd: "italic" },
  { title: "Подчёркнутый (Ctrl+U)", label: "U", cls: "u", open: "<u>", close: "</u>", key: "u", cmd: "underline" },
  { title: "Зачёркнутый", label: "S", cls: "s", open: "<s>", close: "</s>", cmd: "strikeThrough" },
  { title: "Моноширинный (копируется по нажатию)", label: "</>", cls: "mono", open: "<code>", close: "</code>" },
  { title: "Скрытый текст (спойлер)", label: "▒", open: "<tg-spoiler>", close: "</tg-spoiler>" },
  { title: "Цитата", label: "❝", open: "<blockquote>", close: "</blockquote>" },
  { title: "Ссылка", label: "🔗", open: '<a href="https://">', close: "</a>", hint: "текст ссылки" },
];
const escHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** HTML of the current selection (for wrapping it inside the editable preview). */
function selectionHtml() {
  const sel = window.getSelection();
  if (!sel?.rangeCount || sel.isCollapsed) return "";
  const d = document.createElement("div");
  d.appendChild(sel.getRangeAt(0).cloneContents());
  return d.innerHTML;
}

// ------------------------------------------------------------- banner inside the preview
function BannerSlot({ src, onFile, busy, error, done }: {
  src: string; onFile: (f: File) => void; busy?: boolean; error?: string; done?: number;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [hover, setHover] = useState(false);
  const drop = useDrop((k) => k === "image" || k === "file", (dt) => { const f = dt.files[0]; if (f) onFile(f); });

  // Ctrl+V with an image in the clipboard while the cursor is over the banner.
  useEffect(() => {
    if (!hover) return;
    const paste = (e: ClipboardEvent) => {
      const file = Array.from(e.clipboardData?.files ?? []).find((f) => f.type.startsWith("image/"));
      if (!file) return;
      e.preventDefault();
      onFile(file);
    };
    window.addEventListener("paste", paste);
    return () => window.removeEventListener("paste", paste);
  }, [hover, onFile]);

  return (
    <div
      className={"tg-image slot" + (drop.over ? " drag" : "") + (busy ? " busy" : "") + (error ? " failed" : "")}
      role="button" tabIndex={0} title="Перетащите картинку, нажмите или вставьте Ctrl+V, чтобы заменить баннер"
      {...drop.props}
      onClick={() => input.current?.click()}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
    >
      <img src={src} alt="" />
      <span className="slot-over">
        {busy ? "Загружаем…" : drop.over ? "Отпустите — заменим баннер" : "Перетащите картинку, нажмите или Ctrl+V"}
      </span>
      {done && !busy && <span key={done} className="banner-done">Заменено ✓</span>}
      {error && <span className="slot-error">{error}</span>}
      <input ref={input} type="file" accept="image/*" hidden
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
    </div>
  );
}

function TextField({
  section, k, value, original, ks, label, hint, max, caption, errors, onChange, preview,
}: {
  section: Section; k: string; value: string; original: string; ks?: KeySpec; label: string; hint?: string;
  max: number | null; caption: boolean; errors: string[]; onChange: (v: string) => void;
  preview: {
    values: Record<string, string>; image?: string | null; buttons?: TgButton[][]; onImage?: (f: File) => void;
    busy?: boolean; imageError?: string; imageDone?: number; onButton?: (key: string, v: string) => void; buttonMax?: number;
  };
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const prev = useRef<TgEditHandle>(null);
  // Which of the two editors the toolbar acts on: the HTML source or the WYSIWYG preview.
  const target = useRef<"text" | "preview">("text");
  const inPreview = () => target.current === "preview" && !!prev.current?.el && document.activeElement === prev.current.el;
  const [note, setNote] = useState("");
  const dirty = value !== original;

  useLayoutEffect(() => {
    const ta = ref.current;
    if (!ta) return;
    ta.style.height = "0px";
    ta.style.height = `${ta.scrollHeight + 2}px`;
  }, [value]);

  const insert = (text: string) => {
    const ta = ref.current!;
    ta.focus();
    if (!document.execCommand("insertText", false, text)) {
      onChange(ta.value.slice(0, ta.selectionStart) + text + ta.value.slice(ta.selectionEnd));
    }
  };
  const wrap = (open: string, close: string, fill = "текст") => {
    const ta = ref.current!;
    const s = ta.selectionStart, e = ta.selectionEnd;
    const sel = ta.value.slice(s, e) || fill;
    insert(open + sel + close);
    if (open.startsWith("<a ")) ta.setSelectionRange(s + 9, s + 17); // select "https://"
    else ta.setSelectionRange(s + open.length, s + open.length + sel.length);
  };

  const applyTool = (t: Tool) => {
    if (!inPreview()) return wrap(t.open, t.close, t.hint);
    if (t.cmd) document.execCommand(t.cmd);
    else {
      let open = t.open;
      if (open.startsWith("<a ")) {
        const url = window.prompt("Адрес ссылки", "https://");
        if (!url) return;
        open = `<a href="${escHtml(url.trim())}">`;
      }
      document.execCommand("insertHTML", false, open + (selectionHtml() || escHtml(t.hint ?? "текст")) + t.close);
    }
    prev.current?.sync();
  };
  const insertAnywhere = (text: string, html?: string) => {
    if (!inPreview()) return insert(text);
    document.execCommand(html ? "insertHTML" : "insertText", false, html ?? text);
    prev.current?.sync();
  };
  const shortcut = (e: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean; key: string; preventDefault: () => void }) => {
    if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return;
    const t = TOOLS.find((x) => x.key === e.key.toLowerCase());
    if (t) { e.preventDefault(); applyTool(t); }
  };

  const drop = useDrop(
    (kind) => kind !== "image" || !!preview.onImage,
    async (dt, kind) => {
      setNote("");
      if (kind === "image") {
        const f = dt.files[0];
        if (f && preview.onImage) preview.onImage(f);
        return;
      }
      try { replaceAll(ref.current, await droppedText(dt, kind), onChange); setNote("Текст заменён — Ctrl+Z, чтобы вернуть"); }
      catch (e: any) { setNote(e.message); }
    },
  );

  const placeholders = ks?.placeholders ?? [];
  const length = caption ? visibleLength(value) : value.length;
  const over = max !== null && length > max;
  const missing = placeholders.filter((p) => !value.includes(`{${p}}`));
  const invalid = errors.length > 0 || over || missing.length > 0;

  return (
    <section id={fieldId(section, k)} className={"tf" + (dirty ? " dirty" : "") + (invalid ? " invalid" : "")}>
      <header className="tf-head">
        <div className="tf-title">
          <h3>{label}</h3>
          <code>{k}</code>
          {dirty && <span className="tf-dot" title="Не сохранено" />}
        </div>
        <div className="tf-meta">
          {dirty && <Button className="link" onClick={() => replaceAll(ref.current, original, onChange)}>Вернуть как было</Button>}
          <span className={"counter" + (over ? " over" : "")} title={caption ? "Видимых символов (без тегов) — лимит подписи к картинке" : undefined}>
            {length}{max !== null && <> / {max}</>}
            {max !== null && <i style={{ width: `${Math.min(100, (length / max) * 100)}%` }} />}
          </span>
        </div>
      </header>
      {hint && <p className="tf-hint">{hint}</p>}
      <div className="tf-body">
        <div className={"tf-editor" + (drop.over ? " drag" : "")} {...drop.props}>
          <div className="tf-tools">
            {TOOLS.map((t) => (
              <Button key={t.label} type="button" className={"tool " + (t.cls ?? "")} title={t.title}
                onMouseDown={(e) => e.preventDefault()} onClick={() => applyTool(t)}>{t.label}</Button>
            ))}
            <EmojiPicker onPick={(e) => insertAnywhere(e)} />
            <span className="tf-sep" />
            {[...placeholders, "brand"].map((p) => {
              const has = value.includes(`{${p}}`);
              const req = p !== "brand";
              return (
                <Button key={p} type="button" onMouseDown={(e) => e.preventDefault()}
                  onClick={() => insertAnywhere(`{${p}}`, phChip(p, preview.values[p] ?? `{${p}}`))}
                  className={"ph" + (has ? " has" : req ? " need" : "")}
                  title={req ? (has ? "Обязательная подстановка — есть" : "Обязательная подстановка — нажмите, чтобы вставить") : "Название бренда (необязательно)"}>
                  {`{${p}}`}
                </Button>
              );
            })}
          </div>
          <Textarea
            ref={ref} value={value} spellCheck onChange={(e) => onChange(e.target.value)}
            onFocus={() => { target.current = "text"; }}
            onKeyDown={shortcut}
          />
          {drop.over && (
            <div className="drop-hint">
              {drop.over === "image" ? "Отпустите — заменим баннер этого экрана" : "Отпустите — текст заменит содержимое поля"}
            </div>
          )}
        </div>
        <div className="tf-preview">
          <TgMessage
            html="" buttons={preview.buttons} onButton={preview.onButton} buttonMax={preview.buttonMax}
            image={preview.image}
            imageSlot={preview.image && preview.onImage
              ? <BannerSlot src={preview.image} onFile={preview.onImage} busy={preview.busy} error={preview.imageError} done={preview.imageDone} />
              : undefined}
            edit={{
              source: value, values: preview.values, onChange, handle: prev, onKeyDown: shortcut,
              onFocus: () => { target.current = "preview"; },
            }}
          />
          <div className="tf-preview-hint">Текст и кнопки можно править прямо здесь</div>
        </div>
      </div>
      {missing.length > 0 && <div className="error">Не хватает обязательных подстановок: {missing.map((p) => `{${p}}`).join(", ")}</div>}
      {over && <div className="error">Длиннее лимита на {length - (max ?? 0)} симв.</div>}
      {errors.map((m, i) => <div key={i} className="error">{m}</div>)}
      {note && <div className="muted tf-note">{note}</div>}
    </section>
  );
}

function LineField({
  section, k, value, original, ks, label, sub, max, errors, onChange, children,
}: {
  section: Section; k: string; value: string; original: string; ks?: KeySpec; label: string; sub?: string;
  max: number | null; errors: string[]; onChange: (v: string) => void; children?: ReactNode;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const dirty = value !== original;
  const drop = useDrop((kind) => kind !== "image", async (dt, kind) => {
    try { replaceAll(ref.current, (await droppedText(dt, kind)).split("\n")[0], onChange); } catch { /* ignore */ }
  });
  const over = max !== null && value.length > max;
  const missing = (ks?.placeholders ?? []).filter((p) => !value.includes(`{${p}}`));
  return (
    <div id={fieldId(section, k)} className={"lf" + (dirty ? " dirty" : "") + (over || missing.length || errors.length ? " invalid" : "") + (drop.over ? " drag" : "")} {...drop.props}>
      <div className="lf-head">
        <span className="lf-label">{label}{dirty && <span className="tf-dot" title="Не сохранено" />}</span>
        <span className={"counter" + (over ? " over" : "")}>{value.length}{max !== null && ` / ${max}`}</span>
      </div>
      <div className="lf-sub"><code>{k}</code>{sub && <> · {sub}</>}</div>
      <div className="lf-input">
        <Input ref={ref} value={value} onChange={(e) => onChange(e.target.value)} />
        <EmojiPicker className="lf-emoji" onPick={(e) => {
          const el = ref.current;
          if (el) { el.focus(); if (document.execCommand("insertText", false, e)) return; }
          onChange(value + e);
        }} />
        {dirty && <Button className="ghost lf-undo" title="Вернуть как было" onClick={() => replaceAll(ref.current, original, onChange)}>↺</Button>}
      </div>
      {(ks?.placeholders.length ?? 0) > 0 && (
        <div className="lf-ph">
          {ks!.placeholders.map((p) => <span key={p} className={"ph" + (value.includes(`{${p}}`) ? " has" : " need")}>{`{${p}}`}</span>)}
        </div>
      )}
      {children}
      {missing.length > 0 && <div className="error">Нужно: {missing.map((p) => `{${p}}`).join(", ")}</div>}
      {errors.map((m, i) => <div key={i} className="error">{m}</div>)}
    </div>
  );
}

// ------------------------------------------------------------- page
export default function CoverEdit() {
  const { slug = "" } = useParams();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("texts");
  const spec = useQuery({ queryKey: ["spec"], queryFn: () => api<Spec>("/api/spec") });
  const cfg = useQuery({ queryKey: ["config", slug], queryFn: () => api<CoverConfig>(`/api/covers/${slug}/config`) });
  const [draft, setDraft] = useState<CoverConfig | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [toast, setToast] = useState("");
  const [newToken, setNewToken] = useState("");
  const [query, setQuery] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(false);
  const banners = useBanners(slug, spec.data?.banner_max_bytes ?? 10 * 1048576);

  useEffect(() => { if (cfg.data) setDraft(structuredClone(cfg.data)); }, [cfg.data]);
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(""), 5000); return () => clearTimeout(t); }, [toast]);

  const orig = cfg.data;
  const changed = useMemo(() => {
    const out: Record<Section, string[]> = { texts: [], buttons: [], statuses: [] };
    if (!draft || !orig) return out;
    for (const s of SECTIONS) out[s] = Object.keys(draft[s]).filter((k) => (draft[s][k] ?? "") !== (orig[s][k] ?? ""));
    return out;
  }, [draft, orig]);
  const mainChanged = !!draft && !!orig && (draft.brand_name !== orig.brand_name || String(draft.unban_fine_rub) !== String(orig.unban_fine_rub));
  const dirtyCount = changed.texts.length + changed.buttons.length + changed.statuses.length + (mainChanged ? 1 : 0);

  const goToError = (errs: string[]) => {
    const m = errs.map((e) => e.match(/(texts|buttons|statuses)\.(\w+):/)).find(Boolean);
    if (!m) { if (errs.some((e) => /brand_name|unban_fine/.test(e))) setTab("main"); return; }
    setTab(m[1] as Tab);
    setTimeout(() => document.getElementById(fieldId(m[1], m[2]))?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
  };

  const save = useMutation({
    mutationFn: () => {
      const patch: Record<string, unknown> = {};
      for (const s of SECTIONS) if (changed[s].length) patch[s] = Object.fromEntries(changed[s].map((k) => [k, draft![s][k]]));
      patch.expected = Object.fromEntries(SECTIONS.filter((s) => changed[s].length).map((s) => [s, Object.fromEntries(changed[s].map((k) => [k, orig![s][k]]))]));
      if (mainChanged) {
        const fine = String(draft!.unban_fine_rub).trim();
        if (draft!.brand_name !== orig!.brand_name) { patch.brand_name = draft!.brand_name; (patch.expected as Record<string, unknown>).brand_name = orig!.brand_name; }
        if (String(draft!.unban_fine_rub) !== String(orig!.unban_fine_rub)) { patch.unban_fine_rub = /^\d+$/.test(fine) ? Number(fine) : fine; (patch.expected as Record<string, unknown>).unban_fine_rub = orig!.unban_fine_rub; }
      }
      return api<{ changed: string[] }>(`/api/covers/${slug}/config`, json("PUT", patch));
    },
    onSuccess: (r) => {
      setErrors([]);
      setToast(r.changed.length ? "Сохранено. Бот подхватит изменения за ~10 секунд." : "Изменений нет");
      qc.invalidateQueries({ queryKey: ["config", slug] });
    },
    onError: (e: ApiError) => { const errs = e.errors?.length ? e.errors : [e.message]; setErrors(errs); goToError(errs); },
  });
  const changeToken = useMutation({
    mutationFn: () => api(`/api/covers/${slug}/token`, json("PUT", { token: newToken })),
    onSuccess: () => { setNewToken(""); setErrors([]); setToast("Токен заменён, бот перезапустится сам."); qc.invalidateQueries({ queryKey: ["config", slug] }); },
    onError: (e: ApiError) => setErrors(e.errors?.length ? e.errors : [e.message]),
  });

  // Ctrl+S saves; leaving with unsaved changes asks first; files dropped
  // outside a drop zone never make the browser navigate away.
  const canSave = dirtyCount > 0 && !save.isPending;
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (canSave) save.mutate(); }
    };
    const leave = (e: BeforeUnloadEvent) => { if (dirtyCount > 0) e.preventDefault(); };
    const stop = (e: globalThis.DragEvent) => { if (e.dataTransfer?.types.includes("Files")) e.preventDefault(); };
    window.addEventListener("keydown", key);
    window.addEventListener("beforeunload", leave);
    window.addEventListener("dragover", stop);
    window.addEventListener("drop", stop);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("beforeunload", leave);
      window.removeEventListener("dragover", stop);
      window.removeEventListener("drop", stop);
    };
  }, [canSave, dirtyCount, save]);

  if (cfg.error) return <p className="error">{(cfg.error as Error).message}</p>;
  if (!draft || !orig || !spec.data) return <PageSkeleton rows={8} cols={4} />;
  const sp = spec.data;

  const setField = (s: Section) => (k: string) => (v: string) => setDraft((d) => (d ? { ...d, [s]: { ...d[s], [k]: v } } : d));
  const sample = { ...SAMPLE, brand: draft.brand_name || "Бренд", status: draft.statuses.shipping_to_ru ?? "В пути" };
  const kb = (name?: string): TgButton[][] | undefined => name
    ? KEYBOARDS[name]?.map((row) => row.map((b) => ({ key: b, raw: draft.buttons[b] ?? "", label: fillPlain(draft.buttons[b] ?? "", sample) })))
    : undefined;
  const setButton = (k: string, v: string) => setField("buttons")(k)(v);
  const tabCount: Partial<Record<Tab, number>> = {
    main: mainChanged ? 1 : 0, texts: changed.texts.length, buttons: changed.buttons.length, statuses: changed.statuses.length,
  };

  const q = query.trim().toLowerCase();
  const matches = (s: Section, k: string, label: string) =>
    (!onlyChanged || changed[s].includes(k)) &&
    (!q || k.toLowerCase().includes(q) || label.toLowerCase().includes(q) || (draft[s][k] ?? "").toLowerCase().includes(q));

  const textKeys = Object.keys(sp.texts);
  const grouped = TEXT_GROUPS.map(([g, keys]) => [g, keys.filter((k) => textKeys.includes(k))] as [string, string[]]);
  const known = new Set(grouped.flatMap(([, ks]) => ks));
  const rest = textKeys.filter((k) => !known.has(k));
  if (rest.length) grouped.push(["Прочее", rest]);
  const visibleGroups = grouped
    .map(([g, ks]) => [g, ks.filter((k) => matches("texts", k, TEXT_META[k]?.label ?? k))] as [string, string[]])
    .filter(([, ks]) => ks.length);

  const filterBar = (
    <div className="ce-filter">
      <Input type="search" placeholder="Поиск по названию, ключу или тексту" value={query} onChange={(e) => setQuery(e.target.value)} />
      <label><CustomCheckbox checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} /> Только изменённые</label>
    </div>
  );

  return (
    <div className="cover-edit">
      <div className="head">
        <h1><Link to="/covers">Обложки</Link> / {slug}</h1>
      </div>
      <Tabs value={tab} onValueChange={(value) => setTab(value as typeof tab)}><TabsList className="app-tabs">{TABS.map(([id, label]) => <TabsTrigger key={id} value={id}>{label}{tabCount[id] ? <span className="tab-count">{tabCount[id]}</span> : null}</TabsTrigger>)}</TabsList></Tabs>
      {orig.last_error && <p className="error">Ошибка обложки: {orig.last_error}</p>}

      {tab === "visual" && <BotSimulator draft={draft} spec={sp} banners={banners} sample={sample} keyboard={kb} onText={setField("texts")} onButton={setButton} errors={errors} />}

      {tab === "main" && (
        <Card className="card form">
          <label>Название бренда
            <Input value={draft.brand_name} onChange={(e) => setDraft({ ...draft, brand_name: e.target.value })} />
            <small className="muted">Подставляется в тексты вместо {"{brand}"}</small>
          </label>
          <label>
            Штраф за разблокировку, ₽
            <Input value={String(draft.unban_fine_rub)} onChange={(e) => setDraft({ ...draft, unban_fine_rub: e.target.value })} />
            <small className="muted">Число или «{sp.shared}» — общий штраф из настроек</small>
          </label>
          {errors.filter((e) => /brand_name|unban_fine/.test(e)).map((m, i) => <p key={i} className="error">{m}</p>)}
          <hr />
          <label>Токен бота (сейчас: id {draft.token_masked})
            <Input value={newToken} onChange={(e) => setNewToken(e.target.value)} placeholder="новый токен от @BotFather" autoComplete="off" />
          </label>
          <div><Button disabled={!newToken || changeToken.isPending} onClick={() => changeToken.mutate()}>Сменить токен</Button></div>
          {changeToken.isError && errors.map((m, i) => <p key={i} className="error">{m}</p>)}
        </Card>
      )}

      {tab === "texts" && (
        <div className="ce-layout">
          <nav className="ce-nav">
            {visibleGroups.map(([g, ks]) => (
              <div key={g}>
                <div className="ce-nav-group">{g}</div>
                {ks.map((k) => {
                  const bad = errorsFor(errors, "texts", k).length > 0;
                  return (
                    <a key={k} href={`#${fieldId("texts", k)}`} className={(changed.texts.includes(k) ? "dirty" : "") + (bad ? " bad" : "")}
                      onClick={(e) => { e.preventDefault(); document.getElementById(fieldId("texts", k))?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>
                      {TEXT_META[k]?.label ?? k}
                    </a>
                  );
                })}
              </div>
            ))}
          </nav>
          <div className="ce-main">
            {filterBar}
            <p className="ce-tip muted">
              Текст можно править и в поле с HTML, и прямо в превью — справа, как в Telegram: выделите слова и нажмите B / I / U, вставьте смайлик ☺ или подстановку.
              Картинку перетащите на баннер в превью (или нажмите на него), подпись кнопки — нажмите на неё.
              Текстовый файл, брошенный на поле, заменит его содержимое.
            </p>
            {!visibleGroups.length && <Card className="card muted">Ничего не найдено</Card>}
            {visibleGroups.map(([g, ks]) => (
              <div key={g} className="ce-group">
                <h2>{g}</h2>
                {ks.map((k) => {
                  const meta = TEXT_META[k];
                  const ks_ = sp.texts[k];
                  const banner = ks_?.caption ? meta?.banner : undefined;
                  return (
                    <TextField
                      key={k} section="texts" k={k} value={draft.texts[k] ?? ""} original={orig.texts[k] ?? ""} ks={ks_}
                      label={meta?.label ?? k} hint={meta?.hint} caption={!!ks_?.caption} max={ks_?.caption ? sp.caption_limit : null}
                      errors={errorsFor(errors, "texts", k)} onChange={setField("texts")(k)}
                      preview={{
                        values: sample,
                        image: banner ? banners.src(banner) : null,
                        buttons: kb(meta?.kb), onButton: setButton, buttonMax: sp.button_limit,
                        onImage: banner ? (f) => banners.upload(banner, f) : undefined,
                        busy: banner ? banners.state[banner]?.busy : false,
                        imageError: banner ? banners.state[banner]?.error : undefined,
                        imageDone: banner ? banners.state[banner]?.done : undefined,
                      }}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "buttons" && (
        <>
          {filterBar}
          <div className="lf-grid">
            {Object.keys(sp.buttons).filter((k) => matches("buttons", k, BUTTON_LABELS[k] ?? k)).map((k) => {
              const used = buttonUsage(k);
              return (
                <LineField key={k} section="buttons" k={k} value={draft.buttons[k] ?? ""} original={orig.buttons[k] ?? ""} ks={sp.buttons[k]}
                  label={BUTTON_LABELS[k] ?? k} max={sp.button_limit} errors={errorsFor(errors, "buttons", k)} onChange={setField("buttons")(k)}
                  sub={used.length ? `экраны: ${used.slice(0, 3).join(", ")}${used.length > 3 ? ` и ещё ${used.length - 3}` : ""}` : undefined}>
                  <div className="tg-kb lf-preview"><div className="tg-row"><span className="tg-btn">{fillPlain(draft.buttons[k] ?? "", sample) || "—"}</span></div></div>
                </LineField>
              );
            })}
          </div>
        </>
      )}

      {tab === "statuses" && (
        <>
          {filterBar}
          <div className="lf-grid">
            {sp.statuses.filter((k) => matches("statuses", k, STATUS_LABELS[k] ?? k)).map((k) => (
              <LineField key={k} section="statuses" k={k} value={draft.statuses[k] ?? ""} original={orig.statuses[k] ?? ""}
                label={STATUS_LABELS[k] ?? k} max={null} errors={errorsFor(errors, "statuses", k)} onChange={setField("statuses")(k)} />
            ))}
          </div>
        </>
      )}

      {tab === "banners" && <BannersTab spec={sp} banners={banners} />}

      {tab !== "banners" && errors.length > 0 && !errors.some((e) => /(texts|buttons|statuses)\.\w+:/.test(e)) && tab !== "main" &&
        errors.map((m, i) => <p key={i} className="error">{m}</p>)}

      {(dirtyCount > 0 || toast) && tab !== "banners" && (
        <div className={"savebar" + (dirtyCount ? "" : " done")}>
          {dirtyCount > 0 ? (
            <>
              <span>Не сохранено: <b>{dirtyCount}</b> {plural(dirtyCount, "поле", "поля", "полей")}<span className="muted kbd-hint"> · Ctrl+S</span></span>
              <span className="savebar-actions">
                <Button className="ghost" onClick={() => { setDraft(structuredClone(orig)); setErrors([]); }}>Отменить всё</Button>
                <Button disabled={!canSave} onClick={() => save.mutate()}>{save.isPending ? "Сохраняем…" : "Сохранить"}</Button>
              </span>
            </>
          ) : <span className="ok-text">{toast}</span>}
        </div>
      )}
    </div>
  );
}

function BotSimulator({ draft, spec, banners, sample, keyboard, onText, onButton, errors }: {
  draft: CoverConfig; spec: Spec; banners: Banners; sample: Record<string, string>;
  keyboard: (name?: string) => TgButton[][] | undefined;
  onText: (key: string) => (value: string) => void; onButton: (key: string, value: string) => void; errors: string[];
}) {
  const [screen, setScreen] = useState("WELCOME");
  const [editing, setEditing] = useState(false);
  const [typed, setTyped] = useState("");
  const [notice, setNotice] = useState("");
  const editor = useRef<TgEditHandle>(null);
  const meta = TEXT_META[screen];
  const ks = spec.texts[screen];
  const value = draft.texts[screen] ?? "";
  const banner = ks?.caption ? meta?.banner : undefined;
  const screens = Object.keys(spec.texts).filter((k) => TEXT_META[k]?.banner || TEXT_META[k]?.kb);
  const go = (next: string) => { setScreen(next); setTyped(""); setNotice(""); };
  const navigate = (key: string) => {
    const destinations: Record<string, string> = {
      BTN_NEW_ORDER: "ORDER_CHOICE", BTN_PROFILE: "PROFILE", BTN_SUPPORT: "SUPPORT",
      BTN_MY_ORDERS: "NO_ORDERS", BTN_ORDER_PERSONAL: "PERSONAL_PROMPT", BTN_PERSONAL_APPLICATION: "PERSONAL_PROMPT",
      BTN_ORDER_ARTICLE: "ARTICLE_ALREADY", BTN_ORDER_MYSTERY: "MYSTERY_LIST", BTN_HAS_ARTICLE: "ARTICLE_PROMPT",
      BTN_NO_ARTICLE: "PERSONAL_PROMPT", BTN_MYSTERY_ITEM: "MYSTERY_OFFER", BTN_TO_PAYMENT: "PAYMENT",
      BTN_MENU: "WELCOME", BTN_HOME: "WELCOME", BTN_CLOSE_DIALOG: "WELCOME", BTN_OPEN_DIALOG: "DIALOG_OPEN",
      BTN_BACK: ["NO_ORDERS", "ORDERS_HEADER", "OFFER"].includes(screen) ? "PROFILE" :
        ["ARTICLE_PROMPT", "ARTICLE_INVALID"].includes(screen) ? "ARTICLE_ALREADY" :
        ["ARTICLE_ALREADY", "PERSONAL_PROMPT", "MYSTERY_LIST", "MYSTERY_EMPTY"].includes(screen) ? "ORDER_CHOICE" : "WELCOME",
    };
    if (key === "BTN_OFFER" || key === "BTN_PAY_ONLINE" || key === "BTN_PAY_FINE") {
      setNotice(key === "BTN_OFFER" ? "В настоящем боте откроется ссылка на оферту." : "В настоящем боте откроется страница оплаты.");
      return;
    }
    if (destinations[key]) go(destinations[key]);
  };
  const insert = (text: string, html = false) => {
    const el = editor.current?.el;
    if (!el) return;
    el.focus();
    document.execCommand(html ? "insertHTML" : "insertText", false, text);
    editor.current?.sync();
  };
  const format = (tool: Tool) => {
    if (!editor.current?.el) return;
    if (tool.cmd) document.execCommand(tool.cmd);
    else {
      let open = tool.open;
      if (open.startsWith("<a ")) {
        const url = window.prompt("Адрес ссылки", "https://");
        if (!url) return;
        open = `<a href="${escHtml(url.trim())}">`;
      }
      document.execCommand("insertHTML", false, open + (selectionHtml() || escHtml(tool.hint ?? "текст")) + tool.close);
    }
    editor.current.sync();
  };
  const missing = (ks?.placeholders ?? []).filter((p) => !value.includes(`{${p}}`));
  const isInput = screen === "ARTICLE_PROMPT" || screen === "ARTICLE_INVALID" || screen === "PERSONAL_PROMPT";
  return <div className="bot-sim-layout">
    <div className="bot-sim-info">
      <h2>Интерфейс бота</h2>
      <p className="muted">Пройдите по кнопкам, как клиент. В режиме редактирования нажмите на сообщение или подпись кнопки.</p>
      <div className="bot-sim-modes" role="group" aria-label="Режим интерфейса бота">
        <Button type="button" className={!editing ? "" : "ghost"} aria-pressed={!editing} onClick={() => setEditing(false)}>Навигация</Button>
        <Button type="button" className={editing ? "" : "ghost"} aria-pressed={editing} onClick={() => setEditing(true)}>Редактирование</Button>
      </div>
      <label className="bot-sim-select">Экран
        <CustomSelect value={screen} onChange={(e) => go(e.target.value)}>{screens.map((k) => <option key={k} value={k}>{TEXT_META[k]?.label ?? k}</option>)}</CustomSelect>
      </label>
      <p className="muted bot-sim-note">Это предпросмотр с примерами данных. Сообщения не отправляются, заказы и платежи не создаются.</p>
      {editing && <>
        <p className="muted">Подпись кнопки изменится на всех экранах, где она используется. Баннер сохраняется сразу; тексты и кнопки — по кнопке «Сохранить».</p>
        {missing.length > 0 && <p className="error">Нужны подстановки: {missing.map((p) => `{${p}}`).join(", ")}</p>}
        {ks?.caption && visibleLength(value) > spec.caption_limit && <p className="error">Текст превышает лимит подписи: {spec.caption_limit} символов.</p>}
        {errorsFor(errors, "texts", screen).map((e, i) => <p className="error" key={i}>{e}</p>)}
      </>}
    </div>
    <div className="bot-sim-phone">
      <div className="bot-sim-header"><span className="bot-sim-avatar">{(draft.brand_name || "Т").slice(0, 1)}</span><div><b>{draft.brand_name || "Бот"}</b><small>бот · предпросмотр</small></div><Button className="ghost" type="button" onClick={() => go("WELCOME")}>/start</Button></div>
      {editing && <div className="tf-tools bot-sim-tools">
        {TOOLS.map((t) => <Button key={t.label} type="button" className={`tool ${t.cls ?? ""}`} title={t.title} onMouseDown={(e) => e.preventDefault()} onClick={() => format(t)}>{t.label}</Button>)}
        <EmojiPicker onPick={(emoji) => insert(emoji)} />
        {(ks?.placeholders ?? []).concat("brand").map((p) => <Button key={p} className="ph" type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => insert(phChip(p, sample[p] ?? `{${p}}`), true)}>{`{${p}}`}</Button>)}
      </div>}
      <div className="bot-sim-chat">
        <div key={screen} className="bot-sim-message">
          <TgMessage html={fillPlaceholders(value, sample)} image={banner ? banners.src(banner) : null}
            imageSlot={editing && banner ? <BannerSlot src={banners.src(banner)} onFile={(f) => banners.upload(banner, f)} busy={banners.state[banner]?.busy} error={banners.state[banner]?.error} done={banners.state[banner]?.done} /> : undefined}
            buttons={keyboard(meta?.kb)} onButton={editing ? onButton : undefined} onNavigate={editing ? undefined : navigate} buttonMax={spec.button_limit}
            edit={editing ? { source: value, values: sample, onChange: onText(screen), handle: editor } : undefined} />
        </div>
        {notice && <div className="bot-sim-toast" role="status">{notice}</div>}
      </div>
      {isInput && !editing && <form className="bot-sim-input" onSubmit={(e) => { e.preventDefault(); if (!typed.trim()) return; go(screen === "PERSONAL_PROMPT" ? "PERSONAL_DONE" : /^\d{6}$/.test(typed.trim()) ? "FOUND" : "ARTICLE_INVALID"); }}>
        <Input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={screen === "PERSONAL_PROMPT" ? "Опишите товар…" : "Введите пример артикула…"} />
        <Button type="submit" disabled={!typed.trim()}>➤</Button>
      </form>}
    </div>
  </div>;
}

const plural = (n: number, one: string, few: string, many: string) => {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
};

// ------------------------------------------------------------- banners tab
function BannerCard({ name, banners, onHover }: { name: string; banners: Banners; onHover: (n: string | null) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const st = banners.state[name] ?? {};
  const [size, setSize] = useState("");
  const drop = useDrop((k) => k === "image" || k === "file", (dt) => { const f = dt.files[0]; if (f) banners.upload(name, f); });
  const used = bannerUsage(name);
  return (
    <div
      className={"banner" + (drop.over ? " drag" : "") + (st.busy ? " busy" : "") + (st.error ? " failed" : "")}
      tabIndex={0} {...drop.props}
      onMouseEnter={() => onHover(name)} onMouseLeave={() => onHover(null)} onFocus={() => onHover(name)} onBlur={() => onHover(null)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}
    >
      <Button type="button" className="banner-img" onClick={() => input.current?.click()} title="Нажмите, чтобы выбрать файл">
        <img src={banners.src(name)} alt={name} loading="lazy"
          onLoad={(e) => setSize(`${e.currentTarget.naturalWidth}×${e.currentTarget.naturalHeight}`)} />
        <span className="banner-over">
          {st.busy ? "Загружаем…" : drop.over ? "Отпустите, чтобы заменить" : "Перетащите картинку или нажмите"}
        </span>
        {st.done && !st.busy && <span key={st.done} className="banner-done">Заменено ✓</span>}
      </Button>
      <div className="banner-info">
        <b>{BANNER_LABELS[name] ?? name}</b>
        <span className="muted"><code>{name}</code>{size && ` · ${size}`}</span>
        {used.length > 0 && <span className="muted banner-used">{used.join(", ")}</span>}
        {st.error && <span className="error">{st.error}</span>}
      </div>
      <input ref={input} type="file" accept="image/*" hidden
        onChange={(e) => { const f = e.target.files?.[0]; if (f) banners.upload(name, f); e.target.value = ""; }} />
    </div>
  );
}

function BannersTab({ spec, banners }: { spec: Spec; banners: Banners }) {
  const hovered = useRef<string | null>(null);
  const [msg, setMsg] = useState<{ ok: number; unknown: string[] } | null>(null);

  // Drop several files anywhere on the grid: matched by file name (welcome.png -> welcome).
  const many = useDrop((k) => k === "image" || k === "file", (dt) => {
    const files = Array.from(dt.files);
    const unknown: string[] = [];
    let ok = 0;
    for (const f of files) {
      const n = matchBanner(f.name, spec.banners);
      if (n) { ok++; banners.upload(n, f); } else unknown.push(f.name);
    }
    setMsg({ ok, unknown });
  });

  // Ctrl+V with an image in the clipboard replaces the banner under the cursor.
  useEffect(() => {
    const paste = (e: ClipboardEvent) => {
      const name = hovered.current;
      const file = Array.from(e.clipboardData?.files ?? []).find((f) => f.type.startsWith("image/"));
      if (!name || !file) return;
      e.preventDefault();
      banners.upload(name, file);
    };
    window.addEventListener("paste", paste);
    return () => window.removeEventListener("paste", paste);
  }, [banners]);

  return (
    <div className={"banners-zone" + (many.over ? " drag" : "")} {...many.props}>
      <p className="ce-tip muted">
        Перетащите картинку на карточку — она сразу заменит баннер. Можно бросить сразу несколько файлов в любое место:
        они разложатся по именам (<code>welcome.png</code> → «{BANNER_LABELS.welcome}»). Наведите на карточку и нажмите Ctrl+V, чтобы вставить из буфера.
        JPG и WebP переводятся в PNG автоматически, до {Math.round(spec.banner_max_bytes / 1048576)} МБ.
      </p>
      <div className="banners">
        {spec.banners.map((n) => <BannerCard key={n} name={n} banners={banners} onHover={(x) => { hovered.current = x; }} />)}
      </div>
      {msg && msg.ok > 0 && <p className="muted">Заменяем баннеров: {msg.ok}</p>}
      {msg && msg.unknown.length > 0 && (
        <p className="error">
          Не удалось определить баннер для: {msg.unknown.join(", ")}. Назовите файл как баннер (например, welcome.png) или перетащите его прямо на карточку.
        </p>
      )}
      {many.over && <div className="banners-drop">Отпустите файлы — разложим по именам</div>}
    </div>
  );
}
