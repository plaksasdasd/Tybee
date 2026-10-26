(() => {
  'use strict';

  const STATUS_LABELS = {
    accepted: 'Принят',
    bought: 'Выкуплен',
    shipping_to_ru: 'В пути в РФ',
    customs: 'На таможне',
    shipping_in_ru: 'Доставка по РФ',
    awaiting_pickup: 'Ожидает получения',
    received: 'Получен',
  };

  const FULFILLMENT_STATUSES = [
    'accepted', 'bought', 'shipping_to_ru', 'customs',
    'shipping_in_ru', 'awaiting_pickup', 'received',
  ];

  const PAYMENT_STATUSES = ['awaiting_payment', 'payment_review'];

  const SOURCE_CLASSES = {
    telegram: 'source-telegram',
    avito: 'source-avito',
    whatsapp: 'source-whatsapp',
    instagram: 'source-instagram',
  };

  const UI = {
    loginScreen: document.getElementById('login-screen'),
    loginWidget: document.getElementById('login-widget'),
    loginError: document.getElementById('login-error'),
    appScreen: document.getElementById('app-screen'),
    userName: document.getElementById('user-name'),
    operatorsBtn: document.getElementById('operators-btn'),
    logoutBtn: document.getElementById('logout-btn'),
    clientSearch: document.getElementById('client-search'),
    clientList: document.getElementById('client-list'),
    loadMoreClients: document.getElementById('load-more-clients'),
    clientListError: document.getElementById('client-list-error'),
    clientPlaceholder: document.getElementById('client-placeholder'),
    clientDetail: document.getElementById('client-detail'),
    detailName: document.getElementById('detail-name'),
    detailUsername: document.getElementById('detail-username'),
    detailSource: document.getElementById('detail-source'),
    detailStatus: document.getElementById('detail-status'),
    banBtn: document.getElementById('ban-btn'),
    messageHistory: document.getElementById('message-history'),
    composerForm: document.getElementById('composer-form'),
    composerInput: document.getElementById('composer-input'),
    sendBtn: document.getElementById('send-btn'),
    composerError: document.getElementById('composer-error'),
    createInvoiceBtn: document.getElementById('create-invoice-btn'),
    ordersList: document.getElementById('orders-list'),
    ordersError: document.getElementById('orders-error'),
    invoiceModal: document.getElementById('invoice-modal'),
    invoiceForm: document.getElementById('invoice-form'),
    invTitle: document.getElementById('inv-title'),
    invDescription: document.getElementById('inv-description'),
    invPrice: document.getElementById('inv-price'),
    invoiceError: document.getElementById('invoice-error'),
    operatorsModal: document.getElementById('operators-modal'),
    operatorForm: document.getElementById('operator-form'),
    opId: document.getElementById('op-id'),
    opName: document.getElementById('op-name'),
    operatorsList: document.getElementById('operators-list'),
    operatorsError: document.getElementById('operators-error'),
    toast: document.getElementById('toast'),
  };

  const state = {
    csrf: null,
    user: null,
    botUsername: null,
    clients: [],
    clientsTotal: 0,
    query: '',
    clientsOffset: 0,
    selectedClientId: null,
    detail: null,
    drafts: {},
    operators: [],
    clientsTimer: null,
    detailTimer: null,
  };

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null) continue;
      if (key === 'class') {
        node.className = value;
      } else if (key === 'dataset') {
        Object.assign(node.dataset, value);
      } else if (key.startsWith('on') && typeof value === 'function') {
        node.addEventListener(key.slice(2), value);
      } else {
        node.setAttribute(key, value);
      }
    }
    for (const child of children) {
      if (child == null) continue;
      if (typeof child === 'string' || typeof child === 'number') {
        node.appendChild(document.createTextNode(String(child)));
      } else {
        node.appendChild(child);
      }
    }
    return node;
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function formatDate(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toLocaleString('ru-RU', {
      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
    });
  }

  function formatMoney(n) {
    const num = Number(n);
    if (Number.isNaN(num)) return n;
    return num.toLocaleString('ru-RU') + ' ₽';
  }

  function showErrorInto(element, message) {
    if (element) {
      element.textContent = message || '';
      element.classList.toggle('hidden', !message);
    }
  }

  function toast(message) {
    UI.toast.textContent = message;
    UI.toast.classList.remove('hidden');
    clearTimeout(UI.toast._t);
    UI.toast._t = setTimeout(() => {
      UI.toast.classList.add('hidden');
      UI.toast.textContent = '';
    }, 4000);
  }

  function setLoading(button, loading) {
    if (!button) return;
    button.disabled = loading;
    const original = button.dataset.label || button.textContent;
    if (loading) {
      button.dataset.label = original;
      button.textContent = '…';
    } else {
      button.textContent = original;
      button.dataset.label = '';
    }
  }

  async function api(path, options = {}) {
    const opts = { credentials: 'same-origin', ...options };
    opts.headers = { ...opts.headers };
    if (opts.body && typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
      opts.headers['Content-Type'] = 'application/json';
    }
    if (state.csrf) {
      opts.headers['X-CSRF-Token'] = state.csrf;
    }

    const res = await fetch(path, opts);

    if (res.status === 401) {
      showLogin();
      throw new Error('Требуется авторизация');
    }

    if (!res.ok) {
      let message = res.statusText || `Ошибка ${res.status}`;
      try {
        const data = await res.json();
        if (data.error || data.message || data.detail) {
          message = data.error || data.message || data.detail;
        }
      } catch {}
      throw new Error(message);
    }

    if (res.status === 204 || res.headers.get('content-length') === '0') {
      return null;
    }
    return res.json();
  }

  function clearTimers() {
    if (state.clientsTimer) {
      clearInterval(state.clientsTimer);
      state.clientsTimer = null;
    }
    if (state.detailTimer) {
      clearInterval(state.detailTimer);
      state.detailTimer = null;
    }
  }

  function resetState() {
    state.csrf = null;
    state.user = null;
    state.clients = [];
    state.clientsTotal = 0;
    state.query = '';
    state.clientsOffset = 0;
    state.selectedClientId = null;
    state.detail = null;
    state.drafts = {};
    state.operators = [];
    clearTimers();
  }

  async function showLogin() {
    resetState();
    UI.appScreen.classList.add('hidden');
    UI.loginScreen.classList.remove('hidden');
    UI.loginWidget.textContent = '';
    UI.loginError.textContent = '';
    UI.loginError.classList.add('hidden');

    if (!state.botUsername) {
      try {
        const cfg = await api('/api/public-config', { method: 'GET' });
        state.botUsername = cfg.bot_username;
      } catch (e) {
        showErrorInto(UI.loginError, e.message);
        return;
      }
    }
    loadTelegramWidget(state.botUsername);
  }

  function loadTelegramWidget(botUsername) {
    UI.loginWidget.textContent = '';
    if (!botUsername) return;
    const script = el('script', {
      async: '',
      src: 'https://telegram.org/js/telegram-widget.js?22',
      'data-telegram-login': botUsername,
      'data-size': 'large',
      'data-auth-url': '/auth/telegram',
      'data-request-access': 'write',
    });
    UI.loginWidget.appendChild(script);
  }

  function showApp() {
    UI.loginScreen.classList.add('hidden');
    UI.appScreen.classList.remove('hidden');
    UI.userName.textContent = state.user?.name || 'Оператор';
    UI.operatorsBtn.classList.toggle('hidden', !state.user?.is_admin);
    UI.clientSearch.value = state.query;
    loadClients(true);
    startClientsPoll();
  }

  function startClientsPoll() {
    if (state.clientsTimer) return;
    state.clientsTimer = setInterval(() => refreshClientList(), 10000);
  }

  function startDetailPoll() {
    if (state.detailTimer) return;
    state.detailTimer = setInterval(() => refreshSelectedDetail(), 4000);
  }

  async function loadSession() {
    try {
      const data = await api('/api/session', { method: 'GET' });
      state.csrf = data.csrf;
      state.user = data.user;
      state.botUsername = data.bot_username;
      showApp();
    } catch (e) {
      if (e.message === 'Требуется авторизация') return;
      showLogin();
    }
  }

  async function logout() {
    try {
      await api('/api/logout', { method: 'POST', body: {} });
    } catch {}
    showLogin();
  }

  async function loadClients(reset = false) {
    if (reset) {
      state.clientsOffset = 0;
      state.clients = [];
    }
    showErrorInto(UI.clientListError, '');
    const url = `/api/clients?q=${encodeURIComponent(state.query)}&offset=${state.clientsOffset}`;
    try {
      const data = await api(url, { method: 'GET' });
      const fetched = Array.isArray(data.clients) ? data.clients : [];
      state.clients = reset ? fetched : state.clients.concat(fetched);
      state.clientsTotal = data.total || 0;
      renderClientList();
    } catch (e) {
      showErrorInto(UI.clientListError, e.message);
    }
  }

  async function refreshClientList() {
    if (!state.user) return;
    showErrorInto(UI.clientListError, '');
    const url = `/api/clients?q=${encodeURIComponent(state.query)}&offset=0`;
    try {
      const data = await api(url, { method: 'GET' });
      const fetched = Array.isArray(data.clients) ? data.clients : [];
      const byId = new Map(state.clients.map(c => [c.id, c]));
      for (const c of fetched) {
        byId.set(c.id, c);
      }
      state.clients = Array.from(byId.values());
      state.clientsTotal = data.total || 0;
      renderClientList();
    } catch (e) {
      showErrorInto(UI.clientListError, e.message);
    }
  }

  function renderClientList() {
    UI.clientList.textContent = '';

    if (state.clients.length === 0) {
      UI.clientList.appendChild(el('div', { class: 'empty-state' }, 'Нет клиентов'));
      UI.loadMoreClients.classList.add('hidden');
      return;
    }

    for (const client of state.clients) {
      const isSelected = client.id === state.selectedClientId;
      const item = el('article', {
        class: `client-item${isSelected ? ' active' : ''}`,
        role: 'listitem',
        tabindex: '0',
        onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') selectClient(client.id); },
        onclick: () => selectClient(client.id),
      });

      const meta = el('div', { class: 'client-meta' });
      const nameLine = el('div', { class: 'client-name-line' });
      nameLine.appendChild(el('span', { class: 'client-name' }, client.full_name || `ID ${client.id}`));
      if (client.username) {
        nameLine.appendChild(el('span', { class: 'client-username' }, `@${client.username}`));
      }
      const badgeClass = SOURCE_CLASSES[client.source] || 'source-unknown';
      nameLine.appendChild(el('span', { class: `badge ${badgeClass}` }, client.source || '—'));
      meta.appendChild(nameLine);

      if (client.last_message) {
        meta.appendChild(el('p', { class: 'client-last-message' }, client.last_message));
      }
      if (client.last_activity) {
        meta.appendChild(el('time', { class: 'client-time' }, formatDate(client.last_activity)));
      }

      if (client.is_banned) {
        item.appendChild(el('span', { class: 'banned-marker', title: 'Забанен' }, 'Забанен'));
      }
      item.appendChild(meta);
      UI.clientList.appendChild(item);
    }

    UI.loadMoreClients.classList.toggle('hidden', state.clients.length >= state.clientsTotal);
  }

  async function selectClient(id) {
    state.selectedClientId = id;
    state.detail = null;
    renderClientList();
    UI.clientPlaceholder.classList.add('hidden');
    UI.clientDetail.classList.add('hidden');
    clearInterval(state.detailTimer);
    state.detailTimer = null;
    await loadDetail(0);
    startDetailPoll();
  }

  async function loadDetail(after = 0) {
    if (!state.selectedClientId) return;
    showErrorInto(UI.ordersError, '');
    showErrorInto(UI.composerError, '');

    try {
      const data = await api(`/api/clients/${state.selectedClientId}?after=${after}`, { method: 'GET' });

      if (after === 0) {
        state.detail = data;
        UI.clientDetail.classList.remove('hidden');
        UI.composerInput.value = state.drafts[state.selectedClientId] || '';
      } else {
        if (data.client) state.detail.client = data.client;
        if (Array.isArray(data.orders)) state.detail.orders = data.orders;
        if (Array.isArray(data.messages)) {
          const existing = new Set((state.detail.messages || []).map(m => m.id));
          for (const m of data.messages) {
            if (!existing.has(m.id)) state.detail.messages.push(m);
          }
        }
      }

      renderDetailHeader();
      renderMessages();
      renderOrders();
    } catch (e) {
      showErrorInto(UI.ordersError, e.message);
    }
  }

  function refreshSelectedDetail() {
    if (!state.selectedClientId || !state.detail) return;
    const after = (state.detail.messages || []).reduce((max, m) => Math.max(max, Number(m.id) || 0), 0);
    loadDetail(after);
  }

  function renderDetailHeader() {
    const client = state.detail?.client;
    if (!client) return;

    UI.detailName.textContent = client.full_name || `Клиент ${client.id}`;
    UI.detailUsername.textContent = client.username ? `@${client.username}` : '';
    UI.detailUsername.classList.toggle('hidden', !client.username);

    UI.detailSource.textContent = client.source || '—';
    UI.detailSource.className = `badge ${SOURCE_CLASSES[client.source] || 'source-unknown'}`;

    if (client.is_banned) {
      UI.detailStatus.textContent = 'Забанен';
      UI.detailStatus.className = 'status banned';
      UI.banBtn.textContent = 'Разбанить';
      UI.banBtn.className = 'btn btn-success';
    } else {
      UI.detailStatus.textContent = 'Активен';
      UI.detailStatus.className = 'status active';
      UI.banBtn.textContent = 'Забанить';
      UI.banBtn.className = 'btn btn-danger';
    }
  }

  function renderMessages() {
    const messages = Array.isArray(state.detail?.messages) ? state.detail.messages : [];
    const sorted = messages.slice().sort((a, b) => (Number(a.id) || 0) - (Number(b.id) || 0));

    const history = UI.messageHistory;
    const isFirstRender = history.children.length === 0;
    const wasNearBottom = history.scrollHeight - history.scrollTop - history.clientHeight < 80;

    history.textContent = '';

    if (sorted.length === 0) {
      history.appendChild(el('div', { class: 'empty-state' }, 'Нет сообщений'));
      return;
    }

    for (const m of sorted) {
      const direction = m.direction || 'inbound';
      const wrapper = el('div', { class: `message ${direction}` });
      const bubble = el('div', { class: 'message-bubble' });

      if (direction === 'system') {
        bubble.appendChild(el('span', { class: 'message-body system-body' }, m.body || ''));
      } else {
        const header = el('div', { class: 'message-header' });
        if (direction === 'outbound' && m.operator_name) {
          header.appendChild(el('span', { class: 'operator-name' }, m.operator_name));
        }
        if (m.channel) {
          header.appendChild(el('span', { class: 'message-channel' }, m.channel));
        }
        if (header.childNodes.length) bubble.appendChild(header);
        bubble.appendChild(el('p', { class: 'message-body' }, m.body || ''));
      }

      bubble.appendChild(el('time', { class: 'message-time' }, formatDate(m.created_at)));
      wrapper.appendChild(bubble);
      history.appendChild(wrapper);
    }

    if (isFirstRender || wasNearBottom) {
      history.scrollTop = history.scrollHeight;
    }
  }

  function renderOrders() {
    UI.ordersList.textContent = '';
    const orders = Array.isArray(state.detail?.orders) ? state.detail.orders : [];
    const sorted = orders.slice().sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));

    if (sorted.length === 0) {
      UI.ordersList.appendChild(el('div', { class: 'empty-state' }, 'Нет заказов'));
      return;
    }

    for (const order of sorted) {
      const status = order.status || '';
      const showFulfillment = status === 'approved' || FULFILLMENT_STATUSES.includes(status);
      const showPayment = PAYMENT_STATUSES.includes(status);

      const card = el('article', { class: 'order-card' });
      const head = el('div', { class: 'order-header' });
      head.appendChild(el('h4', { class: 'order-title' }, order.title || `Заказ #${order.id}`));
      head.appendChild(el('span', { class: `badge status-${status || 'unknown'}` }, STATUS_LABELS[status] || status || '—'));
      card.appendChild(head);

      if (order.description) {
        card.appendChild(el('p', { class: 'order-description' }, order.description));
      }

      const meta = el('div', { class: 'order-meta' });
      meta.appendChild(el('span', { class: 'order-price' }, formatMoney(order.price)));
      meta.appendChild(el('time', {}, formatDate(order.created_at)));
      card.appendChild(meta);

      const controls = el('div', { class: 'order-controls' });

      if (showFulfillment) {
        const statusLabel = el('label', { class: 'field-inline' });
        statusLabel.appendChild(el('span', { class: 'field-label' }, 'Статус'));
        const statusSelect = el('select', { class: 'status-select' });
        for (const st of FULFILLMENT_STATUSES) {
          statusSelect.appendChild(el('option', { value: st }, STATUS_LABELS[st]));
        }
        statusSelect.value = status === 'approved' ? 'accepted' : status;
        statusSelect.addEventListener('change', async () => {
          if (statusSelect.value === order.status) return;
          statusSelect.disabled = true;
          try {
            await api(`/api/orders/${order.id}/status`, { method: 'POST', body: { status: statusSelect.value } });
            await loadDetail(0);
            toast('Статус обновлен');
          } catch (e) {
            showErrorInto(UI.ordersError, e.message);
            statusSelect.value = order.status === 'approved' ? 'accepted' : order.status;
          } finally {
            statusSelect.disabled = false;
          }
        });
        statusLabel.appendChild(statusSelect);
        controls.appendChild(statusLabel);
      }

      if (showPayment) {
        const paymentGroup = el('div', { class: 'btn-group' });
        const approveBtn = el('button', { class: 'btn btn-success', type: 'button' }, 'Подтвердить оплату');
        const rejectBtn = el('button', { class: 'btn btn-danger', type: 'button' }, 'Отклонить оплату');
        approveBtn.addEventListener('click', async () => {
          setLoading(approveBtn, true);
          try {
            await api(`/api/orders/${order.id}/payment`, { method: 'POST', body: { approved: true } });
            await loadDetail(0);
            toast('Оплата подтверждена');
          } catch (e) {
            showErrorInto(UI.ordersError, e.message);
          } finally {
            setLoading(approveBtn, false);
          }
        });
        rejectBtn.addEventListener('click', async () => {
          setLoading(rejectBtn, true);
          try {
            await api(`/api/orders/${order.id}/payment`, { method: 'POST', body: { approved: false } });
            await loadDetail(0);
            toast('Оплата отклонена');
          } catch (e) {
            showErrorInto(UI.ordersError, e.message);
          } finally {
            setLoading(rejectBtn, false);
          }
        });
        paymentGroup.appendChild(approveBtn);
        paymentGroup.appendChild(rejectBtn);
        controls.appendChild(paymentGroup);
      }

      if (controls.childNodes.length) {
        card.appendChild(controls);
      }
      UI.ordersList.appendChild(card);
    }
  }

  async function sendMessage(text) {
    if (!state.selectedClientId) return;
    showErrorInto(UI.composerError, '');
    try {
      await api(`/api/clients/${state.selectedClientId}/messages`, { method: 'POST', body: { text } });
      state.drafts[state.selectedClientId] = '';
      UI.composerInput.value = '';
      await loadDetail(0);
    } catch (e) {
      showErrorInto(UI.composerError, e.message);
    }
  }

  async function toggleBan() {
    if (!state.selectedClientId || !state.detail?.client) return;
    const client = state.detail.client;
    const action = client.is_banned ? 'разбанить' : 'забанить';
    if (!window.confirm(`Вы уверены, что хотите ${action} этого клиента?`)) return;

    setLoading(UI.banBtn, true);
    try {
      await api(`/api/clients/${state.selectedClientId}/ban`, { method: 'POST', body: { banned: !client.is_banned } });
      client.is_banned = !client.is_banned;
      renderDetailHeader();
      await refreshClientList();
      toast(client.is_banned ? 'Клиент забанен' : 'Клиент разбанен');
    } catch (e) {
      showErrorInto(UI.ordersError, e.message);
    } finally {
      setLoading(UI.banBtn, false);
    }
  }

  function openInvoiceModal() {
    if (!state.selectedClientId) return;
    UI.invoiceForm.reset();
    showErrorInto(UI.invoiceError, '');
    UI.invoiceModal.classList.remove('hidden');
    UI.invTitle.focus();
  }

  function closeInvoiceModal() {
    UI.invoiceModal.classList.add('hidden');
  }

  async function submitInvoice(e) {
    e.preventDefault();
    if (!state.selectedClientId) return;
    showErrorInto(UI.invoiceError, '');

    const title = UI.invTitle.value.trim();
    const description = UI.invDescription.value.trim();
    const price = parseInt(UI.invPrice.value, 10);

    if (!title || Number.isNaN(price) || price < 1) {
      showErrorInto(UI.invoiceError, 'Укажите название и положительную целую сумму');
      return;
    }

    const submitBtn = UI.invoiceForm.querySelector('button[type="submit"]');
    setLoading(submitBtn, true);
    try {
      await api(`/api/clients/${state.selectedClientId}/orders`, {
        method: 'POST',
        body: { title, description, price },
      });
      closeInvoiceModal();
      UI.invoiceForm.reset();
      await loadDetail(0);
      toast('Счет создан');
    } catch (err) {
      showErrorInto(UI.invoiceError, err.message);
    } finally {
      setLoading(submitBtn, false);
    }
  }

  function openOperatorsModal() {
    UI.operatorsModal.classList.remove('hidden');
    loadOperators();
  }

  function closeOperatorsModal() {
    UI.operatorsModal.classList.add('hidden');
  }

  async function loadOperators() {
    showErrorInto(UI.operatorsError, '');
    try {
      const data = await api('/api/operators', { method: 'GET' });
      state.operators = Array.isArray(data.operators) ? data.operators : [];
      renderOperators();
    } catch (e) {
      showErrorInto(UI.operatorsError, e.message);
    }
  }

  function renderOperators() {
    UI.operatorsList.textContent = '';
    if (state.operators.length === 0) {
      UI.operatorsList.appendChild(el('div', { class: 'empty-state' }, 'Операторов пока нет'));
      return;
    }

    for (const op of state.operators) {
      const row = el('div', { class: 'operator-row' });
      const nameInput = el('input', { type: 'text', value: op.display_name || '', maxlength: 100 });
      const activeCheck = el('input', { type: 'checkbox' });
      activeCheck.checked = !!op.is_active;

      const saveBtn = el('button', { class: 'btn btn-primary', type: 'button' }, 'Сохранить');
      saveBtn.addEventListener('click', async () => {
        setLoading(saveBtn, true);
        try {
          await api(`/api/operators/${op.telegram_id}`, {
            method: 'PATCH',
            body: { display_name: nameInput.value.trim(), is_active: activeCheck.checked },
          });
          await loadOperators();
          toast('Оператор обновлен');
        } catch (e) {
          showErrorInto(UI.operatorsError, e.message);
        } finally {
          setLoading(saveBtn, false);
        }
      });

      row.appendChild(el('span', { class: 'operator-id' }, `ID ${op.telegram_id}`));
      row.appendChild(nameInput);
      const activeLabel = el('label', { class: 'checkbox-label' });
      activeLabel.appendChild(activeCheck);
      activeLabel.appendChild(document.createTextNode('Активен'));
      row.appendChild(activeLabel);
      row.appendChild(saveBtn);
      UI.operatorsList.appendChild(row);
    }
  }

  async function addOperator(e) {
    e.preventDefault();
    showErrorInto(UI.operatorsError, '');
    const telegramId = parseInt(UI.opId.value, 10);
    const displayName = UI.opName.value.trim();
    if (Number.isNaN(telegramId) || telegramId < 1 || !displayName) {
      showErrorInto(UI.operatorsError, 'Укажите числовой Telegram ID и имя');
      return;
    }
    const submitBtn = UI.operatorForm.querySelector('button[type="submit"]');
    setLoading(submitBtn, true);
    try {
      await api('/api/operators', { method: 'POST', body: { telegram_id: telegramId, display_name: displayName } });
      UI.operatorForm.reset();
      await loadOperators();
      toast('Оператор добавлен');
    } catch (e) {
      showErrorInto(UI.operatorsError, e.message);
    } finally {
      setLoading(submitBtn, false);
    }
  }

  function bindEvents() {
    UI.logoutBtn.addEventListener('click', logout);
    UI.operatorsBtn.addEventListener('click', openOperatorsModal);

    UI.clientSearch.addEventListener('input', debounce((e) => {
      state.query = e.target.value;
      loadClients(true);
    }, 300));

    UI.loadMoreClients.addEventListener('click', () => {
      state.clientsOffset = state.clients.length;
      loadClients(false);
    });

    UI.composerForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = UI.composerInput.value.trim();
      if (!text) return;
      setLoading(UI.sendBtn, true);
      await sendMessage(text);
      setLoading(UI.sendBtn, false);
    });

    UI.composerInput.addEventListener('input', () => {
      if (state.selectedClientId) {
        state.drafts[state.selectedClientId] = UI.composerInput.value;
      }
    });

    UI.banBtn.addEventListener('click', toggleBan);
    UI.createInvoiceBtn.addEventListener('click', openInvoiceModal);
    UI.invoiceForm.addEventListener('submit', submitInvoice);

    UI.operatorForm.addEventListener('submit', addOperator);

    for (const modal of [UI.invoiceModal, UI.operatorsModal]) {
      modal.addEventListener('click', (e) => {
        if (e.target.dataset.close === modal.id) {
          modal.classList.add('hidden');
        }
      });
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeInvoiceModal();
        closeOperatorsModal();
      }
    });
  }

  bindEvents();
  loadSession();
})();
