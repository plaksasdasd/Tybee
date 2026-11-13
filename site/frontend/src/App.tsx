import { Button } from "@/components/ui/button";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, Me } from "./api";
import { ShiftControl, useSupportSummary } from "./Shift";
import { useRealtime, useRealtimeState } from "./realtime";
const Login = lazy(() => import("./pages/Login"));
const CoversList = lazy(() => import("./pages/CoversList"));
const CoverNew = lazy(() => import("./pages/CoverNew"));
const CoverEdit = lazy(() => import("./pages/CoverEdit"));
const Cabinet = lazy(() => import("./pages/Cabinet"));
const Staff = lazy(() => import("./pages/Staff"));
const Audit = lazy(() => import("./pages/Audit"));
const Invite = lazy(() => import("./pages/Invite"));
const SupportTemplates = lazy(() => import("./pages/SupportTemplates"));
const Support = lazy(() => import("./pages/Support"));
const Finance = lazy(() => import("./pages/Finance"));
const Leaders = lazy(() => import("./pages/Leaders"));
const Avito = lazy(() => import("./pages/Avito"));
const Catalog = lazy(() => import("./pages/Catalog"));
const Broadcasts = lazy(() => import("./pages/Broadcasts"));
const LoginBot = lazy(() => import("./pages/LoginBot"));
import ErrorBoundary from "./ErrorBoundary";
import { Icon } from "./icons";
import { ThemeToggle } from "./theme";
import { AppShellSkeleton, AuthSkeleton, PageSkeleton } from "./Skeletons";
import { OfflineBanner } from "./OfflineBanner";
import { Toaster } from "./Toaster";
import { primeAudio, useSupportAlerts } from "./notify";
import { useScrollFades } from "./scrollFades";
import { GuideDialog, useGuideAutoOpen } from "./Guide";

// Menu entries gated by permission; new sections (support, finance, ...) will be added here.
const MENU: { to: string; label: string; icon: string; perm?: string }[] = [
  { to: "/me", icon: "me", label: "Кабинет" },
  { to: "/support", icon: "support", label: "Саппорт", perm: "support.chat" },
  { to: "/leaders", icon: "leaders", label: "Лидеры" },
  { to: "/support-templates", icon: "support", label: "Шаблоны ответов", perm: "support.templates.manage" },
  { to: "/finance", icon: "finance", label: "Финансы", perm: "finance.*" },
  { to: "/covers", icon: "covers", label: "Обложки", perm: "covers.manage" },
  { to: "/login-bot", icon: "covers", label: "Бот входа", perm: "covers.manage" },
  { to: "/catalog", icon: "catalog", label: "Каталог", perm: "catalog.manage" },
  { to: "/broadcasts", icon: "broadcasts", label: "Рассылки", perm: "broadcast.send" },
  { to: "/avito", icon: "avito", label: "Avito", perm: "avito.manage" },
  { to: "/staff", icon: "staff", label: "Сотрудники", perm: "staff.view" },
  { to: "/audit", icon: "audit", label: "Аудит", perm: "audit.view" },
];

const initials = (name: string) =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("") || "?";

const homeFor = (me: Me) => (me.permissions.includes("covers.manage") ? "/covers" : "/me");

// Wide tables on phones get a "scroll" hint, but only when they really overflow.
function useTableScrollHints(key: string) {
  useEffect(() => {
    const mark = (el: HTMLElement) => {
      const table = el.querySelector("table");
      const width = table ? table.getBoundingClientRect().width : el.scrollWidth;
      el.classList.toggle("is-scrollable", width > el.clientWidth + 2);
    };
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) if (entry.target instanceof HTMLElement) mark(entry.target);
    });
    const observed = new WeakSet<HTMLElement>();
    const scan = () => document.querySelectorAll<HTMLElement>('[data-slot="table-container"]').forEach((el) => {
      mark(el);
      if (!observed.has(el)) { observed.add(el); ro.observe(el); }
    });
    let frame = 0;
    const schedule = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; scan(); }); };
    scan();
    const mo = new MutationObserver(schedule);
    mo.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", schedule);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      mo.disconnect();
      ro.disconnect();
      window.removeEventListener("resize", schedule);
    };
  }, [key]);
}

function Layout({ me }: { me: Me }) {
  const { pathname } = useLocation();
  const [leaving, setLeaving] = useState(false);
  const [sectionsOpen, setSectionsOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  useGuideAutoOpen(me, setGuideOpen);
  useTableScrollHints(pathname);
  // The phone menu is an overlay: close it on Escape and on route change.
  // While the tour runs it may open the drawer on purpose, so leave it alone.
  useEffect(() => { if (!guideOpen) setSectionsOpen(false); }, [pathname, guideOpen]);
  useEffect(() => {
    if (!sectionsOpen) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setSectionsOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sectionsOpen]);
  // The phone drawer covers the page, so the page behind must not scroll under it.
  useEffect(() => {
    if (!sectionsOpen) return;
    const html = document.documentElement;
    const prevBody = document.body.style.overflow;
    const prevHtml = html.style.overflow;
    document.body.style.overflow = "hidden";
    html.style.overflow = "hidden";
    return () => { document.body.style.overflow = prevBody; html.style.overflow = prevHtml; };
  }, [sectionsOpen]);
  const logout = async () => {
    setLeaving(true);
    try { await api("/api/auth/logout", { method: "POST" }); } catch { /* the session is gone either way */ }
    // A full reload drops every cached query and timer, so nothing of the old session survives.
    for (const key of Object.keys(localStorage)) if (key.startsWith(`tybee:draft:${me.telegram_id}:`)) localStorage.removeItem(key);
    window.location.replace("/login");
  };
  const summary = useSupportSummary(me.permissions.includes("support.chat"));
  // One socket per tab: pushes replace polling for every page of the panel.
  useRealtime(true);
  const { connected: live } = useRealtimeState();
  const has = (p: string) => me.permissions.includes(p);
  const navigate = useNavigate();
  const openSupport = useCallback(() => navigate("/support"), [navigate]);
  useSupportAlerts(summary.data, has("support.chat"), openSupport);
  const anyFinance = me.permissions.some((p) => p.startsWith("finance.")) || has("buyout.manage");
  const items = MENU.filter((m) => !m.perm || (m.perm === "finance.*" ? anyFinance : has(m.perm)));
  const finSummary = useQuery({
    queryKey: ["fin-summary"],
    queryFn: () => api<{ requested: number }>("/api/finance/summary"),
    enabled: has("finance.payouts.approve"),
    refetchInterval: live ? 60_000 : 15_000,
  });
  return (
    <div className="layout">
      <aside className={sectionsOpen ? `menu-open${guideOpen ? " menu-tour" : ""}` : undefined} onKeyDown={(e) => { if (e.key === "Escape") setSectionsOpen(false); }}>
        {sectionsOpen && <div className="menu-backdrop" aria-hidden="true" onClick={() => setSectionsOpen(false)} />}
        <div className="brand">
          <a href="https://tybee.ru" target="_blank" rel="noreferrer" className="brand-logo-link" title="Перейти на сайт tybee.ru">
            <img src="/logobig.png" alt="tybee" className="brand-logo-img" />
          </a>
          <span className="brand-tag">панель</span>
          <ThemeToggle className="brand-theme" />
        </div>
        <nav id="main-navigation" aria-label="Основная навигация">
          {items.map((m) => (
            <NavLink key={m.to} to={m.to} data-guide={`nav:${m.to}`} onClick={() => setSectionsOpen(false)}>
              <Icon name={m.icon} />
              <span>{m.label}{m.to === "/finance" && !!finSummary.data?.requested && <small className="finance-nav-detail">Заявки на выплату</small>}</span>
              {m.to === "/support" && !!summary.data?.unread && <span className="badge bad nav-badge">{summary.data.unread}</span>}
              {m.to === "/finance" && !!finSummary.data?.requested && <span className="badge bad nav-badge" aria-label={`Заявок на выплату ожидают решения: ${finSummary.data.requested}`}>{finSummary.data.requested}</span>}
            </NavLink>
          ))}
          <a href="https://tybee.ru" target="_blank" rel="noreferrer" className="nav-store-link" title="Открыть витрину tybee.ru">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="10" />
              <path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20M2 12h20" />
            </svg>
            <span>Витрина tybee.ru</span>
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginLeft: "auto", opacity: 0.6 }}>
              <path d="M7 17l9.2-9.2M17 17V8H8" />
            </svg>
          </a>
          <button type="button" className="guide-link" onClick={() => { setSectionsOpen(false); setGuideOpen(true); }}>
            <Icon name="guide" />
            <span>Интерактивный гайд</span>
          </button>
        </nav>
        {has("support.chat") && <ShiftControl />}
        <div className="me">
          <span className="avatar">{initials(me.display_name)}</span>
          <div className="me-text">
            <div>{me.display_name}</div>
            <small>{me.username ? `@${me.username} · ` : ""}{me.role_label}</small>
          </div>
          <ThemeToggle className="me-theme" />
          <Button className="logout" variant="ghost" title="Выйти из аккаунта" disabled={leaving} onClick={logout}><Icon name="logout" /><span>{leaving ? "Выходим…" : "Выйти"}</span></Button>
        </div>
        <button type="button" className="mobile-menu-toggle" aria-label={sectionsOpen ? "Закрыть меню" : "Открыть меню"} aria-expanded={sectionsOpen} aria-controls="main-navigation" onClick={() => setSectionsOpen(!sectionsOpen)}><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">{sectionsOpen ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 6h16M4 12h16M4 18h16" />}</svg></button>
      </aside>
      <main className={pathname === "/support" || pathname.startsWith("/support/") ? "support-page" : undefined}>
        <ErrorBoundary resetKey={pathname}>
        <Suspense fallback={<PageSkeleton />}>
        <Routes>
          <Route path="/me" element={<Cabinet me={me} />} />
          <Route path="/leaders" element={<Leaders me={me} />} />
          {anyFinance && <Route path="/finance" element={<Finance me={me} />} />}
          {has("support.templates.manage") && <Route path="/support-templates" element={<SupportTemplates />} />}
          {has("support.chat") && <Route path="/support" element={<Support me={me} />} />}
          {has("support.chat") && <Route path="/support/:id" element={<Support me={me} />} />}
          {has("covers.manage") && <Route path="/covers" element={<CoversList />} />}
          {has("covers.manage") && <Route path="/covers/new" element={<CoverNew />} />}
          {has("covers.manage") && <Route path="/covers/:slug" element={<CoverEdit />} />}
          {has("covers.manage") && <Route path="/login-bot" element={<LoginBot />} />}
          {has("avito.manage") && <Route path="/avito" element={<Avito />} />}
          {has("catalog.manage") && <Route path="/catalog" element={<Catalog />} />}
          {has("broadcast.send") && <Route path="/broadcasts" element={<Broadcasts />} />}
          {has("staff.view") && <Route path="/staff" element={<Staff />} />}
          {has("audit.view") && <Route path="/audit" element={<Audit />} />}
          <Route path="*" element={<Navigate to={homeFor(me)} replace />} />
        </Routes>
        </Suspense>
        </ErrorBoundary>
      </main>
      <GuideDialog me={me} open={guideOpen} onOpenChange={setGuideOpen} onMenuChange={setSectionsOpen} />
    </div>
  );
}

export default function App() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/auth/me") });
  const { pathname } = useLocation();
  useScrollFades();
  // Browsers only allow sound after a gesture, so arm the audio context early.
  useEffect(() => {
    const arm = () => primeAudio();
    window.addEventListener("pointerdown", arm, { once: true, capture: true });
    window.addEventListener("keydown", arm, { once: true, capture: true });
    return () => {
      window.removeEventListener("pointerdown", arm, { capture: true });
      window.removeEventListener("keydown", arm, { capture: true });
    };
  }, []);
  // No full-screen spinner while the session is being checked: show the frame
  // of the panel, the same shape the real layout will take.
  if (me.isLoading) return <><AppShellSkeleton /><OfflineBanner /><Toaster /></>;
  return (
    <>
      <ErrorBoundary resetKey={pathname}>
      <Suspense fallback={<AuthSkeleton />}><Routes>
        <Route path="/invite/:token" element={<Invite />} />
        <Route path="/login" element={me.data ? <Navigate to={homeFor(me.data)} replace /> : <Login />} />
        <Route path="/*" element={me.data ? <Layout me={me.data} /> : <Navigate to="/login" replace />} />
      </Routes></Suspense>
      </ErrorBoundary>
      <OfflineBanner />
      <Toaster />
    </>
  );
}
