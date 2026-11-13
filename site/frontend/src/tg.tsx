// A Telegram-like preview of a bot message. The text is Telegram HTML; only the
// tags Telegram supports are rendered, everything else is shown as plain text,
// so nothing typed in the editor can inject markup into the panel.
//
// The preview can also be edited in place (WYSIWYG): the bubble is a
// contentEditable built from the same whitelist, and every edit is serialized
// back to Telegram HTML (see `serializeTg`).
import { Fragment, KeyboardEvent, ReactNode, Ref, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escAttr = (s: string) => esc(s).replace(/"/g, "&quot;");
/** Templates go through str.format, so literal braces are doubled. */
const braces = (s: string) => s.replace(/[{}]/g, (c) => c + c);

/** str.format-like substitution; values are wrapped so the preview can highlight them. */
export function fillPlaceholders(template: string, values: Record<string, string>) {
  return template.replace(/\{\{|\}\}|\{(\w+)\}/g, (m, key?: string) => {
    if (m === "{{") return "{";
    if (m === "}}") return "}";
    const v = key !== undefined ? values[key] : undefined;
    return v === undefined ? `<tg-missing>${esc(m)}</tg-missing>` : `<tg-ph data-k="${key}">${esc(v)}</tg-ph>`;
  });
}

/** Same substitution for plain text (button labels are not HTML). */
export const fillPlain = (template: string, values: Record<string, string>) =>
  template.replace(/\{\{|\}\}|\{(\w+)\}/g, (m, key?: string) =>
    m === "{{" ? "{" : m === "}}" ? "}" : key !== undefined && values[key] !== undefined ? values[key] : m);

/** Length Telegram counts: tags removed, entities decoded. */
export function visibleLength(html: string) {
  const el = document.createElement("textarea");
  el.innerHTML = html.replace(/<\/?[a-zA-Z][a-zA-Z0-9-]*[^<>]*>/g, "");
  return el.value.length;
}

const SAFE_URL = /^(https?:|tg:|mailto:)/i;
const parse = (html: string) => new DOMParser().parseFromString(`<body>${html}</body>`, "text/html").body;

function walk(node: Node, key: number): ReactNode {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE) return null;
  const el = node as Element;
  const kids = Array.from(el.childNodes).map((c, i) => walk(c, i));
  switch (el.tagName.toLowerCase()) {
    case "b": case "strong": return <b key={key}>{kids}</b>;
    case "i": case "em": return <i key={key}>{kids}</i>;
    case "u": case "ins": return <u key={key}>{kids}</u>;
    case "s": case "strike": case "del": return <s key={key}>{kids}</s>;
    case "code": return <code key={key}>{kids}</code>;
    case "pre": return <pre key={key}>{kids}</pre>;
    case "blockquote": return <blockquote key={key}>{kids}</blockquote>;
    case "tg-spoiler": return <span key={key} className="tg-spoiler">{kids}</span>;
    case "tg-ph": return <span key={key} className="tg-ph">{kids}</span>;
    case "tg-missing": return <span key={key} className="tg-missing">{kids}</span>;
    case "a": {
      const href = el.getAttribute("href") ?? "";
      return SAFE_URL.test(href)
        ? <a key={key} href={href} target="_blank" rel="noreferrer noopener">{kids}</a>
        : <span key={key} className="tg-link">{kids}</span>;
    }
    default: return <Fragment key={key}>{kids}</Fragment>;
  }
}

export function renderTg(html: string): ReactNode {
  return Array.from(parse(html).childNodes).map((n, i) => walk(n, i));
}

// ------------------------------------------------------------- editable preview
const SIMPLE: Record<string, string> = {
  b: "b", strong: "b", i: "i", em: "i", u: "u", ins: "u", s: "s", strike: "s", del: "s",
  code: "code", pre: "pre", blockquote: "blockquote", "tg-spoiler": "tg-spoiler",
};

/** Filled Telegram HTML -> safe HTML for the contentEditable bubble. */
function toEditable(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return esc(node.textContent ?? "");
  if (node.nodeType !== Node.ELEMENT_NODE) return "";
  const el = node as Element;
  const kids = Array.from(el.childNodes).map(toEditable).join("");
  const tag = el.tagName.toLowerCase();
  if (SIMPLE[tag]) return `<${SIMPLE[tag]}>${kids}</${SIMPLE[tag]}>`;
  if (tag === "tg-ph") return phChip(el.getAttribute("data-k") ?? "", el.textContent ?? "");
  if (tag === "tg-missing") return `<span class="tg-missing" contenteditable="false" data-raw="${escAttr(el.textContent ?? "")}">${kids}</span>`;
  if (tag === "a") return `<a href="${escAttr(el.getAttribute("href") ?? "")}">${kids}</a>`;
  return kids;
}

/** A placeholder inside the editable bubble: one undividable chip. */
export const phChip = (key: string, value: string) =>
  `<span class="tg-ph" contenteditable="false" data-ph="${escAttr(key)}">${esc(value)}</span>`;

/** contentEditable DOM -> Telegram HTML template (placeholders back to {name}). */
function serialize(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return braces(esc((node.textContent ?? "").replace(/ /g, " ")));
  if (node.nodeType !== Node.ELEMENT_NODE) return "";
  const el = node as HTMLElement;
  if (el.dataset.ph) return `{${el.dataset.ph}}`;
  if (el.dataset.raw !== undefined) return el.dataset.raw;
  const tag = el.tagName.toLowerCase();
  if (tag === "br") return "\n";
  const only = el.childNodes.length === 1 && el.firstChild?.nodeName === "BR";
  let inner = only ? "" : Array.from(el.childNodes).map(serialize).join("");
  if (tag === "div" || tag === "p") return (el.previousSibling ? "\n" : "") + inner;
  if (!inner) return "";
  if (SIMPLE[tag]) return `<${SIMPLE[tag]}>${inner}</${SIMPLE[tag]}>`;
  if (tag === "a") return `<a href="${braces(escAttr(el.getAttribute("href") ?? ""))}">${inner}</a>`;
  // Formatting the browser expressed with inline styles.
  const st = el.style;
  if (st) {
    if (/line-through/.test(st.textDecoration + st.textDecorationLine)) inner = `<s>${inner}</s>`;
    if (/underline/.test(st.textDecoration + st.textDecorationLine)) inner = `<u>${inner}</u>`;
    if (st.fontStyle === "italic") inner = `<i>${inner}</i>`;
    if (st.fontWeight === "bold" || Number(st.fontWeight) >= 600) inner = `<b>${inner}</b>`;
  }
  return inner;
}

export function serializeTg(root: HTMLElement) {
  const nodes = Array.from(root.childNodes);
  // The browser keeps a trailing <br> so the caret can stand on an empty last line; it renders nothing.
  if (nodes.length && nodes[nodes.length - 1].nodeName === "BR") nodes.pop();
  return nodes.map(serialize).join("");
}

export type TgEditHandle = { el: HTMLDivElement | null; sync: () => void };

function TgEditable({
  source, values, onChange, handle, onKeyDown, onFocus,
}: {
  source: string; values: Record<string, string>; onChange: (v: string) => void;
  handle?: Ref<TgEditHandle>; onKeyDown?: (e: KeyboardEvent<HTMLDivElement>) => void; onFocus?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const last = useRef<{ source: string; values: string } | null>(null);
  const valuesKey = JSON.stringify(values);

  const markEmpty = () => { const el = ref.current; if (el) el.dataset.empty = el.textContent?.trim() || el.querySelector("[data-ph]") ? "0" : "1"; };
  const sync = () => {
    const el = ref.current;
    if (!el) return;
    const s = serializeTg(el);
    last.current = { source: s, values: valuesKey };
    markEmpty();
    if (s !== source) onChange(s);
  };
  useImperativeHandle(handle, () => ({ el: ref.current, sync }));

  // Rebuild the DOM only when the text changed somewhere else (textarea, undo,
  // "revert"), never in response to our own edits — that keeps the caret.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || (last.current?.source === source && last.current.values === valuesKey)) return;
    el.innerHTML = Array.from(parse(fillPlaceholders(source, values)).childNodes).map(toEditable).join("");
    last.current = { source, values: valuesKey };
    markEmpty();
  });

  return (
    <div
      ref={ref} className="tg-edit" contentEditable suppressContentEditableWarning spellCheck
      data-placeholder="Пустой текст — начните печатать"
      onInput={sync}
      onFocus={() => { document.execCommand("styleWithCSS", false, "false"); onFocus?.(); }}
      onPaste={(e) => {
        e.preventDefault();
        document.execCommand("insertText", false, e.clipboardData.getData("text/plain").replace(/\r\n?/g, "\n"));
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.nativeEvent.isComposing) {
          e.preventDefault();
          if (!document.execCommand("insertLineBreak")) document.execCommand("insertText", false, "\n");
          return;
        }
        onKeyDown?.(e);
      }}
      onClick={(e) => { if ((e.target as HTMLElement).closest("a")) e.preventDefault(); }}
    />
  );
}

// ------------------------------------------------------------- message
export type TgButton = { key: string; raw: string; label: string };

function EditableButton({ b, max, onChange }: { b: TgButton; max?: number; onChange: (key: string, v: string) => void }) {
  const [editing, setEditing] = useState<string | null>(null); // value before editing
  if (editing !== null) {
    return (
      <input
        className="tg-btn tg-btn-input" autoFocus value={b.raw} maxLength={max ? max + 20 : undefined}
        onChange={(e) => onChange(b.key, e.target.value)}
        onBlur={() => setEditing(null)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); setEditing(null); }
          if (e.key === "Escape") { e.preventDefault(); onChange(b.key, editing); setEditing(null); }
        }}
      />
    );
  }
  return (
    <button type="button" className="tg-btn editable" onClick={() => setEditing(b.raw)}
      title="Нажмите, чтобы изменить подпись. Кнопка поменяется на всех экранах, где она есть">
      {b.label || "—"}
    </button>
  );
}

export function TgMessage({
  html, image, imageSlot, buttons, edit, onButton, onNavigate, buttonMax,
}: {
  html: string; image?: string | null; imageSlot?: ReactNode; buttons?: (string | TgButton)[][];
  edit?: {
    source: string; values: Record<string, string>; onChange: (v: string) => void; handle?: Ref<TgEditHandle>;
    onKeyDown?: (e: KeyboardEvent<HTMLDivElement>) => void; onFocus?: () => void;
  };
  onButton?: (key: string, v: string) => void; onNavigate?: (key: string) => void; buttonMax?: number;
}) {
  const img = imageSlot ?? (image ? <div className="tg-image"><img src={image} alt="" /></div> : null);
  return (
    <div className="tg">
      <div className={"tg-bubble" + (img ? " with-image" : "")}>
        {img}
        <div className="tg-text">
          {edit ? <TgEditable {...edit} />
            : html.trim() ? renderTg(html) : <span className="tg-empty">Пустой текст</span>}
          <span className="tg-time">12:00</span>
        </div>
      </div>
      {buttons && buttons.length > 0 && (
        <div className="tg-kb">
          {buttons.map((row, i) => (
            <div key={i} className="tg-row">
              {row.map((b, j) => typeof b === "string"
                ? <span key={j} className="tg-btn">{b || "—"}</span>
                : onButton ? <EditableButton key={b.key} b={b} max={buttonMax} onChange={onButton} />
                  : onNavigate ? <button type="button" key={b.key} className="tg-btn tg-nav-button" onClick={() => onNavigate(b.key)}>{b.label || "—"}</button>
                  : <span key={j} className="tg-btn">{b.label || "—"}</span>)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
