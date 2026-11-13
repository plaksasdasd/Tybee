import { Children, ChangeEvent, isValidElement, ReactNode, SelectHTMLAttributes, InputHTMLAttributes, useState } from "react";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem, SelectGroup, SelectLabel } from "@/components/ui/select";
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { ru } from "date-fns/locale";
import { CalendarDays } from "lucide-react";

type Item = { value: string; label: ReactNode; disabled?: boolean; group?: string };
function collect(children: ReactNode, group?: string): Item[] {
  return Children.toArray(children).flatMap((node): Item[] => {
    if (!isValidElement<any>(node)) return [];
    if (node.type === "optgroup") return collect(node.props.children, node.props.label);
    if (node.type === "option") return [{ value: String(node.props.value ?? node.props.children), label: node.props.children, disabled: node.props.disabled, group }];
    return collect(node.props.children, group);
  });
}
const EMPTY = "__tybee_empty_option__";
export function CustomSelect({ children, value, onChange, disabled, style, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  const items = collect(children);
  const groups: { label?: string; items: Item[] }[] = [];
  for (const item of items) {
    if (!groups.length || groups[groups.length - 1].label !== item.group) groups.push({ label: item.group, items: [] });
    groups[groups.length - 1].items.push(item);
  }
  return <Select value={String(value ?? "") || EMPTY} disabled={disabled} onValueChange={(v) => onChange?.({ target: { value: v === EMPTY ? "" : v }, currentTarget: { value: v === EMPTY ? "" : v } } as ChangeEvent<HTMLSelectElement>)}>
    <SelectTrigger className="app-select-trigger" style={style} aria-label={props["aria-label"]}><SelectValue placeholder="Выберите" /></SelectTrigger>
    <SelectContent position="popper" className="app-select-content">{groups.map((group, i) => <SelectGroup key={i}>
      {group.label && <SelectLabel>{group.label}</SelectLabel>}
      {group.items.map((item, j) => <SelectItem key={`${item.value}-${j}`} value={item.value || EMPTY} disabled={item.disabled} className="app-select-item">{item.label}</SelectItem>)}
    </SelectGroup>)}</SelectContent>
  </Select>;
}
function isoDate(d: Date) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
export function CustomDate({ value, onChange, disabled, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  const [open, setOpen] = useState(false);
  const selected = value ? new Date(`${value}T12:00:00`) : undefined;
  const choose = (v: string) => { onChange?.({ target: { value: v }, currentTarget: { value: v } } as ChangeEvent<HTMLInputElement>); setOpen(false); };
  return <Popover open={open} onOpenChange={setOpen}><PopoverTrigger asChild><Button type="button" variant="outline" className="app-date-trigger" disabled={disabled} aria-label={props["aria-label"] || "Выбрать дату"}>{value ? String(value).split("-").reverse().join(".") : "Выберите дату"}<CalendarDays size={16} /></Button></PopoverTrigger>
    <PopoverContent className="app-calendar-popover" align="start"><Calendar mode="single" locale={ru} selected={selected} defaultMonth={selected} onSelect={(date) => choose(date ? isoDate(date) : "")} /><div className="date-actions"><Button type="button" variant="link" onClick={() => choose("")}>Очистить</Button><Button type="button" variant="link" onClick={() => choose(isoDate(new Date()))}>Сегодня</Button></div></PopoverContent>
  </Popover>;
}
export function CustomCheckbox({ checked, onChange, disabled, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <Checkbox checked={checked} disabled={disabled} className="app-checkbox" aria-label={props["aria-label"]} onCheckedChange={(v) => onChange?.({ target: { checked: v === true }, currentTarget: { checked: v === true } } as ChangeEvent<HTMLInputElement>)} />;
}

export function DateRangePicker({ from, to, onChange }: { from: string; to: string; onChange: (range: { from: string; to: string }) => void }) {
  const [open, setOpen] = useState(false);
  const parse = (value: string) => value ? new Date(value + "T12:00:00") : undefined;
  const [draft, setDraft] = useState<{from: Date | undefined; to?: Date | undefined} | undefined>();
  const display = (value: string) => value.split("-").reverse().join(".");
  return <Popover open={open} onOpenChange={(next) => { if (next) setDraft(from ? {from: parse(from), to: parse(to)} : undefined); setOpen(next); }}>
    <PopoverTrigger asChild><Button type="button" variant="outline" className="app-date-trigger app-range-trigger" aria-label="Выбрать период"><CalendarDays size={16} />{from ? display(from) + (to ? " — " + display(to) : "") : "Выберите период"}</Button></PopoverTrigger>
    <PopoverContent className="app-calendar-popover" align="start"><Calendar mode="range" locale={ru} selected={draft} defaultMonth={draft?.from} onSelect={setDraft} /><div className="date-actions"><Button type="button" variant="ghost" onClick={() => { onChange({ from: "", to: "" }); setOpen(false); }}>Очистить</Button><Button type="button" disabled={!draft?.from} onClick={() => { if (!draft?.from) return; onChange({ from: isoDate(draft.from), to: isoDate(draft.to ?? draft.from) }); setOpen(false); }}>Применить</Button></div></PopoverContent>
  </Popover>;
}
