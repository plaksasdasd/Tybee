export class ApiError extends Error {
  constructor(public status: number, message: string, public errors: string[] = [], public reason: string | null = null) {
    super(message);
  }
}

/** The request never reached a working server: no network, DNS or a timeout. */
export class NetworkError extends Error {
  constructor(message: string) { super(message); this.name = "NetworkError"; }
}

const TIMEOUT_MS = 20_000;
// Uploads can be several megabytes on a phone connection: give them room.
const UPLOAD_TIMEOUT_MS = 120_000;

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "tybee-panel" };
  if (init.body && !(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), init.body instanceof FormData ? UPLOAD_TIMEOUT_MS : TIMEOUT_MS);
  try {
    let res: Response;
    try {
      res = await fetch(path, { credentials: "same-origin", ...init, signal: controller.signal, headers: { ...headers, ...(init.headers as any) } });
    } catch {
      // A dropped connection and a timeout look the same to the user; both are "no answer".
      throw new NetworkError(navigator.onLine === false ? "Нет подключения к интернету" : "Сервер не ответил. Проверьте соединение");
    }
    if (!res.ok) {
      let detail = res.statusText;
      let errors: string[] = [];
      try {
        const body = await res.json();
        detail = typeof body.detail === "string" ? body.detail : detail;
        errors = body.errors ?? [];
      } catch { /* not json */ }
      throw new ApiError(res.status, detail, errors, res.headers.get("X-Tybee-Reason"));
    }
    return res.status === 204 ? (undefined as T) : await res.json();
  } finally {
    clearTimeout(timeout);
  }
}

export const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export type Me = { telegram_id: number; display_name: string; username: string | null; photo_url: string | null; role: string;
  role_label: string; position: string | null; permissions: string[]; on_shift?: boolean;
};
export type StaffMember = Me & {
  is_active: boolean; on_shift: boolean; created_at: string | null; created_by: number | null;
  is_bootstrap: boolean; manageable: boolean; manage_hint: string | null; base_salary_kop?: number;
};
export type RoleInfo = { code: string; label: string; permissions: string[]; assignable: boolean };
export type AuditEntry = {
  id: number; actor_id: number | null; actor_name: string | null; action: string;
  target: string | null; target_label?: string | null; details: Record<string, unknown> | null; created_at: string | null;
};
export const fmtDate = (iso: string | null) => (iso ? new Date(iso).toLocaleString("ru-RU") : "—");
export type Cover = {
  slug: string; bot_username: string | null; enabled: boolean; running: boolean;
  last_error: string | null; clients: number; orders: number;
};
export type KeySpec = { placeholders: string[]; caption: boolean };
export type Spec = {
  texts: Record<string, KeySpec>; buttons: Record<string, KeySpec>; statuses: string[]; banners: string[];
  caption_limit: number; button_limit: number; banner_max_bytes: number; shared: string;
};
export type CoverConfig = {
  brand_name: string; unban_fine_rub: number | string; token_masked: string; last_error: string | null;
  texts: Record<string, string>; buttons: Record<string, string>; statuses: Record<string, string>;
};

export const fmtRub = (kop: number) => {
  const sign = kop < 0 ? "−" : "";
  const abs = Math.abs(kop);
  const rub = Math.floor(abs / 100).toLocaleString("ru-RU");
  const rest = abs % 100;
  return `${sign}${rub}${rest ? "," + String(rest).padStart(2, "0") : ""} ₽`;
};
export type FinOp = {
  id: number; staff_id: number; staff_name: string; kind: string; kind_label: string; amount_kop: number;
  period: string | null; comment: string | null; created_by_name: string | null; reversed: boolean; reversal_of: number | null;
  created_at: string | null;
};
export type Payout = {
  id: number; staff_id: number; staff_name: string; amount_kop: number; status: string; comment: string | null;
  requisites: string | null; decision_comment: string | null; decided_by_name: string | null;
  paid_reference: string | null; paid_at: string | null; created_at: string | null;
};
export const PAYOUT_STATUS: Record<string, { label: string; cls: string }> = {
  requested: { label: "Запрошено", cls: "wait" }, approved: { label: "Одобрено", cls: "ok" },
  paid: { label: "Выплачено", cls: "ok" }, rejected: { label: "Отклонено", cls: "bad" }, cancelled: { label: "Отменено", cls: "off" },
};

export const AUDIT_GROUPS: Record<string, string> = {
  auth: "Вход", staff: "Сотрудники", support: "Поддержка", finance: "Финансы", catalog: "Каталог",
  cover: "Боты-обложки", avito: "Авито", broadcast: "Рассылки",
};
export const AUDIT_ACTIONS: Record<string, string> = {
  "support.note": "Внутренняя заметка",
  "support.retry": "Повтор отправки сообщения",
  "support.template.create": "Создан шаблон ответа",
  "support.template.update": "Изменён шаблон ответа",
  "auth.login": "Вход в панель",
  "staff.create": "Добавлен сотрудник",
  "staff.update": "Изменён профиль сотрудника",
  "staff.delete": "Удалён сотрудник",
  "staff.activate": "Сотрудник включён",
  "staff.deactivate": "Сотрудник отключён",
  "staff.role.change": "Изменена роль сотрудника",
  "staff.invite.create": "Создано приглашение",
  "staff.invite.revoke": "Приглашение отозвано",
  "staff.invite.accept": "Приглашение принято",
  "support.reply": "Ответ клиенту",
  "support.initiate": "Начат диалог с клиентом",
  "support.claim": "Диалог взят в работу",
  "support.transfer": "Диалог передан другому",
  "support.close": "Диалог закрыт",
  "support.release": "Диалоги сотрудника освобождены",
  "support.invoice": "Выставлен счёт",
  "support.order.status": "Изменён статус заказа",
  "support.order.ping": "Клиенту напомнили о заказе",
  "support.ban": "Клиент заблокирован",
  "support.unban": "Клиент разблокирован",
  "support.shift": "Смена начата / завершена",
  "finance.op.create": "Финансовая операция",
  "finance.operation.create": "Финансовая операция",
  "finance.op.reverse": "Операция отменена",
  "finance.payout.request": "Запрошена выплата",
  "finance.payout.cancel": "Запрос выплаты отменён",
  "finance.payout.approve": "Выплата одобрена",
  "finance.payout.reject": "Выплата отклонена",
  "finance.payout.paid": "Выплата проведена",
  "finance.requisites.update": "Изменены реквизиты",
  "finance.export": "Выгрузка отчёта",
  "finance.payroll.accrue": "Начислена зарплата за месяц",
  "finance.payroll.cancel": "Начисление зарплаты отменено",
  "finance.payroll.settings": "Изменены ставки расчёта зарплаты",
  "finance.review.create": "Добавлен отзыв",
  "finance.review.delete": "Удалён отзыв",
  "staff.salary.change": "Изменён оклад сотрудника",
  "catalog.product.create": "Добавлен товар",
  "catalog.product.update": "Изменён товар",
  "catalog.product.enable": "Товар включён",
  "catalog.product.disable": "Товар скрыт",
  "catalog.product.delete": "Удалён товар",
  "catalog.product.photo": "Загружено фото товара",
  "catalog.import": "Импорт каталога из Excel",
  "catalog.box.create": "Создан Mystery Box",
  "catalog.box.update": "Изменён Mystery Box",
  "catalog.box.delete": "Удалён Mystery Box",
  "cover.create": "Создан бот-обложка",
  "cover.enable": "Бот-обложка включён",
  "cover.disable": "Бот-обложка выключен",
  "cover.toggle": "Бот-обложка включён / выключен",
  "cover.config.update": "Изменены тексты и настройки бота",
  "cover.texts.update": "Изменены тексты бота",
  "cover.token.update": "Заменён токен бота",
  "cover.banner.replace": "Заменён баннер бота",
  "avito.profile.create": "Подключён профиль Авито",
  "avito.profile.update": "Изменён профиль Авито",
  "avito.profile.enable": "Профиль Авито включён",
  "avito.profile.disable": "Профиль Авито выключен",
  "avito.profile.check": "Проверка ключей Авито",
  "avito.profile.subscribe": "Подписка на уведомления Авито",
  "broadcast.create": "Запущена рассылка",
  "broadcast.test": "Тестовая рассылка себе",
  "broadcast.cancel": "Рассылка остановлена",
};
export const PERM_LABELS: Record<string, string> = {
  "audit.view": "Просмотр аудита",
  "avito.manage": "Профили Авито",
  "broadcast.send": "Рассылки",
  "buyout.manage": "Выкуп",
  "finance.payouts.pay": "Подтверждение выплат",
  "catalog.manage": "Каталог",
  "covers.manage": "Боты-обложки",
  "finance.analytics": "Финансовая аналитика",
  "finance.bonuses": "Премии и штрафы",
  "finance.payouts.approve": "Одобрение выплат",
  "orders.invoice": "Выставление счетов",
  "orders.operations.view": "Просмотр операций по заказам",
  "refunds.manage": "Возвраты",
  "staff.manage": "Управление сотрудниками",
  "staff.view": "Просмотр сотрудников",
  "support.templates.manage": "Управление шаблонами ответов",
  "support.assign": "Распределение диалогов",
  "support.chat": "Чат поддержки",
  "support.view_all": "Все диалоги поддержки",
};
export const permLabel = (p: string) => PERM_LABELS[p] ?? p;

export const auditTarget = (a: AuditEntry) => a.target_label ?? a.target ?? "—";

const SHIFT_END_REASONS: Record<string, string> = {
  manual: "вручную", logout: "выход из панели", offline: "автоматически: панель закрыта",
  idle: "автоматически: нет активности", no_reply: "автоматически: клиент ждал ответа", deactivated: "сотрудник отключён",
};

export const auditLabel = (action: string, details?: Record<string, unknown> | null) => {
  if (action === "support.shift" && details && "on_shift" in details) {
    if (details.on_shift) return "Смена начата";
    const why = SHIFT_END_REASONS[String(details.reason ?? "")];
    return why ? `Смена завершена (${why})` : "Смена завершена";
  }
  return AUDIT_ACTIONS[action] ?? action;
};
