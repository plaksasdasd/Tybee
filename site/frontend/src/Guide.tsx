import { Button } from "@/components/ui/button";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Me } from "./api";
import { Icon } from "./icons";

// "Интерактивный гайд": a spotlight tour over the real panel. Every step points
// at a live element through a data-guide anchor, dims the rest of the page and
// puts a short explanation next to it. The content is static on purpose — it
// must stay true when the server is down and it needs no permissions of its own.

type Step = {
  icon: string;
  title: string;
  text: string;
  /** Section the step is about; the tour opens it before looking for the anchor. */
  to?: string;
  /** Permission the section needs; the step is hidden without it. */
  need?: string;
  /** data-guide anchor of the real element this step highlights. */
  target?: string;
  /** Anchor to fall back to when the target is not on the page (e.g. no dialog open). */
  fallback?: string;
  /** Shown instead of nothing when the real target is not on the page yet. */
  hint?: string;
};

type Guide = { lead: string; steps: Step[] };

/** Permission each section needs — the same gate the sidebar uses in App.tsx. */
const ROUTE_PERM: Record<string, string> = {
  "/support": "support.chat",
  "/support-templates": "support.templates.manage",
  "/finance": "finance.*",
  "/covers": "covers.manage",
  "/login-bot": "covers.manage",
  "/catalog": "catalog.manage",
  "/broadcasts": "broadcast.send",
  "/avito": "avito.manage",
  "/staff": "staff.view",
  "/audit": "audit.view",
};

const allows = (permissions: string[], need: string | undefined) => {
  if (!need) return true;
  // "a|b" means any of the permissions opens the section.
  return need.split("|").some((one) => {
    if (one === "finance.*") return permissions.some((p) => p.startsWith("finance.")) || permissions.includes("buyout.manage");
    return permissions.includes(one);
  });
};

const SHIFT: Step = {
  icon: "me",
  title: "Смена — пропуск в работу",
  need: "support.chat",
  target: "shift",
  text: "Кнопка «Начать смену» слева запускает рабочее время. Без смены панель не даст взять диалог, ответить клиенту или начать новый: она предложит начать смену и сама повторит действие. Смена завершается автоматически, если закрыть вкладку, не трогать мышь и клавиатуру или оставить клиента ждать слишком долго.",
};

const SUPPORT_LIST: Step = {
  icon: "support",
  title: "Обращения под рукой",
  target: "support-list",
  fallback: "nav:/support",
  text: "Слева — список диалогов. «Нужен ответ» показывает клиентов, которые ждут ответа, «Мои» — ваши ветки, «Очередь» — свободные. Наведите курсор на строку, чтобы увидеть последний заказ клиента целиком, не открывая диалог.",
  to: "/support",
};

const REPLY: Step = {
  icon: "support",
  title: "Ответ клиенту",
  target: "composer",
  hint: "Поле ответа появляется, когда диалог открыт и вы его ведёте.",
  fallback: "chat-head|chat|support-list|nav:/support",
  text: "Пишите текст, прикрепляйте файл скрепкой или перетаскиванием, вставляйте заготовку из шаблонов. Внутренняя заметка видна только сотрудникам. Черновик сохраняется на устройстве: можно закрыть вкладку и вернуться к нему.",
  to: "/support",
};

const NEW_DIALOG: Step = {
  icon: "support",
  title: "Написать клиенту первым",
  target: "start-dialog",
  fallback: "support-list",
  text: "Кнопка «Написать клиенту» ищет любого, кто открывал бота: по имени, @username, ID или #номеру заказа. Если диалог уже ведёт коллега, панель не станет его перехватывать, а откроет существующую ветку.",
  to: "/support",
};

const ORDERS: Step = {
  icon: "catalog",
  title: "Заказы в диалоге",
  need: "support.chat",
  target: "client-button",
  hint: "Откройте диалог в списке слева — покажу кнопку «Клиент и заказы».",
  fallback: "chat-head|chat|support-list|nav:/support",
  to: "/support",
  text: "Справа от чата — карточка клиента и его заказы. Оттуда меняются статусы, ставится напоминание, оформляется возврат и выставляется счёт. Набор действий зависит от ваших прав.",
};

const LEADERS: Step = {
  icon: "leaders",
  title: "Лидеры",
  target: "nav:/leaders",
  text: "Общий рейтинг поддержки за день, неделю и месяц: сообщения, новые клиенты, оплаченные заказы, отзывы. Это дружеское соревнование, а не табель — баллы видны всей команде.",
  to: "/leaders",
};

const CABINET: Step = {
  icon: "me",
  title: "Кабинет и деньги",
  target: "nav:/me",
  text: "В «Кабинете» — ваши права, баланс и выплаты. Заполните реквизиты, чтобы запросить выплату: заявка уйдёт на одобрение, а статус и комментарий появятся в списке ниже. Там же журнал начислений.",
  to: "/me",
};

const TEMPLATES: Step = {
  icon: "support",
  title: "Шаблоны ответов",
  target: "nav:/support-templates",
  text: "Общие заготовки для команды: приветствие, реквизиты, ответы на частые вопросы. Переменные вроде {client_name} подставляются в момент отправки.",
  to: "/support-templates",
};

const TEMPLATES_IN_CHAT: Step = {
  icon: "support",
  title: "Готовые ответы в диалоге",
  need: "support.chat",
  target: "templates",
  hint: "Кнопка шаблонов появляется в открытом диалоге, который вы ведёте.",
  fallback: "chat-head|chat|support-list|nav:/support",
  to: "/support",
  text: "Кнопка «Шаблоны ответов» в окне диалога вставляет частый ответ одним нажатием, а переменные вроде {client_name} подставляются автоматически. Набор заготовок ведёт администратор или руководитель поддержки.",
};

const STAFF: Step = {
  icon: "staff",
  title: "Команда",
  target: "nav:/staff",
  text: "«Сотрудники» — состав, роли и доступы, приглашения и оклады. Здесь же видно, кто сейчас на смене и сколько у кого открытых диалогов.",
  to: "/staff",
};

const AUDIT: Step = {
  icon: "audit",
  title: "Аудит",
  target: "nav:/audit",
  text: "Журнал действий: кто, когда и что изменил — входы, ответы, смены, деньги. Помогает разобрать спорную ситуацию по фактам.",
  to: "/audit",
};

const FIN_BONUSES: Step = {
  icon: "finance",
  title: "Премии и штрафы",
  target: "fin:ops",
  text: "«Финансы» → «Операции»: ручные начисления и списания с комментарием. Ошибочную операцию можно отменить — история и автор сохранятся.",
  to: "/finance",
};

const OPS_VIEW: Step = {
  icon: "finance",
  title: "Журнал операций",
  target: "fin:ops",
  text: "«Финансы» → «Операции»: все начисления и списания с автором и комментарием. Премии и штрафы оформляют администратор и руководитель поддержки, вам журнал показывает общую картину.",
  to: "/finance",
};

const FIN_PAYOUTS: Step = {
  icon: "finance",
  title: "Заявки на выплату",
  target: "fin:payouts",
  text: "«Финансы» → «Выплаты»: сотрудник просит выплату, вы одобряете или отклоняете с причиной, затем отмечаете перевод номером операции. Красный счётчик в меню показывает, сколько заявок ждёт решения.",
  to: "/finance",
};

const PAYROLL: Step = {
  icon: "finance",
  title: "Зарплата саппорта",
  need: "finance.analytics|finance.bonuses",
  target: "fin:payroll",
  text: "Вкладка «Зарплата саппорта» считает сумму за период: сообщения, новые клиенты, заказы, отзывы и оклад. Рядом видны ставки и правила расчёта — можно проверить каждую цифру.",
  to: "/finance",
};

const REVENUE: Step = {
  icon: "finance",
  title: "Выручка и расчёты",
  need: "finance.analytics",
  target: "fin:revenue",
  text: "«Выручка» показывает, сколько принесли заказы и обложки, «Расчёты с сотрудниками» — сколько осталось после возвратов, штрафов и выплат.",
  to: "/finance",
};

const LOGISTICS: Step = {
  icon: "finance",
  title: "Логистика",
  need: "buyout.manage|finance.payouts.approve",
  target: "fin:logistics",
  text: "«Расходы на логистику» — затраты по заказам на выкуп: доставка, таможня, пересылка. Видно, сколько ушло по каждому заказу и где он сейчас.",
  to: "/finance",
};

const REFUNDS: Step = {
  icon: "catalog",
  title: "Возвраты и счета",
  need: "refunds.manage",
  target: "client-button",
  hint: "Откройте диалог в списке слева — покажу кнопку «Клиент и заказы».",
  fallback: "chat-head|chat|support-list|nav:/support",
  to: "/support",
  text: "Возврат оформляется в карточке заказа: сумма и причина, клиент получает уведомление. Счёт выставляется прямо из диалога и приходит клиенту в бота.",
};

const INVOICE: Step = {
  icon: "catalog",
  title: "Счёт клиенту",
  need: "orders.invoice",
  target: "client-button",
  hint: "Откройте диалог в списке слева — покажу кнопку «Клиент и заказы».",
  fallback: "chat-head|chat|support-list|nav:/support",
  text: "Счёт выставляется прямо из диалога: сумма и назначение приходят клиенту в бота, оплата подтягивается в карточку заказа. Оплату и возвраты видно в блоке операций.",
  to: "/support",
};

const BUYOUT: Step = {
  icon: "catalog",
  title: "Ведите заказ по статусам",
  need: "buyout.manage",
  target: "client-button",
  hint: "Откройте диалог в списке слева — покажу кнопку «Клиент и заказы».",
  fallback: "chat-head|chat|support-list|nav:/support",
  to: "/support",
  text: "Заказ на выкуп идёт по цепочке: «Выкуплено» → «Едет в Россию» → «Таможня» → «В России» → «Ожидает получения» → «Получено». Клиент видит каждый шаг в боте, поэтому статус должен совпадать с реальностью.",
};

const COVERS: Step = {
  icon: "covers",
  title: "Обложки — клиентские боты",
  target: "nav:/covers",
  text: "Здесь настраиваются тексты, кнопки, статусы и баннеры каждого бота, его включение и превью. Выключенная обложка перестаёт принимать клиентов, но история остаётся.",
  to: "/covers",
};

const LOGIN_BOT: Step = {
  icon: "covers",
  title: "Бот входа",
  target: "nav:/login-bot",
  text: "Отдельный бот для сотрудников: через него входят в панель по Telegram, без пароля. Настройки — в разделе «Бот входа».",
  to: "/login-bot",
};

const CATALOG: Step = {
  icon: "catalog",
  title: "Каталог",
  target: "nav:/catalog",
  text: "Товары и артикулы, которые видит клиент в боте. Можно добавлять по одному или импортировать список из Excel — панель покажет конфликты до записи.",
  to: "/catalog",
};

const BROADCASTS: Step = {
  icon: "broadcasts",
  title: "Рассылки",
  target: "nav:/broadcasts",
  text: "Сообщения по сегментам клиентов. Перед отправкой видно, сколько человек получит сообщение; история рассылок сохраняется.",
  to: "/broadcasts",
};

const AVITO: Step = {
  icon: "avito",
  title: "Avito",
  target: "nav:/avito",
  text: "Подключение и настройка чатов Avito. Такие диалоги приходят в те же обращения, но отвечать нужно из их ветки — бот Telegram туда не пишет.",
  to: "/avito",
};

const NOTIFY: Step = {
  icon: "me",
  title: "Не пропускайте клиентов",
  target: "notify",
  fallback: "nav:/me",
  text: "Звук и уведомления браузера включаются в «Кабинете». Счётчик непрочитанных всегда виден в заголовке вкладки, а «Нужен ответ» — в меню.",
  to: "/me",
};

const supportBase: Step[] = [SHIFT, SUPPORT_LIST, REPLY, NEW_DIALOG];

const GUIDES: Record<string, Guide> = {
  admin: {
    lead: "Всё, что есть в панели: поддержка, деньги, боты и команда. Начните со смены и обращений, остальное — по мере задач.",
    steps: [
      SHIFT,
      SUPPORT_LIST,
      REPLY,
      NEW_DIALOG,
      ORDERS,
      COVERS,
      CATALOG,
      BROADCASTS,
      STAFF,
      FIN_PAYOUTS,
      AUDIT,
      LEADERS,
    ],
  },
  support_lead: {
    lead: "Вы ведёте поддержку и команду: очередь, качество ответов, шаблоны, смены и выплаты.",
    steps: [
      SHIFT,
      SUPPORT_LIST,
      REPLY,
      NEW_DIALOG,
      ORDERS,
      TEMPLATES,
      STAFF,
      AUDIT,
      FIN_BONUSES,
      FIN_PAYOUTS,
      LEADERS,
      CABINET,
    ],
  },
  finance_manager: {
    lead: "Деньги платформы и людей: заявки на выплату, зарплата, операции, выручка и логистика.",
    steps: [CABINET, FIN_PAYOUTS, PAYROLL, OPS_VIEW, REVENUE, LOGISTICS],
  },
  arbitration_agent: {
    lead: "Спорные заказы и возвраты: диалог с клиентом, операции по заказу, счёт и возврат.",
    steps: [SHIFT, SUPPORT_LIST, REPLY, NEW_DIALOG, ORDERS, REFUNDS, LEADERS, CABINET],
  },
  buyout_agent: {
    lead: "Заказы на выкуп и их путь до клиента: статусы, логистика, расходы.",
    steps: [SHIFT, SUPPORT_LIST, REPLY, NEW_DIALOG, BUYOUT, LOGISTICS, LEADERS, CABINET],
  },
  client_manager_1: {
    lead: "Главное — вовремя ответить клиенту и довести его заказ до результата.",
    steps: [SHIFT, SUPPORT_LIST, REPLY, NEW_DIALOG, ORDERS, TEMPLATES_IN_CHAT, NOTIFY, LEADERS],
  },
  client_manager_2: {
    lead: "Диалоги, счета и операции по заказам: отвечайте клиенту и держите заказ в движении.",
    steps: [SHIFT, SUPPORT_LIST, REPLY, NEW_DIALOG, ORDERS, INVOICE, NOTIFY, LEADERS],
  },
};

const DEFAULT_GUIDE: Guide = {
  lead: "Короткое знакомство с панелью: смена, обращения и ответ клиенту.",
  steps: supportBase,
};

const guideFor = (me: Me): Guide => GUIDES[me.role] ?? DEFAULT_GUIDE;

const SEEN_KEY = (id: number) => `tybee:guide:v1:${id}`;

/** Opens the tour by itself the first time this employee sees the panel. */
export function useGuideAutoOpen(me: Me, setOpen: (open: boolean) => void) {
  useEffect(() => {
    let seen = true;
    try { seen = localStorage.getItem(SEEN_KEY(me.telegram_id)) === "1"; } catch { /* private mode */ }
    if (seen) return;
    const timer = window.setTimeout(() => setOpen(true), 900);
    return () => window.clearTimeout(timer);
  }, [me.telegram_id, setOpen]);
}

type Box = { top: number; left: number; width: number; height: number; sidebar?: boolean };

type Pos =
  | { kind: "float"; top: number; left: number; width: number; place: "above" | "below" | "right" | "wide"; arrow?: { left?: number; top?: number } }
  | { kind: "sheet"; side: "top" | "bottom" };

/** Breathing room between the highlighted element and the dimmed page. */
const HOLE_PAD = 6;

const sameBox = (a: Box | null, b: Box | null) =>
  (!a && !b) || (!!a && !!b && Math.abs(a.top - b.top) < 0.5 && Math.abs(a.left - b.left) < 0.5 && Math.abs(a.width - b.width) < 0.5 && Math.abs(a.height - b.height) < 0.5);

/** Anchors of a step in priority order: the target first, then its fallbacks. */
const anchorsOf = (step: Step) =>
  [step.target, ...(step.fallback ? step.fallback.split("|") : [])].filter(Boolean) as string[];

/** First anchor that is really on screen; a drawer or a lazy page may hide it. */
const anchorFor = (names: string[]) => names
  .map((name) => document.querySelector<HTMLElement>(`[data-guide="${name}"]`))
  .find((el): el is HTMLElement => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 8 && r.height > 8;
  }) ?? null;

export function GuideDialog({ me, open, onOpenChange, onMenuChange }: {
  me: Me; open: boolean; onOpenChange: (open: boolean) => void;
  /** Phone layout keeps the sections behind a drawer: the tour opens it when a step points there. */
  onMenuChange?: (open: boolean) => void;
}) {
  const guide = guideFor(me);
  // A step about a section the role cannot open would be a dead end: hide it.
  const steps = useMemo(() => {
    const visible = guide.steps.filter((item) => allows(me.permissions, item.need ?? (item.to ? ROUTE_PERM[item.to] : undefined)));
    return visible.length ? visible : guide.steps.slice(0, 1);
  }, [guide, me.permissions]);
  const [index, setIndex] = useState(0);
  const [box, setBox] = useState<Box | null>(null);
  const [pos, setPos] = useState<Pos | null>(null);
  const [viewport, setViewport] = useState({ w: 0, h: 0 });
  const [cardHeight, setCardHeight] = useState(0);
  const [onFallback, setOnFallback] = useState(false);
  const fallbackRef = useRef(false);
  const boxRef = useRef<Box | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const last = steps.length - 1;
  const step = steps[Math.min(index, last)];
  const isLast = index >= last;
  const compact = viewport.w > 0 && viewport.w <= 1000;

  const markSeen = useCallback(() => {
    try { localStorage.setItem(SEEN_KEY(me.telegram_id), "1"); } catch { /* private mode */ }
  }, [me.telegram_id]);

  const close = useCallback(() => { markSeen(); onOpenChange(false); }, [markSeen, onOpenChange]);
  const next = useCallback(() => {
    if (index >= last) { close(); return; }
    setIndex(index + 1);
  }, [index, last, close]);
  const prev = useCallback(() => setIndex((value) => Math.max(0, value - 1)), []);

  // Reopening the tour always starts from the first step.
  useEffect(() => { if (open) setIndex(0); }, [open]);

  // Opening a step may live in another section: go there, but only when the
  // anchor is not already around — an open dialog stays open under the tour.
  useEffect(() => {
    if (!open) return;
    if (!step.to) return;
    // "/support" also covers an open thread at "/support/12".
    if (pathname === step.to || pathname.startsWith(`${step.to}/`)) return;
    // A fallback in the sidebar always exists, so only a real target counts;
    // a menu item always exists too, and the section itself must be opened.
    if (step.target && !step.target.startsWith("nav:") && anchorFor([step.target])) return;
    navigate(step.to);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, step]);

  // Track the anchor every frame: it appears after a lazy page loads, moves when
  // the page scrolls and disappears when a dialog closes.
  useEffect(() => {
    if (!open) {
      boxRef.current = null;
      fallbackRef.current = false;
      setBox(null);
      setOnFallback(false);
      return;
    }
    let cancelled = false;
    let frame = 0;
    let scrollDone = false;
    let lastNavHeight = -1;
    let centeredOnce = false;
    const anchors = anchorsOf(step);
    const find = () => anchorFor(anchors);

    // Puts a drawer row into the visible band above the panel. The drawer keeps
    // resizing while the panel is measured, so this runs again when it does —
    // but never while the person scrolls the list themselves.
    const centerInNav = (el: HTMLElement, nav: HTMLElement) => {
      const navBox = nav.getBoundingClientRect();
      if (navBox.height <= 0) return;
      const r = el.getBoundingClientRect();
      const padding = 12;
      const band = navBox.height - padding * 2;
      const offset = r.top - navBox.top - padding;
      if (!centeredOnce || offset < 0 || offset + r.height > band) {
        nav.scrollTop += offset + r.height / 2 - navBox.height / 2;
        centeredOnce = true;
      }
    };

    const loop = () => {
      if (cancelled) return;
      const el = find();
      if (el) {
        const exact = !!step.target && el.getAttribute("data-guide") === step.target;
        if (fallbackRef.current !== !exact) { fallbackRef.current = !exact; setOnFallback(!exact); }
        const inSidebar = !!el.closest("aside");
        const r = el.getBoundingClientRect();
        const card = cardRef.current?.getBoundingClientRect();
        // Bring the element into view once, after the panel is measured: the
        // panel decides how much room is left above it.
        if (!scrollDone && card) {
          const nav = el.closest("nav");
          if (inSidebar && window.innerWidth <= 1000 && nav) {
            // The drawer is open only after the tour asks for it: wait for it.
            if (nav.getBoundingClientRect().height > 0) {
              scrollDone = true;
              centerInNav(el, nav);
              lastNavHeight = nav.getBoundingClientRect().height;
            }
          } else if (!inSidebar) {
            scrollDone = true;
            const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            el.scrollIntoView({ block: "center", inline: "nearest", behavior: reduce ? "auto" : "smooth" });
          } else {
            scrollDone = true;
          }
        } else if (scrollDone && inSidebar) {
          // The drawer changes height when the panel's text wraps differently.
          const nav = el.closest("nav");
          if (nav) {
            const h = nav.getBoundingClientRect().height;
            if (h > 0 && Math.abs(h - lastNavHeight) > 8) {
              lastNavHeight = h;
              centerInNav(el, nav);
            }
          }
        }
        const nextBox = {
          top: r.top - HOLE_PAD,
          left: r.left - HOLE_PAD,
          width: r.width + HOLE_PAD * 2,
          height: r.height + HOLE_PAD * 2,
          sidebar: inSidebar,
        };
        if (!sameBox(boxRef.current, nextBox)) {
          boxRef.current = nextBox;
          setBox(nextBox);
        }
      } else if (boxRef.current) {
        boxRef.current = null;
        setBox(null);
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => { cancelled = true; cancelAnimationFrame(frame); };
  }, [open, step, pathname]);

  // Viewport size decides between a card next to the target and a bottom sheet.
  useEffect(() => {
    if (!open) return;
    const measure = () => setViewport({ w: window.innerWidth, h: window.innerHeight });
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("orientationchange", measure);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("orientationchange", measure);
    };
  }, [open]);

  // Phone layout: a step about a section in the menu opens the drawer so the
  // item is really on screen; every other step closes it again.
  useEffect(() => {
    if (!onMenuChange) return;
    if (!open) { onMenuChange(false); return; }
    const inMenu = !!step.target?.startsWith("nav:") && viewport.w > 0 && viewport.w <= 1000;
    onMenuChange(inMenu);
  }, [open, step, viewport.w, pathname, onMenuChange]);

  // Keyboard: arrows move through the tour, Escape leaves it.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
      else if (event.key === "ArrowRight") { event.preventDefault(); next(); }
      else if (event.key === "ArrowLeft") { event.preventDefault(); prev(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close, next, prev]);

  // Focus the card so the step is announced; the page behind stays usable.
  useEffect(() => {
    if (open) cardRef.current?.focus();
  }, [open]);

  // The card grows when the step text wraps differently: re-place it then.
  useEffect(() => {
    const el = cardRef.current;
    if (!open || !el) return;
    const measure = () => setCardHeight(el.offsetHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [open]);

  // The phone drawer reserves exactly the panel's height, so the list and the
  // panel never overlap and no empty gap is left between them.
  useEffect(() => {
    const root = document.documentElement;
    if (!open || cardHeight <= 0) { root.style.removeProperty("--tour-card-h"); return; }
    root.style.setProperty("--tour-card-h", `${cardHeight}px`);
    return () => { root.style.removeProperty("--tour-card-h"); };
  }, [open, cardHeight]);

  // Place the card as a small floating panel next to the highlighted element.
  // On a phone it docks to whichever edge is farther from the highlight, so the
  // element itself is never covered.
  useLayoutEffect(() => {
    if (!open || !box) { setPos(null); return; }

    if (viewport.w <= 1000) {
      const targetMid = box.top + box.height / 2;
      // A menu step lives in the drawer: its card sits at the bottom.
      const side: "top" | "bottom" = box.sidebar ? "bottom" : targetMid > viewport.h / 2 ? "top" : "bottom";
      setPos({ kind: "sheet", side });
      return;
    }

    const width = Math.min(340, viewport.w - 24);
    const height = cardHeight || cardRef.current?.offsetHeight || 300;
    const gap = 12;
    const edge = 12;
    const clampTop = (value: number) => Math.max(edge, Math.min(value, viewport.h - height - edge));
    const clampLeft = (value: number) => Math.max(edge, Math.min(value, viewport.w - width - edge));
    const centerX = box.left + box.width / 2;
    const centerY = box.top + box.height / 2;
    const arrowX = (left: number) => Math.max(16, Math.min(centerX - left - 6, width - 28));

    // A target that fills most of the screen (the whole chat) gets the panel
    // docked to a corner instead of covering it.
    if (box.width * box.height > viewport.w * viewport.h * 0.42) {
      setPos({ kind: "float", top: viewport.h - height - edge, left: clampLeft(viewport.w - width - edge - 4), width, place: "wide" });
      return;
    }

    // A section in the sidebar: float to the right of the menu.
    if (box.sidebar && box.left + box.width + gap + width <= viewport.w - edge) {
      const top = clampTop(centerY - height / 2);
      setPos({ kind: "float", top, left: box.left + box.width + gap, width, place: "right", arrow: { top: Math.max(16, Math.min(centerY - top - 6, height - 28)) } });
      return;
    }

    const spaceBelow = viewport.h - (box.top + box.height) - gap - edge;
    const spaceAbove = box.top - gap - edge;
    const spaceRight = viewport.w - (box.left + box.width) - gap - edge;

    if (spaceBelow >= height || (spaceBelow >= spaceAbove && spaceBelow >= 160)) {
      const left = clampLeft(centerX - width / 2);
      const top = box.top + box.height + gap;
      setPos({ kind: "float", top: clampTop(top), left, width, place: "below", arrow: { left: arrowX(left) } });
      return;
    }
    if (spaceAbove >= height || spaceAbove >= 160) {
      const left = clampLeft(centerX - width / 2);
      const top = box.top - height - gap;
      setPos({ kind: "float", top: clampTop(top), left, width, place: "above", arrow: { left: arrowX(left) } });
      return;
    }
    if (spaceRight >= width) {
      const top = clampTop(centerY - height / 2);
      setPos({ kind: "float", top, left: box.left + box.width + gap, width, place: "right", arrow: { top: Math.max(16, Math.min(centerY - top - 6, height - 28)) } });
      return;
    }
    // No room around the target: a corner panel, still with a pointer.
    const left = clampLeft(box.left + box.width - width);
    const top = clampTop(centerY - height / 2);
    setPos({ kind: "float", top, left, width, place: "wide" });
  }, [open, box, viewport, cardHeight, step]);

  if (!open) return null;

  const float = pos?.kind === "float" ? pos : null;
  const sheet = pos?.kind === "sheet" ? pos : null;
  const cardClass = ["tour-card", !box && "is-center", sheet && "is-sheet", sheet && `is-sheet-${sheet.side}`, float && `is-${float.place}`]
    .filter(Boolean).join(" ");

  const top = box ? Math.max(0, box.top) : 0;
  const bottom = box ? Math.max(0, viewport.h - box.top - box.height) : 0;
  const left = box ? Math.max(0, box.left) : 0;
  const right = box ? Math.max(0, viewport.w - box.left - box.width) : 0;
  const holeH = box ? Math.max(0, Math.min(box.height, viewport.h - top)) : 0;
  const holeW = box ? Math.max(0, Math.min(box.width, viewport.w - left)) : 0;
  // The phone drawer is already a focused overlay: dimming it again would hide
  // the very item the step is about, so a menu step only draws the ring.
  const dim = !(box && compact && box.sidebar);

  return (
    <div className="tour" role="dialog" aria-modal="false" aria-label="Интерактивный гайд">
      {box ? (
        <>
          {dim && <>
            <div className="tour-shade" style={{ top: 0, left: 0, right: 0, height: top }} />
            <div className="tour-shade" style={{ top: viewport.h - bottom, left: 0, right: 0, height: bottom }} />
            <div className="tour-shade" style={{ top, left: 0, width: left, height: holeH }} />
            <div className="tour-shade" style={{ top, left: left + holeW, right: 0, height: holeH }} />
          </>}
          <div className="tour-ring" style={{ top, left, width: holeW, height: holeH }} aria-hidden="true" />
        </>
      ) : (
        <div className="tour-shade tour-shade-full" />
      )}

      {float?.arrow && (
        <span
          className={`tour-arrow is-${float.place}`}
          style={
            float.place === "below"
              ? { left: float.left + (float.arrow.left ?? 16), top: float.top - 6 }
              : float.place === "above"
                ? { left: float.left + (float.arrow.left ?? 16), top: float.top + (cardHeight || 0) - 6 }
                : { left: float.left - 6, top: float.top + (float.arrow.top ?? 16) }
          }
          aria-hidden="true"
        />
      )}

      <div
        ref={cardRef}
        className={cardClass}
        style={float ? { top: float.top, left: float.left, width: float.width } : undefined}
        tabIndex={-1}
      >
        <div className="tour-top">
          <span className="tour-kicker">Гид по платформе</span>
          <button type="button" className="tour-close" aria-label="Закрыть гайд" onClick={close}>×</button>
        </div>

        <div className="tour-dots" role="tablist" aria-label="Шаги гида">
          {steps.map((item, position) => (
            <button
              key={item.title}
              type="button"
              role="tab"
              className={position === index ? "is-current" : position < index ? "is-done" : undefined}
              aria-selected={position === index}
              aria-label={`Шаг ${position + 1}: ${item.title}`}
              title={item.title}
              onClick={() => setIndex(position)}
            />
          ))}
        </div>

        <div className="tour-body" aria-live="polite">
          <span className="tour-icon" aria-hidden="true"><Icon name={step.icon} /></span>
          <div className="tour-text">
            <h2>{step.title}</h2>
            <p>{step.text}</p>
            {step.hint && onFallback && <p className="tour-hint">{step.hint}</p>}
          </div>
        </div>

        <footer className="tour-foot">
          <div className="tour-progress" role="progressbar" aria-valuemin={1} aria-valuemax={steps.length} aria-valuenow={index + 1} aria-label="Пройдено шагов">
            <i style={{ width: `${((index + 1) / steps.length) * 100}%` }} />
          </div>
          <div className="tour-meta">
            <button type="button" className="tour-skip" onClick={close}>Пропустить</button>
            <span className="tour-count">Шаг {index + 1} из {steps.length}</span>
          </div>
          <div className="tour-actions">
            <Button type="button" className="ghost" disabled={index === 0} onClick={prev}>Назад</Button>
            <Button type="button" onClick={next}>{isLast ? "Готово" : "Далее"}</Button>
          </div>
        </footer>
      </div>
    </div>
  );
}
