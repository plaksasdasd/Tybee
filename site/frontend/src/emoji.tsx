// Emoji picker for the cover editor. Every button keeps focus where it was
// (mousedown is cancelled), so the emoji lands at the caret of whatever field
// the user was typing in — the textarea or the editable preview.
import { useEffect, useRef, useState } from "react";

const GROUPS: [string, string, string][] = [
  ["😀", "Смайлы", "😀 😃 😄 😁 😆 😅 😂 🤣 😊 😇 🙂 🙃 😉 😌 😍 🥰 😘 😋 😛 😜 🤪 😎 🤩 🥳 😏 😒 😔 😟 🙁 😣 😫 🥺 😢 😭 😤 😠 😡 🤯 😳 😱 😨 🤗 🤔 🤭 🤫 😶 😐 😑 😬 🙄 😯 😴 🤤 😵 🤐 🤑 🤠 🤓 🧐 😈 👻 💀 🤖 👽 💩"],
  ["👍", "Жесты", "👍 👎 👌 🤌 ✌️ 🤞 🤟 🤘 🤙 👈 👉 👆 👇 ☝️ ✋ 🤚 🖐 👋 🤝 👏 🙌 👐 🤲 🙏 ✍️ 💪 👀 👁 🧠 👤 👥 🙋 🙅 🙆 🤷 🤦 💁 🧑‍💻 👨‍💼 👩‍💼 🧑‍🔧 🕵️ 🦸 🏃 🚶"],
  ["❤️", "Сердца", "❤️ 🧡 💛 💚 💙 💜 🖤 🤍 🤎 💔 ❣️ 💕 💞 💓 💗 💖 💘 💝 💟 ♥️ 🔥 ✨ ⭐ 🌟 💫 ⚡ 💥 💯 🎉 🎊 🎁 🎈 🏆 🥇 🥈 🥉 🏅 🎯"],
  ["📦", "Покупки", "🛒 🛍 📦 📫 📮 🏷 💳 💰 💵 💶 💷 💴 💸 🪙 🧾 💎 👕 👖 👗 👟 👠 👜 👛 🎒 🕶 ⌚ 📱 💻 🖥 🎧 📷 🎮 🧸 🪆 🖼 🧴 💄 🔑 🎫"],
  ["🚚", "Доставка", "🚚 🚛 🚗 🚕 🚌 🛵 🚲 ✈️ 🛫 🛬 🚢 ⛴ 🚀 🚁 🚂 🛃 🛂 🏁 🗺 🧭 🌍 🌎 🌏 🏠 🏢 🏬 🏪 🏭 🏙 ⏱ ⏳ ⌛ ⏰ 📅 📆 🕐"],
  ["✅", "Знаки", "✅ ☑️ ✔️ ❌ ❎ ⛔ 🚫 ⚠️ ❗ ❕ ❓ ❔ ‼️ ⁉️ 🔴 🟠 🟡 🟢 🔵 🟣 ⚫ ⚪ 🟥 🟧 🟨 🟩 🟦 🟪 ⬛ ⬜ 🔶 🔷 🔸 🔹 ▶️ ⏩ ⏪ 🔄 🔁 ➡️ ⬅️ ⬆️ ⬇️ ↗️ ↘️ 🔝 🆕 🆓 🆗 🆘 ℹ️ 🔞 💬 💭 🗨 📢 📣 🔔 🔕 🔒 🔓 🔗 📌 📍 ✏️ 📝 📄 📋 📊 📈 📉 🔍 🛟 ⚙️ 🛠 👇 👆 👉 👈 №"],
  ["🇷🇺", "Флаги", "🇷🇺 🇺🇸 🇯🇵 🇨🇳 🇰🇷 🇬🇧 🇩🇪 🇫🇷 🇮🇹 🇪🇸 🇳🇱 🇹🇷 🇦🇪 🇿🇦 🇰🇿 🇧🇾 🇦🇲 🇬🇪 🇺🇿 🇮🇳 🇹🇭 🇻🇳 🇨🇦 🇦🇺 🇧🇷 🇲🇽 🇪🇺 🏳️ 🏴 🚩"],
];
const RECENT_KEY = "tybee.emoji.recent";

function loadRecent(): string[] {
  try { const v = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]"); return Array.isArray(v) ? v.slice(0, 24) : []; }
  catch { return []; }
}

export function EmojiPicker({ onPick, className = "" }: { onPick: (emoji: string) => void; className?: string }) {
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState(0);
  const [recent, setRecent] = useState<string[]>(loadRecent);
  const box = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  const pick = (e: string) => {
    onPick(e);
    const next = [e, ...recent.filter((x) => x !== e)].slice(0, 24);
    setRecent(next);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch { /* private mode */ }
  };

  const tabs: [string, string, string[]][] = [
    ...(recent.length ? [["🕘", "Недавние", recent] as [string, string, string[]]] : []),
    ...GROUPS.map(([icon, title, list]) => [icon, title, list.split(" ")] as [string, string, string[]]),
  ];
  const cur = tabs[Math.min(group, tabs.length - 1)];
  const keep = (e: { preventDefault: () => void }) => e.preventDefault();

  return (
    <span ref={box} className={"emoji " + className}>
      <button type="button" className={"tool emoji-toggle" + (open ? " on" : "")} title="Смайлики"
        onMouseDown={keep} onClick={() => setOpen((o) => !o)}>☺</button>
      {open && (
        <div className="emoji-pop" onMouseDown={keep}>
          <div className="emoji-tabs">
            {tabs.map(([icon, title], i) => (
              <button key={title} type="button" title={title} className={tabs[i] === cur ? "on" : ""} onClick={() => setGroup(i)}>{icon}</button>
            ))}
          </div>
          <div className="emoji-title">{cur[1]}</div>
          <div className="emoji-grid">
            {cur[2].map((e) => <button key={e} type="button" onClick={() => pick(e)}>{e}</button>)}
          </div>
        </div>
      )}
    </span>
  );
}
