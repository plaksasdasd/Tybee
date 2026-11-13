import { useState } from "react";

type Draft = { text: string; requestKey: string; savedAt: number; fileName?: string; nextStep?: string; dueAt?: string };
const files = new Map<string, File>();
export const newRequestKey = () => {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};
const blank = (): Draft => ({ text: "", requestKey: newRequestKey(), savedAt: 0 });

export function useSupportDraft(staff: number, thread: number, mode: string, route: string) {
  const legacyKey = `tybee:draft:${staff}:${thread}:${mode}`;
  const key = `${legacyKey}:${route}`;
  const load = (): Draft => {
    try {
      if (route !== "loading" && !localStorage.getItem(key) && localStorage.getItem(legacyKey)) {
        localStorage.setItem(key, localStorage.getItem(legacyKey)!);
        localStorage.removeItem(legacyKey);
      }
      const value = JSON.parse(localStorage.getItem(key) ?? "null");
      if (value && typeof value.text === "string" && typeof value.requestKey === "string" && Date.now() - value.savedAt < 7 * 86400000) return value;
      localStorage.removeItem(key);
    } catch { /* unavailable storage must not prevent typing */ }
    return blank();
  };
  const [state, setState] = useState(() => ({ key, draft: load() }));
  const [fileState, setFileState] = useState<{ key: string; file: File | null }>(() => ({ key, file: files.get(key) ?? null }));
  if (state.key !== key) setState({ key, draft: load() });
  if (fileState.key !== key) setFileState({ key, file: files.get(key) ?? null });
  const draft = state.draft;
  const file = fileState.file;
  const [storageError, setStorageError] = useState(false);
  const save = (value: Draft) => {
    setState({ key, draft: value });
    try {
      if (!value.text && !value.fileName && !value.nextStep && !value.dueAt) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify(value));
      setStorageError(false);
    } catch { setStorageError(true); }
  };
  return {
    nextStep: draft.nextStep ?? "", dueAt: draft.dueAt ?? "",
    setNextStep: (value: string) => save({ ...draft, nextStep: value, savedAt: Date.now() }),
    setDueAt: (value: string) => save({ ...draft, dueAt: value, savedAt: Date.now() }),
    text: draft.text, file, requestKey: draft.requestKey, savedAt: draft.savedAt, storageError,
    missingFile: !file && !!draft.fileName,
    setText: (text: string) => save({ ...draft, text, requestKey: newRequestKey(), savedAt: Date.now() }),
    setFile: (value: File | null) => {
      if (value) files.set(key, value); else files.delete(key);
      setFileState({ key, file: value });
      save({ ...draft, fileName: value?.name, requestKey: newRequestKey(), savedAt: Date.now() });
    },
    clear: () => { files.delete(key); setFileState({ key, file: null }); save(blank()); },
  };
}
