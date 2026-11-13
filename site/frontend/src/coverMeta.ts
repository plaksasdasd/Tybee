// Human names for cover config keys, and how each screen looks in the bot
// (which banner sits above the text and which buttons go under it).
// Keys missing here still work: the editor falls back to the raw key.

export type TextMeta = { label: string; hint?: string; banner?: string; kb?: string };

export const TEXT_META: Record<string, TextMeta> = {
  WELCOME: { label: "Приветствие", hint: "Главный экран после /start", banner: "welcome", kb: "main_menu" },
  PROFILE: { label: "Профиль", hint: "Личный кабинет клиента", banner: "profile", kb: "profile_menu" },
  OFFER: { label: "Публичная оферта", banner: "profile", kb: "back" },
  OFFER_URL: { label: "Ссылка на полную оферту", hint: "Куда ведёт кнопка «Оферта»" },
  NO_ORDERS: { label: "Мои заказы — пусто", banner: "profile", kb: "back" },
  ORDERS_HEADER: { label: "Мои заказы — заголовок", hint: "Над списком заказов", banner: "profile", kb: "back" },
  PROFILE_DEFAULT_NAME: { label: "Имя по умолчанию", hint: "Если у клиента в Telegram нет имени" },
  CMD_START_DESCRIPTION: { label: "Описание команды /start", hint: "Показывается в меню команд Telegram" },
  ORDER_CHOICE: { label: "Выбор способа заказа", banner: "order_choice", kb: "order_choice" },
  ARTICLE_ALREADY: { label: "Есть ли артикул?", banner: "article", kb: "article_choice" },
  ARTICLE_PROMPT: { label: "Просьба прислать артикул", banner: "article", kb: "back" },
  ARTICLE_INVALID: { label: "Артикул введён неверно", banner: "article", kb: "back" },
  FOUND: { label: "Товар найден", hint: "Артикул есть в каталоге", banner: "approved", kb: "pay_offer" },
  REJECTED_CATALOG: { label: "Товара нет в каталоге", banner: "rejected", kb: "catalog_rejected" },
  ORDER_CREATED: { label: "Выставлен счёт", hint: "Когда сотрудник выставил счёт из чата", kb: "payment" },
  MYSTERY_LIST: { label: "Список Mystery Box", banner: "mystery", kb: "mystery" },
  MYSTERY_EMPTY: { label: "Mystery Box — нет в наличии", banner: "mystery" },
  MYSTERY_OFFER: { label: "Mystery Box выбран", banner: "approved", kb: "pay_offer" },
  PERSONAL_PROMPT: { label: "Персональная заявка", hint: "Просьба описать товар", banner: "order_choice", kb: "back" },
  PERSONAL_DONE: { label: "Персональная заявка принята", banner: "approved", kb: "in_dialog" },
  PAYMENT: { label: "Оплата заказа", banner: "payment", kb: "payment" },
  PAYMENT_UNAVAILABLE: { label: "Оплата недоступна", banner: "payment", kb: "menu" },
  APPROVED: { label: "Оплата подтверждена", kb: "open_dialog" },
  REJECTED_PAYMENT: { label: "Оплата не найдена" },
  SUPPORT: { label: "Поддержка", banner: "profile", kb: "in_dialog" },
  DIALOG_OPEN: { label: "Диалог с командой открыт", banner: "approved", kb: "in_dialog" },
  ORDER_LINE: { label: "Строка заказа", hint: "Одна строка в «Мои заказы»" },
  ORDER_PRICE_PART: { label: "Цена в строке заказа", hint: "Подставляется в {price_part}, если цена известна" },
  ORDER_DEFAULT_TITLE: { label: "Название заказа по умолчанию", hint: "Если у заказа нет названия" },
  ORDER_STATUS_PUSH: { label: "Уведомление о смене статуса" },
  ORDER_PING: { label: "Напоминание о заказе", hint: "Кнопка «Напомнить» в чате поддержки" },
  BANNED_SCREEN: { label: "Клиент заблокирован", kb: "pay_fine" },
  UNBANNED: { label: "Блокировка снята" },
  UNBAN_PAYMENT_MESSAGE: { label: "Оплата штрафа", kb: "pay_fine" },
  UNBAN_PAYMENT_DESCRIPTION: { label: "Назначение платежа за штраф", hint: "Видно в Robokassa" },
  ANSWER_DIALOG_CLOSED: { label: "Всплывашка: диалог завершён" },
  ANSWER_ORDER_NOT_FOUND: { label: "Всплывашка: заказ не найден" },
  ANSWER_BOX_UNAVAILABLE: { label: "Всплывашка: бокс недоступен" },
  ANSWER_BAN_LIFTED: { label: "Всплывашка: блокировка уже снята" },
  ANSWER_PAYMENT_UNAVAILABLE: { label: "Всплывашка: оплата недоступна" },
};

export const TEXT_GROUPS: [string, string[]][] = [
  ["Главное меню и профиль", ["WELCOME", "PROFILE", "OFFER", "OFFER_URL", "NO_ORDERS", "ORDERS_HEADER", "PROFILE_DEFAULT_NAME", "CMD_START_DESCRIPTION"]],
  ["Заказ по артикулу", ["ORDER_CHOICE", "ARTICLE_ALREADY", "ARTICLE_PROMPT", "ARTICLE_INVALID", "FOUND", "REJECTED_CATALOG"]],
  ["Mystery Box", ["MYSTERY_LIST", "MYSTERY_EMPTY", "MYSTERY_OFFER"]],
  ["Персональная заявка", ["PERSONAL_PROMPT", "PERSONAL_DONE"]],
  ["Оплата", ["ORDER_CREATED", "PAYMENT", "PAYMENT_UNAVAILABLE", "APPROVED", "REJECTED_PAYMENT"]],
  ["Поддержка", ["SUPPORT", "DIALOG_OPEN"]],
  ["Список заказов и уведомления", ["ORDER_LINE", "ORDER_PRICE_PART", "ORDER_DEFAULT_TITLE", "ORDER_STATUS_PUSH", "ORDER_PING"]],
  ["Блокировка", ["BANNED_SCREEN", "UNBANNED", "UNBAN_PAYMENT_MESSAGE", "UNBAN_PAYMENT_DESCRIPTION"]],
  ["Всплывающие ответы", ["ANSWER_DIALOG_CLOSED", "ANSWER_ORDER_NOT_FOUND", "ANSWER_BOX_UNAVAILABLE", "ANSWER_BAN_LIFTED", "ANSWER_PAYMENT_UNAVAILABLE"]],
];

export const BUTTON_LABELS: Record<string, string> = {
  BTN_NEW_ORDER: "Оформить заказ",
  BTN_PROFILE: "Профиль",
  BTN_SUPPORT: "Поддержка",
  BTN_BACK: "Назад",
  BTN_MENU: "В главное меню",
  BTN_HOME: "На главную (после оплаты)",
  BTN_OPEN_DIALOG: "Открыть диалог",
  BTN_CLOSE_DIALOG: "Завершить диалог",
  BTN_OFFER: "Оферта",
  BTN_MY_ORDERS: "Мои заказы",
  BTN_ORDER_ARTICLE: "Заказ по артикулу",
  BTN_ORDER_MYSTERY: "Mystery Box",
  BTN_ORDER_PERSONAL: "Персональная заявка",
  BTN_PERSONAL_APPLICATION: "Оформить персональную заявку",
  BTN_HAS_ARTICLE: "Артикул есть",
  BTN_NO_ARTICLE: "Артикула нет",
  BTN_PAY_ONLINE: "Оплатить онлайн",
  BTN_PAY_FINE: "Оплатить штраф",
  BTN_TO_PAYMENT: "Перейти к оплате",
  BTN_MYSTERY_ITEM: "Кнопка бокса в списке",
};

export const STATUS_LABELS: Record<string, string> = {
  new: "Новый",
  awaiting_payment: "Ждёт оплаты",
  payment_review: "Проверка оплаты",
  approved: "Оплата подтверждена",
  rejected: "Отклонён",
  in_dialog: "В работе у команды",
  accepted: "Принят",
  bought: "Выкуплен",
  shipping_to_ru: "Едет в Россию",
  customs: "На таможне",
  shipping_in_ru: "Едет по России",
  awaiting_pickup: "Ждёт получения",
  received: "Получен",
  refund_pending: "Оформляется возврат",
  refunded: "Деньги возвращены",
  cancelled: "Отменён",
};

export const BANNER_LABELS: Record<string, string> = {
  welcome: "Приветствие",
  profile: "Профиль и поддержка",
  order_choice: "Выбор способа заказа",
  article: "Артикул",
  mystery: "Mystery Box",
  payment: "Оплата",
  almost: "Почти готово",
  approved: "Заявка одобрена",
  rejected: "Заявка отклонена",
};

// Keyboards from app/keyboards/inline.py: rows of button keys.
export const KEYBOARDS: Record<string, string[][]> = {
  main_menu: [["BTN_NEW_ORDER"], ["BTN_PROFILE"], ["BTN_SUPPORT"]],
  profile_menu: [["BTN_MY_ORDERS"], ["BTN_OFFER"], ["BTN_MENU"]],
  back: [["BTN_BACK"]],
  menu: [["BTN_MENU"]],
  order_choice: [["BTN_ORDER_PERSONAL"], ["BTN_ORDER_ARTICLE", "BTN_ORDER_MYSTERY"], ["BTN_BACK"]],
  article_choice: [["BTN_HAS_ARTICLE", "BTN_NO_ARTICLE"], ["BTN_BACK"]],
  mystery: [["BTN_MYSTERY_ITEM"], ["BTN_BACK"]],
  payment: [["BTN_PAY_ONLINE"], ["BTN_MENU"]],
  catalog_rejected: [["BTN_PERSONAL_APPLICATION"], ["BTN_MENU"]],
  open_dialog: [["BTN_OPEN_DIALOG"], ["BTN_HOME"]],
  in_dialog: [["BTN_CLOSE_DIALOG"]],
  pay_fine: [["BTN_PAY_FINE"]],
  pay_offer: [["BTN_TO_PAYMENT"], ["BTN_MENU"]],
};

/** Screens (text labels) where a button appears. */
export const buttonUsage = (btn: string) =>
  Object.entries(TEXT_META)
    .filter(([, m]) => m.kb && KEYBOARDS[m.kb].some((row) => row.includes(btn)))
    .map(([, m]) => m.label);

export const bannerUsage = (banner: string) =>
  Object.values(TEXT_META).filter((m) => m.banner === banner).map((m) => m.label);

// Example values for the preview.
export const SAMPLE: Record<string, string> = {
  name: "Анна",
  user_id: "123456789",
  orders_count: "3",
  price: "4 990",
  title: "Кроссовки Nike Air Max 90",
  code: "482915",
  description: "Размер 42, белые\n",
  order_id: "10427",
  fine: "500",
  amount: "500",
  price_part: " — 4 990 ₽",
};
