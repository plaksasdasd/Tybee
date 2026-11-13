import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, json } from "../api";
import { CustomSelect } from "../Controls";
import { Skeleton } from "../Skeletons";
import { ThemeToggle } from "../theme";
import { InteractiveGrinya } from "../TybeeLogo";

type BotLogin = { url: string; bot_username: string; expires_in: number };
type DevStaff = { telegram_id: number; display_name: string; role: string; role_label: string };

const DEV_PICK_KEY = "tybee.dev-staff";

const widgetHostOk = () => {
  const h = window.location.hostname;
  return !(h === "localhost" || h.endsWith(".localhost") || /^[\d.]+$/.test(h) || h.includes(":"));
};

export default function Login() {
  const box = useRef<HTMLDivElement>(null);
  const [cfg, setCfg] = useState<{ bot_username: string; dev_login: boolean } | null>(null);
  const [error, setError] = useState(() => {
    const link = new URLSearchParams(window.location.search).get("link");
    if (link === "expired") return "Ссылка из бота устарела или уже использована — отправьте боту /start, чтобы получить новую.";
    if (link === "denied") return "Доступ к панели отключён — обратитесь к руководителю.";
    return "";
  });
  const [botLogin, setBotLogin] = useState<BotLogin | null>(null);
  const [devStaff, setDevStaff] = useState<DevStaff[] | null>(null);
  const [devId, setDevId] = useState(() => window.localStorage.getItem(DEV_PICK_KEY) ?? "");
  const qc = useQueryClient();
  const nav = useNavigate();

  const done = async () => {
    await qc.invalidateQueries({ queryKey: ["me"] });
    nav("/", { replace: true });
  };

  useEffect(() => { api("/api/auth/config").then(setCfg).catch((e) => setError(e.message)); }, []);

  // Telegram Login Widget: works only on the domain set via /setdomain in @BotFather,
  // so on localhost / bare IPs it can only show "Bot domain invalid" — skip it there.
  useEffect(() => {
    if (!cfg?.bot_username || !box.current || !widgetHostOk()) return;
    (window as any).onTelegramAuth = async (user: unknown) => {
      try { await api("/api/auth/telegram", json("POST", user)); await done(); }
      catch (e: any) { setError(e.message); }
    };
    const s = document.createElement("script");
    s.src = "https://telegram.org/js/telegram-widget.js?22";
    s.async = true;
    s.setAttribute("data-telegram-login", cfg.bot_username);
    s.setAttribute("data-size", "large");
    s.setAttribute("data-onauth", "onTelegramAuth(user)");
    s.setAttribute("data-request-access", "write");
    box.current.replaceChildren(s);
  }, [cfg]);

  // Sign-in through the login bot: poll until the user confirms in Telegram.
  useEffect(() => {
    if (!botLogin) return;
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const r = await api<{ status: string }>("/api/auth/bot-login");
        if (stopped) return;
        if (r.status === "ok") { window.clearInterval(timer); await done(); }
        else if (r.status === "expired") {
          window.clearInterval(timer);
          setBotLogin(null);
          setError("Ссылка для входа устарела — нажмите кнопку ещё раз.");
        }
      } catch (e: any) {
        window.clearInterval(timer);
        setBotLogin(null);
        setError(e.message);
      }
    }, 2000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [botLogin]);

  const startBotLogin = async () => {
    setError("");
    // Open the tab synchronously so popup blockers allow it, then point it at the bot.
    const tab = window.open("", "_blank");
    try {
      const r = await api<BotLogin>("/api/auth/bot-login", { method: "POST" });
      setBotLogin(r);
      if (tab) tab.location.href = r.url;
    } catch (e: any) {
      tab?.close();
      setError(e.message);
    }
  };

  const dev = async () => {
    try {
      setError("");
      await api("/api/auth/dev-login", json("POST", devId ? { telegram_id: Number(devId) } : {}));
      await done();
    }
    catch (e: any) { setError(e.message); }
  };

  // Dev mode: list the whole team so any role can be checked without a Telegram account.
  useEffect(() => {
    if (!cfg?.dev_login) return;
    api<{ items?: DevStaff[] } | DevStaff[]>("/api/auth/dev-staff")
      .then((r) => {
        const items = Array.isArray(r) ? r : (Array.isArray(r?.items) ? r.items : []);
        setDevStaff(items);
        setDevId((current) => {
          if (items.some((s) => String(s.telegram_id) === current)) return current;
          // The first row can be a placeholder like "1"; prefer a person with a real name.
          const named = items.find((s) => /[^\d\s]/.test(s.display_name));
          return String((named ?? items[0])?.telegram_id ?? "");
        });
      })
      .catch((e: any) => { setError(e.message); setDevStaff([]); });
  }, [cfg?.dev_login]);

  const pickDev = (value: string) => {
    setDevId(value);
    window.localStorage.setItem(DEV_PICK_KEY, value);
  };
  const devChosen = devStaff?.find((s) => String(s.telegram_id) === devId) ?? null;
  // Sorted by role on the server, so equal roles are already neighbours.
  const devGroups = (devStaff ?? []).reduce<{ label: string; items: DevStaff[] }[]>((groups, person) => {
    const last = groups[groups.length - 1];
    if (last && last.label === person.role_label) last.items.push(person);
    else groups.push({ label: person.role_label, items: [person] });
    return groups;
  }, []);

  return (
    <div className="center">
      <ThemeToggle className="login-theme" />
      <Card className="card login">
        <div className="login-cat-wrapper">
          <InteractiveGrinya width={118} />
        </div>
        <div className="login-brand-row">
          <span className="login-brand-name">tybee</span>
          <span className="login-title-tag">панель</span>
        </div>
        <p className="muted">Личный байер иностранных товаров · Вход для сотрудников</p>
        {!cfg && <div className="" aria-hidden="true"><Skeleton style={{ width: "100%", height: 42, borderRadius: 9, marginTop: 8 }} /><Skeleton style={{ width: "62%", height: 12, marginTop: 14 }} /></div>}
        {widgetHostOk() && <div ref={box} className="widget" />}
        {cfg?.bot_username && !botLogin && (
          <Button className="login-tg-btn" onClick={startBotLogin}>
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true" style={{ marginRight: 6 }}>
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 0 0-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.74-.55 2.92-1.27 4.86-2.11 5.83-2.52 2.77-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .38z"/>
            </svg>
            <span>Войти через Telegram-бота</span>
          </Button>
        )}
        {botLogin && (
          <div className="bot-login">
            <p>
              Откройте <a href={botLogin.url} target="_blank" rel="noreferrer">@{botLogin.bot_username}</a>,
              нажмите «Запустить» и «Подтвердить вход». Страница обновится сама.
            </p>
            <p className="muted">Ждём подтверждения… <Button className="link" onClick={() => setBotLogin(null)}>отмена</Button></p>
          </div>
        )}
        {cfg && !cfg.bot_username && !cfg.dev_login && (
          <p className="error">Бот входа не настроен. Обратитесь к администратору.</p>
        )}
        {cfg?.dev_login && (
          <div className="dev-login">
            <p className="muted">Dev-режим: вход под любым сотрудником, без подтверждения Telegram.</p>
            {devStaff === null
              ? <Skeleton style={{ width: "100%", height: 38, borderRadius: 9 }} />
              : devStaff.length > 0 ? (
                <>
                  <CustomSelect value={devId} onChange={(e) => pickDev(e.target.value)} aria-label="Сотрудник для dev-входа">
                    {devGroups.map((group) => (
                      <optgroup key={group.label} label={group.label}>
                        {group.items.map((person) => (
                          <option key={person.telegram_id} value={person.telegram_id}>{person.display_name}</option>
                        ))}
                      </optgroup>
                    ))}
                  </CustomSelect>
                  {devChosen && <p className="muted dev-login-role">Роль: {devChosen.role_label}</p>}
                </>
              ) : <p className="error">Нет активных сотрудников для dev-входа.</p>}
            <Button className="ghost" disabled={!devStaff?.length} onClick={dev}>
              {devChosen ? `Войти как ${devChosen.display_name}` : "Войти (dev-режим)"}
            </Button>
          </div>
        )}
        {error && <p className="error">{error}</p>}
        <div className="login-footer">
          <a href="https://tybee.ru" target="_blank" rel="noreferrer">
            <span>Перейти на витрину tybee.ru</span>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M7 17l9.2-9.2M17 17V8H8" />
            </svg>
          </a>
        </div>
      </Card>
    </div>
  );
}
