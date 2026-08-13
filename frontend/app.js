'use strict';

const TOKEN_KEY = 'halves_token';
const CATS = ['Rent', 'Groceries', 'Utilities', 'Household', 'Eating out', 'Transport', 'Other'];
const EPS = 0.005;
const PALETTE = ['avatar--sage', 'avatar--amber', 'avatar--blue'];
const NUMBER_WORDS = ['Zero', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];

function defaultSignupForm() {
  return { name: '', email: '', password: '', mode: 'create', householdName: '', householdId: '', households: [], error: null, loading: false };
}

const state = {
  token: localStorage.getItem(TOKEN_KEY) || null,
  me: null,
  users: [],
  expenses: [],
  settlements: [],
  balance: null,
  route: 'loading',
  noHousehold: false,
  sheet: null,
  pending: [],
  requestRoles: {},
  toast: null,
  loginForm: { email: '', password: '', error: null, loading: false },
  signupForm: defaultSignupForm(),
  draft: { amount: '', desc: '', category: null, participantIds: [], payerId: null },
  draftSaving: false,
  settleDraft: { counterpartId: null, amount: '' },
  settleSaving: false,
};

// ---------- helpers ----------

function fmt(n) {
  return Math.round(n || 0).toLocaleString('en-US');
}

function parseAmount(str) {
  const digits = String(str || '').replace(/[^0-9]/g, '');
  return digits ? Number(digits) : 0;
}

function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (name || '?').slice(0, 2).toUpperCase();
}

function avatarClass(userId, isMe) {
  if (isMe) return 'avatar--me';
  return PALETTE[Math.abs(userId) % PALETTE.length];
}

function householdLabel(count) {
  if (count <= 1) return 'Just you';
  const word = NUMBER_WORDS[count] || String(count);
  return `${word} of us`;
}

function nameOf(id) {
  const u = state.users.find((x) => x.id === id);
  return u ? u.name : 'Someone';
}

function monthLabel(isoDate) {
  const dt = new Date(isoDate + 'T00:00:00');
  return dt.toLocaleString('en-US', { month: 'long', year: 'numeric' });
}

function dayOfMonth(isoDate) {
  return isoDate.slice(8, 10);
}

// ---------- API client ----------

async function api(path, opts = {}) {
  const { method = 'GET', body, form, auth = true } = opts;
  const headers = {};
  if (auth) {
    if (!state.token) throw new Error('Not authenticated');
    headers['Authorization'] = 'Bearer ' + state.token;
  }
  let fetchBody;
  if (form) {
    fetchBody = new URLSearchParams(form).toString();
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
  } else if (body !== undefined) {
    fetchBody = JSON.stringify(body);
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(path, { method, headers, body: fetchBody });
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    let message = res.statusText || 'Request failed';
    if (data && data.detail) {
      message = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join('; ') : data.detail;
    }
    throw new Error(message);
  }
  return data;
}

// ---------- balance view ----------

function computeBalanceView() {
  const b = state.balance || { balances: [], settlements_to_make: [] };
  const mine = state.me ? b.balances.find((x) => x.user_id === state.me.id) : null;
  const net = mine ? mine.net : 0;
  const square = Math.abs(net) <= EPS;
  const owed = net > EPS;
  const debts = state.me
    ? b.settlements_to_make.filter((d) => d.from_user_id === state.me.id || d.to_user_id === state.me.id)
    : [];
  return { net, square, owed, debts };
}

function getSettleTarget() {
  const { debts } = computeBalanceView();
  const target = debts.find((d) => d.from_user_id === state.settleDraft.counterpartId || d.to_user_id === state.settleDraft.counterpartId);
  if (!target) return null;
  return { debt: target, iOwe: target.from_user_id === state.me.id, amount: target.amount };
}

// ---------- toast ----------

let toastTimer = null;
function flash(msg) {
  clearTimeout(toastTimer);
  state.toast = msg;
  render();
  toastTimer = setTimeout(() => { state.toast = null; render(); }, 2200);
}

// ---------- auth flow ----------

async function boot() {
  if (!state.token) { state.route = 'login'; return render(); }
  try {
    state.me = await api('/auth/me');
    await afterAuth();
  } catch (e) {
    state.token = null;
    localStorage.removeItem(TOKEN_KEY);
    state.route = 'login';
    render();
  }
}

async function afterAuth() {
  if (state.me.status !== 'approved') { state.route = 'pending'; state.noHousehold = false; return render(); }
  if (!state.me.household_id) { state.route = 'pending'; state.noHousehold = true; return render(); }
  await loadHome();
}

async function doLogin() {
  const { email, password } = state.loginForm;
  if (!email || !password) { state.loginForm.error = 'Enter your email and password.'; return render(); }
  state.loginForm.loading = true;
  state.loginForm.error = null;
  render();
  try {
    const tok = await api('/auth/login', { method: 'POST', auth: false, form: { username: email, password } });
    state.token = tok.access_token;
    localStorage.setItem(TOKEN_KEY, state.token);
    state.me = await api('/auth/me');
    state.loginForm = { email: '', password: '', error: null, loading: false };
    await afterAuth();
  } catch (e) {
    state.loginForm.loading = false;
    state.loginForm.error = e.message || 'Could not sign in.';
    render();
  }
}

async function openSignup() {
  state.route = 'signup';
  render();
  try {
    state.signupForm.households = await api('/households', { auth: false });
  } catch (e) { /* non-fatal, join tab will just show no options */ }
  render();
}

async function doSignup() {
  const f = state.signupForm;
  if (!f.name || !f.email || !f.password) { f.error = 'Fill in your name, email, and password.'; return render(); }
  if (f.password.length < 8) { f.error = 'Password needs at least 8 characters.'; return render(); }
  if (f.mode === 'create' && !f.householdName) { f.error = 'Name your household.'; return render(); }
  if (f.mode === 'join' && !f.householdId) { f.error = 'Pick a household to join.'; return render(); }
  f.loading = true;
  f.error = null;
  render();
  try {
    const payload = { name: f.name, email: f.email, password: f.password };
    if (f.mode === 'create') payload.household_name = f.householdName;
    else payload.household_id = Number(f.householdId);
    await api('/auth/signup', { method: 'POST', auth: false, body: payload });
    const tok = await api('/auth/login', { method: 'POST', auth: false, form: { username: f.email, password: f.password } });
    state.token = tok.access_token;
    localStorage.setItem(TOKEN_KEY, state.token);
    state.me = await api('/auth/me');
    state.signupForm = defaultSignupForm();
    await afterAuth();
  } catch (e) {
    f.loading = false;
    f.error = e.message || 'Could not create your account.';
    render();
  }
}

function doLogout() {
  state.token = null;
  localStorage.removeItem(TOKEN_KEY);
  Object.assign(state, { me: null, users: [], expenses: [], settlements: [], balance: null, pending: [], requestRoles: {}, sheet: null, route: 'login' });
  render();
}

// ---------- data loading ----------

async function refreshData() {
  const isAdmin = state.me.role === 'admin';
  const calls = [api('/users'), api('/expenses'), api('/balances'), api('/settlements')];
  if (isAdmin) calls.push(api('/users/pending'));
  const results = await Promise.all(calls);
  state.users = results[0];
  state.expenses = results[1];
  state.balance = results[2];
  state.settlements = results[3];
  state.pending = isAdmin ? results[4] : [];
}

async function loadHome() {
  try {
    await refreshData();
    state.route = 'home';
  } catch (e) {
    flash(e.message || 'Could not load your household.');
  }
  render();
}

async function goHousehold() {
  state.sheet = null;
  state.route = 'household';
  render();
  try {
    await refreshData();
  } catch (e) {
    flash(e.message || 'Could not refresh the household.');
  }
  render();
}

function toggleRequestRole(userId) {
  const cur = state.requestRoles[userId] || 'member';
  state.requestRoles[userId] = cur === 'admin' ? 'member' : 'admin';
  render();
}

async function approveRequest(userId) {
  const role = state.requestRoles[userId] || 'member';
  const person = state.pending.find((p) => p.id === userId);
  try {
    await api(`/users/${userId}/approve`, { method: 'PATCH', body: { role } });
    await refreshData();
    flash(`Approved · ${person ? person.name.split(' ')[0] : 'They'}${role === 'admin' ? ' is in as admin' : ' is in'}`);
  } catch (e) {
    flash(e.message || 'Could not approve.');
  }
  render();
}

// ---------- add expense ----------

function openAddSheet() {
  state.draft = {
    amount: '', desc: '', category: null,
    participantIds: state.users.map((u) => u.id),
    payerId: state.me.id,
  };
  state.sheet = 'add';
  render();
}

function toggleDraftPerson(id) {
  const ids = state.draft.participantIds;
  state.draft.participantIds = ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
  render();
}

function cyclePayer() {
  const ids = state.users.map((u) => u.id);
  if (!ids.length) return;
  const idx = ids.indexOf(state.draft.payerId);
  state.draft.payerId = ids[(idx + 1) % ids.length];
  render();
}

async function saveExpense() {
  const d = state.draft;
  const amount = parseAmount(d.amount);
  if (!amount) return flash('Enter an amount.');
  if (!d.participantIds.length) return flash('Tag at least one person.');
  state.draftSaving = true;
  render();
  try {
    await api('/expenses', {
      method: 'POST',
      body: {
        amount,
        description: d.desc || d.category || 'Expense',
        category: d.category || 'general',
        participant_ids: d.participantIds,
        payer_id: d.payerId,
      },
    });
    state.sheet = null;
    state.draftSaving = false;
    await refreshData();
    flash(`Saved · ${fmt(amount)} toman`);
  } catch (e) {
    state.draftSaving = false;
    flash(e.message || 'Could not save the expense.');
    render();
  }
}

// ---------- settle up ----------

function openSettleSheet() {
  const { debts } = computeBalanceView();
  const first = debts[0];
  const otherId = first ? (first.from_user_id === state.me.id ? first.to_user_id : first.from_user_id) : null;
  state.settleDraft = { counterpartId: otherId, amount: first ? fmt(first.amount) : '' };
  state.sheet = 'settle';
  render();
}

function pickCounterparty(otherId) {
  const { debts } = computeBalanceView();
  const target = debts.find((d) => d.from_user_id === otherId || d.to_user_id === otherId);
  state.settleDraft = { counterpartId: otherId, amount: target ? fmt(target.amount) : '' };
  render();
}

async function confirmSettle() {
  const t = getSettleTarget();
  const amount = parseAmount(state.settleDraft.amount);
  if (!t || !amount) return flash('Pick a person and an amount.');
  state.settleSaving = true;
  render();
  try {
    const payload = t.iOwe
      ? { from_user_id: state.me.id, to_user_id: t.debt.to_user_id, amount }
      : { from_user_id: t.debt.from_user_id, to_user_id: state.me.id, amount };
    await api('/settlements', { method: 'POST', body: payload });
    state.sheet = null;
    state.settleSaving = false;
    await refreshData();
    flash(`Logged · ${fmt(amount)} toman repaid`);
  } catch (e) {
    state.settleSaving = false;
    flash(e.message || 'Could not log the settlement.');
    render();
  }
}

// ---------- history grouping ----------

function buildHistoryGroups() {
  const rows = [];
  for (const e of state.expenses) {
    rows.push({
      kind: 'expense', sortKey: e.date, month: monthLabel(e.date), day: dayOfMonth(e.date),
      desc: e.description, cat: e.category, amount: e.amount,
      payerLabel: `${e.payer.name} paid`,
      parts: e.participants.map((p) => initials(p.name)),
      shareLabel: e.participants.length > 1 ? `split ${e.participants.length} ways` : `all ${e.participants[0] ? e.participants[0].name : ''}`,
      titleColor: 'var(--ink)', tagClass: 'tag',
    });
  }
  for (const s of state.settlements) {
    const fromName = nameOf(s.from_user_id);
    const toName = nameOf(s.to_user_id);
    rows.push({
      kind: 'settle', sortKey: s.date, month: monthLabel(s.date), day: dayOfMonth(s.date),
      desc: `${fromName} paid ${toName}`, cat: 'Settled', amount: s.amount,
      payerLabel: 'repayment',
      parts: [initials(fromName), initials(toName)],
      shareLabel: 'balance reduced',
      titleColor: 'var(--sage-soft-text)', tagClass: 'tag tag--settled',
    });
  }
  rows.sort((a, b) => b.sortKey.localeCompare(a.sortKey));
  const map = new Map();
  for (const r of rows) {
    if (!map.has(r.month)) map.set(r.month, []);
    map.get(r.month).push(r);
  }
  return [...map.entries()].map(([month, items]) => ({
    month, items,
    total: fmt(items.filter((i) => i.kind === 'expense').reduce((a, b) => a + b.amount, 0)) + ' spent',
  }));
}

// ---------- render: screens ----------

function renderLoading() {
  return `<div class="loading-screen">Loading…</div>`;
}

function renderLogin() {
  const f = state.loginForm;
  return `
    <div class="screen screen--auth">
      <div class="brand">Halves</div>
      <div class="tagline">One running total for the household.</div>
      <div class="form-stack">
        <div class="field">
          <label>Email</label>
          <input type="email" data-field="login.email" value="${escapeHtml(f.email)}" placeholder="you@example.com" autocomplete="username" />
        </div>
        <div class="field">
          <label>Password</label>
          <input type="password" data-field="login.password" value="${escapeHtml(f.password)}" placeholder="••••••••" autocomplete="current-password" />
        </div>
      </div>
      ${f.error ? `<div class="error-row"><div class="error-dot"></div><div class="error-text">${escapeHtml(f.error)}</div></div>` : ''}
      <button class="btn-primary" style="margin-top:26px" data-action="login.submit" ${f.loading ? 'disabled' : ''}>${f.loading ? 'Signing in…' : 'Sign in'}</button>
      <div class="link-row">
        <button data-action="signup.open">Create an account</button>
      </div>
    </div>
  `;
}

function renderSignup() {
  const f = state.signupForm;
  return `
    <div class="screen screen--auth">
      <div class="brand">Halves</div>
      <div class="tagline">Create your account.</div>
      <div class="form-stack">
        <div class="field"><label>Name</label><input data-field="signup.name" value="${escapeHtml(f.name)}" placeholder="Your name" /></div>
        <div class="field"><label>Email</label><input type="email" data-field="signup.email" value="${escapeHtml(f.email)}" placeholder="you@example.com" /></div>
        <div class="field"><label>Password</label><input type="password" data-field="signup.password" value="${escapeHtml(f.password)}" placeholder="At least 8 characters" /></div>
      </div>

      <div class="tabs-row">
        <button class="tab-btn ${f.mode === 'create' ? 'tab-btn--active' : ''}" data-action="signup.modeCreate">New household</button>
        <button class="tab-btn ${f.mode === 'join' ? 'tab-btn--active' : ''}" data-action="signup.modeJoin">Join existing</button>
      </div>

      ${f.mode === 'create' ? `
        <div class="field" style="margin-top:10px">
          <label>Household name</label>
          <input data-field="signup.householdName" value="${escapeHtml(f.householdName)}" placeholder="e.g. The Flat" />
        </div>
      ` : `
        <div class="field" style="margin-top:10px">
          <label>Household</label>
          <select data-field="signup.householdId">
            <option value="">Choose one…</option>
            ${f.households.map((h) => `<option value="${h.id}" ${String(h.id) === String(f.householdId) ? 'selected' : ''}>${escapeHtml(h.name)}</option>`).join('')}
          </select>
        </div>
      `}

      ${f.error ? `<div class="error-row"><div class="error-dot"></div><div class="error-text">${escapeHtml(f.error)}</div></div>` : ''}

      <button class="btn-primary" style="margin-top:22px" data-action="signup.submit" ${f.loading ? 'disabled' : ''}>${f.loading ? 'Creating…' : 'Create account'}</button>
      <div class="link-row"><button data-action="signup.toLogin">Back to sign in</button></div>
    </div>
  `;
}

function renderPending() {
  const title = state.noHousehold ? 'No household yet.' : 'Waiting on your household admin.';
  const copy = state.noHousehold
    ? 'Your account doesn’t belong to a household. Sign in again after an admin sorts you into one.'
    : 'Your account is created. The household admin approves new members — sign in again once it’s done.';
  return `
    <div class="screen screen--auth">
      <div class="brand">Halves</div>
      <div class="pending-dot-ring"><div class="pending-dot"></div></div>
      <div class="pending-title">${escapeHtml(title)}</div>
      <div class="pending-copy">${escapeHtml(copy)}</div>
      <button class="btn-secondary" style="margin-top:auto" data-action="logout">Back to sign in</button>
    </div>
  `;
}

function renderHome() {
  const { net, square, owed, debts } = computeBalanceView();
  const isEmpty = state.expenses.length === 0;
  const showSettle = !square && debts.length > 0;
  const recent = state.expenses.slice(0, 3);
  const stackUsers = state.users.slice(0, 4);

  return `
    <div class="screen">
      <div class="topbar">
        <div class="topbar-left">
          <div class="avatar-stack">
            ${stackUsers.map((u) => `<div class="avatar ${avatarClass(u.id, u.id === state.me.id)}">${initials(u.name)}</div>`).join('')}
          </div>
          <div class="household-label">${householdLabel(state.users.length)}</div>
        </div>
        <div class="icon-btn-wrap">
          <button class="icon-btn" data-action="menu.open" title="Menu">
            <div class="dot"></div><div class="dot"></div><div class="dot"></div>
          </button>
          ${state.me.role === 'admin' && state.pending.length > 0 ? `<div class="badge-count">${state.pending.length}</div>` : ''}
        </div>
      </div>

      <div class="balance-block">
        <div class="eyebrow">${isEmpty ? 'Nothing to split yet' : square ? 'All square' : owed ? 'You’re owed' : 'You owe'}</div>
        <div class="balance-amount-row">
          <div class="balance-amount tabular">${fmt(Math.abs(net))}</div>
          <div class="balance-unit">toman</div>
        </div>
        <div class="balance-sub">${
          isEmpty
            ? 'Add your first shared cost and the balance appears here.'
            : square
              ? 'Every expense and repayment is accounted for. Nothing owed in either direction.'
              : `Your net position across ${state.expenses.length} expense${state.expenses.length === 1 ? '' : 's'}.`
        }</div>
      </div>

      ${debts.length ? `
        <div class="debts-list">
          ${debts.map((d) => {
            const iAmFrom = d.from_user_id === state.me.id;
            const otherName = iAmFrom ? d.to_name : d.from_name;
            return `<div class="debt-row">
              <div class="avatar avatar--muted avatar-sm">${initials(otherName)}</div>
              <div class="debt-line">${iAmFrom ? `You pay ${escapeHtml(otherName)}` : `${escapeHtml(otherName)} pays you`}</div>
              <div class="debt-amount tabular">${fmt(d.amount)}</div>
            </div>`;
          }).join('')}
        </div>
      ` : ''}

      <button class="btn-primary" style="margin-top:34px;display:flex;align-items:center;justify-content:center;gap:10px" data-action="openAdd">
        <span style="font-size:20px;line-height:1">+</span><span>Add expense</span>
      </button>

      ${showSettle ? `<button class="btn-secondary" style="margin-top:10px" data-action="openSettle">Settle up</button>` : ''}

      ${!isEmpty ? `
        <div style="margin-top:40px">
          <div class="section-head">
            <div class="eyebrow">Recent</div>
            <a href="#" data-action="toHistory" style="font-size:13.5px;color:var(--sage-soft-text);text-decoration:none">All ${state.expenses.length} expenses</a>
          </div>
          <div class="recent-card">
            ${recent.map((e) => `
              <div class="recent-row">
                <div style="flex:1;min-width:0">
                  <div class="recent-desc">${escapeHtml(e.description)}</div>
                  <div class="recent-meta">${escapeHtml(e.payer.name)} paid · ${escapeHtml(e.category)} · ${e.participants.length > 1 ? 'split ' + e.participants.length : 'all ' + escapeHtml(e.participants[0] ? e.participants[0].name : '')}</div>
                </div>
                <div class="recent-amount tabular">${fmt(e.amount)}</div>
              </div>
            `).join('')}
          </div>
        </div>
      ` : `
        <div class="empty-state">
          <h3>Nothing logged yet</h3>
          <p>The first expense any of you adds shows up here.</p>
        </div>
      `}
    </div>
  `;
}

function renderHistory() {
  const { net, square, owed, debts } = computeBalanceView();
  const groups = buildHistoryGroups();
  const isEmpty = state.expenses.length === 0 && state.settlements.length === 0;
  const showSettle = !square && debts.length > 0;

  return `
    <div class="screen">
      <div class="topbar">
        <button class="icon-btn" data-action="toHome"><div class="chevron"></div></button>
        <div class="eyebrow">All ${state.expenses.length} expenses</div>
      </div>
      <div style="margin-top:26px;display:flex;align-items:flex-end;justify-content:space-between;gap:14px">
        <div>
          <div style="font-family:var(--font-serif);font-size:32px;line-height:1.1">History</div>
          <div style="margin-top:7px;font-size:13.5px" class="muted">${square ? 'All square' : owed ? 'You’re owed' : 'You owe'} <span class="tabular" style="color:var(--ink)">${fmt(Math.abs(net))}</span></div>
        </div>
        ${showSettle ? `<button class="btn-pill-sage" data-action="openSettle">Settle up</button>` : ''}
      </div>

      ${isEmpty ? `<div class="empty-state" style="margin-top:36px"><p style="margin:0">No expenses yet.<br />Add one and it lands here.</p></div>` : ''}

      ${groups.map((g) => `
        <div class="month-group">
          <div class="month-head"><div class="eyebrow">${escapeHtml(g.month)}</div><div class="month-total tabular">${g.total}</div></div>
          ${g.items.map((e) => `
            <div class="history-row">
              <div class="history-day tabular">${e.day}</div>
              <div class="history-main">
                <div class="history-top">
                  <div class="history-desc" style="color:${e.titleColor}">${escapeHtml(e.desc)}</div>
                  <div class="history-amount tabular" style="color:${e.titleColor}">${fmt(e.amount)}</div>
                </div>
                <div class="history-tags">
                  <span class="${e.tagClass}">${escapeHtml(e.cat)}</span>
                  <span class="payer-label">${escapeHtml(e.payerLabel)}</span>
                  <span class="parts-row">${e.parts.map((p) => `<span class="avatar avatar-xs">${p}</span>`).join('')}</span>
                  <span class="share-label">${escapeHtml(e.shareLabel)}</span>
                </div>
              </div>
            </div>
          `).join('')}
        </div>
      `).join('')}
      <div style="height:20px"></div>
    </div>
  `;
}

// ---------- render: sheets ----------

function renderAddSheet() {
  const d = state.draft;
  const amt = parseAmount(d.amount);
  const splitHint = d.participantIds.length === 0
    ? 'tag at least one person'
    : !amt
      ? `${d.participantIds.length} tagged`
      : d.participantIds.length === 1
        ? `${nameOf(d.participantIds[0])} carries all of it`
        : `${fmt(amt / d.participantIds.length)} each`;
  const payer = state.users.find((u) => u.id === d.payerId) || state.me;

  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head">
        <div class="sheet-title">New expense</div>
        <button class="sheet-cancel" data-action="sheet.close">Cancel</button>
      </div>

      <div class="amount-row">
        <input class="amount-input tabular" inputmode="numeric" data-field="draft.amount" value="${escapeHtml(d.amount)}" placeholder="0" />
        <div class="amount-unit">toman</div>
      </div>
      <div class="amount-rule"></div>

      <input class="desc-input" data-field="draft.desc" value="${escapeHtml(d.desc)}" placeholder="What was it for?" />

      <div class="chip-row" style="margin-top:16px">
        ${CATS.map((c) => `<button class="chip ${d.category === c ? 'chip--active' : ''}" data-action="draft.pickCategory" data-cat="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join('')}
      </div>

      <div class="split-block">
        <div class="split-head"><div style="font-size:14.5px" class="muted">Split between</div><div style="font-size:12.5px" class="faint">${splitHint}</div></div>
        <div class="people-row">
          ${state.users.map((u) => `
            <button class="person-chip ${d.participantIds.includes(u.id) ? 'person-chip--active' : ''}" data-action="draft.togglePerson" data-user-id="${u.id}">
              <span class="avatar avatar-sm ${avatarClass(u.id, u.id === state.me.id)}">${initials(u.name)}</span>
              <span>${escapeHtml(u.name)}</span>
            </button>
          `).join('')}
        </div>
      </div>

      <div class="payer-date-row">
        <button class="payer-btn" data-action="draft.cyclePayer">
          <span class="avatar avatar-sm avatar--muted">${initials(payer.name)}</span>
          <span>${escapeHtml(payer.name)} paid</span>
        </button>
        <button class="date-btn" disabled>Today</button>
      </div>

      <button class="btn-primary" style="margin-top:16px" data-action="draft.save" ${state.draftSaving ? 'disabled' : ''}>${state.draftSaving ? 'Saving…' : 'Save expense'}</button>
    </div>
  `;
}

function renderSettleSheet() {
  const { debts } = computeBalanceView();
  const sd = state.settleDraft;
  const t = getSettleTarget();
  const targetAmount = t ? t.amount : 0;
  const amt = parseAmount(sd.amount);

  let hint = 'Enter an amount.';
  if (t && amt) {
    const left = targetAmount - amt;
    if (left > 0.5) hint = `${fmt(left)} would still be outstanding between you two.`;
    else if (left < -0.5) hint = `That’s ${fmt(-left)} more than owed — the balance flips the other way.`;
    else hint = 'This clears the balance between you two completely.';
  }

  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head"><div class="sheet-title">Settle up</div><button class="sheet-cancel" data-action="sheet.close">Cancel</button></div>

      <div class="counterparty-row">
        ${debts.map((d) => {
          const other = d.from_user_id === state.me.id ? d.to_user_id : d.from_user_id;
          const otherName = d.from_user_id === state.me.id ? d.to_name : d.from_name;
          const label = d.from_user_id === state.me.id ? `Pay ${otherName}` : `${otherName} pays you`;
          const active = other === sd.counterpartId;
          return `<button class="counterparty-chip ${active ? 'counterparty-chip--active' : ''}" data-action="settle.pick" data-user-id="${other}">
            <span>${escapeHtml(label)}</span><span class="tabular">${fmt(d.amount)} toman</span>
          </button>`;
        }).join('')}
      </div>

      <div class="settle-line">${
        t
          ? (t.iOwe
              ? `You owe ${escapeHtml(t.debt.to_name)} ${fmt(targetAmount)}. Log what you actually handed over — part of it is fine.`
              : `${escapeHtml(t.debt.from_name)} owes you ${fmt(targetAmount)}. Log what actually came back.`)
          : 'Pick who you’re settling with.'
      }</div>

      <div class="amount-row">
        <input class="amount-input amount-input--sm tabular" inputmode="numeric" data-field="settle.amount" value="${escapeHtml(sd.amount)}" />
        <div class="amount-unit">toman</div>
      </div>
      <div class="amount-rule"></div>

      <div class="quick-row">
        <button class="quick-btn" data-action="settle.full">Full amount</button>
        <button class="quick-btn" data-action="settle.half">Half</button>
      </div>

      <div class="settle-hint">${hint}</div>

      <button class="btn-sage" style="margin-top:14px" data-action="settle.confirm" ${state.settleSaving ? 'disabled' : ''}>${state.settleSaving ? 'Saving…' : 'Mark as paid'}</button>
    </div>
  `;
}

function renderMenuSheet() {
  const isAdmin = state.me.role === 'admin';
  const pendingCount = state.pending.length;
  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      ${isAdmin ? `
        <button class="menu-row" data-action="toHousehold">
          <span style="flex:1">Household</span>
          ${pendingCount > 0 ? `<span class="menu-badge">${pendingCount}</span>` : ''}
          <span class="faint" style="font-size:13px">${pendingCount > 0 ? 'waiting' : 'all in'}</span>
        </button>
      ` : ''}
      <button class="menu-row" data-action="toHistory">
        <span style="flex:1">History</span>
        <span class="faint" style="font-size:13px">All ${state.expenses.length} expenses</span>
      </button>
      <button class="menu-row menu-row--last" data-action="logout">
        <span style="flex:1;color:var(--ink-soft)">Sign out</span>
      </button>
      <button class="btn-secondary" style="margin-top:14px" data-action="sheet.close">Close</button>
    </div>
  `;
}

function renderHousehold() {
  const members = state.users;
  const pending = state.pending;
  const waitingCount = pending.length;

  return `
    <div class="screen">
      <div class="topbar">
        <button class="icon-btn" data-action="toHome"><div class="chevron"></div></button>
        <div class="eyebrow">Admin</div>
      </div>

      <div style="margin-top:26px">
        <div style="font-family:var(--font-serif);font-size:32px;line-height:1.1">Household</div>
        <div style="margin-top:7px;font-size:13.5px" class="muted">${members.length} member${members.length === 1 ? '' : 's'} · ${waitingCount ? waitingCount + ' waiting to join' : 'nobody waiting'}</div>
      </div>

      <div class="hh-section">
        <div class="hh-section-head"><div class="eyebrow">Requests</div><div style="font-size:12px" class="faint">${waitingCount ? waitingCount + ' waiting' : 'clear'}</div></div>
        ${waitingCount === 0 ? `<div class="hh-empty">Nobody waiting.<br />New sign-ups land here for you to let in.</div>` : ''}
        ${pending.map((p) => {
          const role = state.requestRoles[p.id] || 'member';
          return `
            <div class="request-row">
              <div class="request-top">
                <div class="avatar avatar-34 avatar--muted">${initials(p.name)}</div>
                <div style="flex:1;min-width:0">
                  <div class="request-name">${escapeHtml(p.name)}</div>
                  <div class="request-email">${escapeHtml(p.email)}</div>
                </div>
                <button class="approve-btn" data-action="household.approve" data-user-id="${p.id}">Approve</button>
              </div>
              <div class="request-role-row">
                <span class="faint" style="font-size:12.5px">Joins as</span>
                <button class="role-pill-btn" data-action="household.toggleRole" data-user-id="${p.id}">${role === 'admin' ? 'Admin' : 'Member'}</button>
                <button class="role-swap-link" data-action="household.toggleRole" data-user-id="${p.id}">${role === 'admin' ? 'make member instead' : 'make admin instead'}</button>
              </div>
            </div>
          `;
        }).join('')}
      </div>

      <div class="hh-section">
        <div class="hh-section-head"><div class="eyebrow">Members</div><div style="font-size:12px" class="faint">${members.length} approved</div></div>
        ${members.map((m) => `
          <div class="member-row">
            <div class="avatar avatar-30 ${avatarClass(m.id, m.id === state.me.id)}">${initials(m.name)}</div>
            <div style="flex:1;min-width:0">
              <div class="member-name">${escapeHtml(m.name)}</div>
              <div class="member-email">${escapeHtml(m.email)}</div>
            </div>
            <div class="member-role-badge ${m.role === 'admin' ? 'member-role-badge--admin' : 'member-role-badge--member'}">${m.role === 'admin' ? 'Admin' : 'Member'}</div>
          </div>
        `).join('')}
        <div class="hh-footnote">Removing or demoting a member isn’t possible yet — no endpoint for it.</div>
      </div>
      <div style="height:20px"></div>
    </div>
  `;
}

// ---------- root render ----------

function buildHtml() {
  let html = '';
  switch (state.route) {
    case 'login': html = renderLogin(); break;
    case 'signup': html = renderSignup(); break;
    case 'pending': html = renderPending(); break;
    case 'home': html = renderHome(); break;
    case 'history': html = renderHistory(); break;
    case 'household': html = renderHousehold(); break;
    default: html = renderLoading();
  }
  if (state.sheet === 'add') html += renderAddSheet();
  if (state.sheet === 'settle') html += renderSettleSheet();
  if (state.sheet === 'menu') html += renderMenuSheet();
  if (state.toast) html += `<div class="toast">${escapeHtml(state.toast)}</div>`;
  return html;
}

function render() {
  const app = document.getElementById('app');
  const active = document.activeElement;
  const activeField = active && active.matches && active.matches('[data-field]') ? active.dataset.field : null;
  const selStart = active && typeof active.selectionStart === 'number' ? active.selectionStart : null;
  const selEnd = active && typeof active.selectionEnd === 'number' ? active.selectionEnd : null;

  app.innerHTML = buildHtml();

  if (activeField) {
    const el = app.querySelector(`[data-field="${CSS.escape(activeField)}"]`);
    if (el) {
      el.focus();
      const isAmountField = activeField === 'draft.amount' || activeField === 'settle.amount';
      if (el.setSelectionRange) {
        try {
          if (isAmountField) el.setSelectionRange(el.value.length, el.value.length);
          else if (selStart != null) el.setSelectionRange(selStart, selEnd);
        } catch (e) { /* not a text-selectable input */ }
      }
    }
  }
}

// ---------- event handling ----------

function handleFieldInput(field, value) {
  switch (field) {
    case 'login.email': state.loginForm.email = value; state.loginForm.error = null; break;
    case 'login.password': state.loginForm.password = value; state.loginForm.error = null; break;
    case 'signup.name': state.signupForm.name = value; break;
    case 'signup.email': state.signupForm.email = value; break;
    case 'signup.password': state.signupForm.password = value; break;
    case 'signup.householdName': state.signupForm.householdName = value; break;
    case 'signup.householdId': state.signupForm.householdId = value; break;
    case 'draft.amount': state.draft.amount = value ? fmt(parseAmount(value)) : ''; break;
    case 'draft.desc': state.draft.desc = value; break;
    case 'settle.amount': state.settleDraft.amount = value ? fmt(parseAmount(value)) : ''; break;
    default: return;
  }
  render();
}

function handleAction(action, el) {
  switch (action) {
    case 'login.submit': return doLogin();
    case 'signup.open': return openSignup();
    case 'signup.toLogin': state.route = 'login'; return render();
    case 'signup.modeCreate': state.signupForm.mode = 'create'; return render();
    case 'signup.modeJoin': state.signupForm.mode = 'join'; return render();
    case 'signup.submit': return doSignup();
    case 'logout': return doLogout();
    case 'menu.open': state.sheet = 'menu'; return render();
    case 'openAdd': return openAddSheet();
    case 'openSettle': return openSettleSheet();
    case 'sheet.close': state.sheet = null; return render();
    case 'toHistory': state.sheet = null; state.route = 'history'; return render();
    case 'toHome': state.sheet = null; state.route = 'home'; return render();
    case 'toHousehold': return goHousehold();
    case 'household.toggleRole': return toggleRequestRole(Number(el.dataset.userId));
    case 'household.approve': return approveRequest(Number(el.dataset.userId));
    case 'draft.pickCategory': state.draft.category = el.dataset.cat; return render();
    case 'draft.togglePerson': return toggleDraftPerson(Number(el.dataset.userId));
    case 'draft.cyclePayer': return cyclePayer();
    case 'draft.save': return saveExpense();
    case 'settle.pick': return pickCounterparty(Number(el.dataset.userId));
    case 'settle.full': { const t = getSettleTarget(); if (t) state.settleDraft.amount = fmt(t.amount); return render(); }
    case 'settle.half': { const t = getSettleTarget(); if (t) state.settleDraft.amount = fmt(t.amount / 2); return render(); }
    case 'settle.confirm': return confirmSettle();
    default: return;
  }
}

function initEvents() {
  const app = document.getElementById('app');

  app.addEventListener('click', (e) => {
    const actionEl = e.target.closest('[data-action]');
    if (!actionEl) return;
    e.preventDefault();
    handleAction(actionEl.dataset.action, actionEl);
  });

  app.addEventListener('input', (e) => {
    const el = e.target.closest('[data-field]');
    if (!el) return;
    handleFieldInput(el.dataset.field, el.value);
  });

  app.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const el = e.target.closest('[data-field]');
    if (!el) return;
    if (el.dataset.field === 'login.password') doLogin();
    if (el.dataset.field === 'signup.password') doSignup();
  });
}

// ---------- boot ----------

initEvents();
render();
boot();
