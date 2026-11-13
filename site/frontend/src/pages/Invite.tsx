import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, json } from "../api";
import { SkeletonText } from "../Skeletons";
import { ThemeToggle } from "../theme";
import { InteractiveGrinya } from "../TybeeLogo";

export default function Invite() {
  const { token } = useParams();
  const box = useRef<HTMLDivElement>(null);
  const [cfg, setCfg] = useState<{ bot_username: string; dev_login: boolean } | null>(null);
  const [error, setError] = useState("");
  const [devId, setDevId] = useState("");
  const info = useQuery({
    queryKey: ["invite", token], retry: false,
    queryFn: () => api<{ role_label: string; position: string | null }>(`/api/invites/${token}`),
  });

  const accept = async (payload: unknown) => {
    try { await api(`/api/invites/${token}/accept`, json("POST", payload)); window.location.assign("/me"); }
    catch (e: any) { setError(e.message); }
  };

  useEffect(() => { api("/api/auth/config").then(setCfg).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    if (!cfg?.bot_username || !box.current || !info.data) return;
    (window as any).onTelegramAuth = accept;
    const s = document.createElement("script");
    s.src = "https://telegram.org/js/telegram-widget.js?22";
    s.async = true;
    s.setAttribute("data-telegram-login", cfg.bot_username);
    s.setAttribute("data-size", "large");
    s.setAttribute("data-onauth", "onTelegramAuth(user)");
    s.setAttribute("data-request-access", "write");
    box.current.replaceChildren(s);
  }, [cfg, info.data]);

  return (
    <div className="center">
      <ThemeToggle className="login-theme" />
      <Card className="card login">
        <div className="login-cat-wrapper">
          <InteractiveGrinya width={112} />
        </div>
        <div className="login-brand-row">
          <span className="login-brand-name">tybee</span>
          <span className="login-title-tag">приглашение</span>
        </div>
        {info.error && <p className="error">{(info.error as Error).message}</p>}
        {info.isLoading && <SkeletonText lines={2} />}
        {info.data && (
          <>
            <p style={{ marginTop: 12 }}>Вас приглашают в команду Тайби как <b>{info.data.role_label}</b>{info.data.position ? ` (${info.data.position})` : ""}.</p>
            <p className="muted">Войдите через Telegram — ваш ID подставится автоматически.</p>
            <div ref={box} className="widget" />
            {cfg && !cfg.bot_username && !cfg.dev_login && <p className="error">Вход через Telegram не настроен.</p>}
            {cfg?.dev_login && (
              <div className="form">
                <Input placeholder="dev: ваш Telegram ID" value={devId} onChange={(e) => setDevId(e.target.value)} />
                <Button disabled={!devId} onClick={() => accept({ id: Number(devId) })}>Принять (dev-режим)</Button>
              </div>
            )}
          </>
        )}
        {error && <p className="error">{error}</p>}
      </Card>
    </div>
  );
}
