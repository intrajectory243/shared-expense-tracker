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
  former: [],
  household: null,
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
  selectedMemberId: null,
  inviteForm: { name: '', email: '', role: 'member' },
  inviteSaving: false,
  renameDraft: '',
  renameSaving: false,
  acceptInvite: null,
  editShares: { expenseId: null, amount: 0, draft: {} },
  editSharesSaving: false,
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
  const u = state.users.find((x) => x.id === id) || state.former.find((x) => x.id === id);
  return u ? u.name : 'Someone';
}

function firstName(name) {
  return String(name || '').trim().split(/\s+/)[0] || name;
}

function netOf(userId) {
  const b = state.balance || { balances: [] };
  const entry = b.balances.find((x) => x.user_id === userId);
  return entry ? entry.net : 0;
}

function standingLabel(userId) {
  const n = netOf(userId);
  if (Math.abs(n) <= EPS) return 'Square with the household.';
  return n > 0 ? `Still owed ${fmt(n)} toman.` : `Still owes ${fmt(-n)} toman.`;
}

function formerStandingShort(m) {
  const n = netOf(m.id);
  const tier = m.status === 'moved_out' ? 'moved out' : 'no access';
  const bal = Math.abs(n) <= EPS ? 'square' : n > 0 ? `owed ${fmt(n)}` : `owes ${fmt(-n)}`;
  return `${tier} · ${bal}`;
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
  const inviteToken = new URLSearchParams(location.search).get('invite');
  if (inviteToken) return bootAcceptInvite(inviteToken);

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

async function bootAcceptInvite(token) {
  state.route = 'accept-invite';
  state.acceptInvite = { token, name: '', email: '', householdName: '', password: '', error: null, loading: false, loaded: false };
  render();
  try {
    const preview = await api(`/auth/invite/${encodeURIComponent(token)}`, { auth: false });
    state.acceptInvite.name = preview.name;
    state.acceptInvite.email = preview.email;
    state.acceptInvite.householdName = preview.household_name;
    state.acceptInvite.loaded = true;
  } catch (e) {
    state.acceptInvite.error = e.message || 'This invite link is no longer valid.';
  }
  render();
}

async function afterAuth() {
  // Only truly-pending sign-ups get parked; moved_out still gets full home
  // access (read + settle), just restricted from logging new expenses.
  if (state.me.status === 'pending') { state.route = 'pending'; state.noHousehold = false; return render(); }
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
  clearTimeout(toastTimer);
  Object.assign(state, {
    me: null, users: [], former: [], household: null, expenses: [], settlements: [], balance: null,
    pending: [], requestRoles: {}, sheet: null, route: 'login', selectedMemberId: null,
    inviteForm: { name: '', email: '', role: 'member' }, toast: null,
  });
  render();
}

// ---------- data loading ----------

async function refreshData() {
  const isAdmin = state.me.role === 'admin';
  const calls = [api('/users'), api('/expenses'), api('/balances'), api('/settlements'), api('/households', { auth: false })];
  if (isAdmin) calls.push(api('/users/pending'), api('/users/former'));
  const results = await Promise.all(calls);
  state.users = results[0];
  state.expenses = results[1];
  state.balance = results[2];
  state.settlements = results[3];
  state.household = results[4].find((h) => h.id === state.me.household_id) || null;
  state.pending = isAdmin ? results[5] : [];
  state.former = isAdmin ? results[6] : [];
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

async function declineRequest(userId) {
  const person = state.pending.find((p) => p.id === userId);
  try {
    await api(`/users/${userId}`, { method: 'DELETE' });
    await refreshData();
    flash(`Declined${person ? ' · ' + firstName(person.name) + ' won’t get in' : ''}`);
  } catch (e) {
    flash(e.message || 'Could not decline.');
  }
  render();
}

// ---------- member detail sheet ----------

function findMember(id) {
  return state.users.find((u) => u.id === id) || state.former.find((u) => u.id === id) || null;
}

function activeAdminCount() {
  return state.users.filter((u) => u.role === 'admin').length;
}

function openMemberSheet(userId) {
  state.selectedMemberId = userId;
  state.sheet = 'member';
  render();
}

async function pickMemberRole(role) {
  const selected = findMember(state.selectedMemberId);
  if (!selected || selected.role === role) return;
  try {
    await api(`/users/${selected.id}`, { method: 'PATCH', body: { role } });
    await refreshData();
    flash(`${firstName(selected.name)} is now ${role === 'admin' ? 'an admin' : 'a member'}`);
  } catch (e) {
    flash(e.message || 'Could not change their role.');
  }
  render();
}

async function runAccessAction(to, blocked) {
  const selected = findMember(state.selectedMemberId);
  if (!selected) return;
  if (blocked) {
    flash(selected.id === state.me.id ? 'Ask another admin to change your own access.' : 'A household needs at least one admin.');
    return;
  }
  try {
    await api(`/users/${selected.id}`, { method: 'PATCH', body: { status: to } });
    await refreshData();
    const who = firstName(selected.name);
    state.sheet = null;
    flash(to === 'approved' ? `${who} is back in` : to === 'moved_out' ? `${who} moved out · history and balance stay` : `${who} can no longer sign in`);
  } catch (e) {
    flash(e.message || 'Could not update their access.');
  }
  render();
}

// ---------- household rename ----------

function openRenameSheet() {
  state.renameDraft = state.household ? state.household.name : '';
  state.renameSaving = false;
  state.sheet = 'rename';
  render();
}

async function saveRename() {
  const name = (state.renameDraft || '').trim();
  if (!name) return flash('Give the household a name.');
  state.renameSaving = true;
  render();
  try {
    await api(`/households/${state.me.household_id}`, { method: 'PATCH', body: { name } });
    await refreshData();
    state.sheet = null;
    state.renameSaving = false;
    flash(`Renamed · ${name}`);
  } catch (e) {
    state.renameSaving = false;
    flash(e.message || 'Could not rename the household.');
    render();
  }
}

// ---------- invite ----------

function openInviteSheet() {
  state.inviteForm = { name: '', email: '', role: 'member' };
  state.inviteSaving = false;
  state.sheet = 'invite';
  render();
}

async function sendInvite() {
  const f = state.inviteForm;
  const name = (f.name || '').trim();
  const email = (f.email || '').trim();
  if (!name || !email) return flash('Name and email, then create the link.');
  state.inviteSaving = true;
  render();
  try {
    const res = await api('/users/invite', { method: 'POST', body: { name, email, role: f.role } });
    await refreshData();
    state.sheet = null;
    state.inviteSaving = false;
    const link = `${location.origin}${location.pathname}?invite=${res.invite_token}`;
    try { await navigator.clipboard.writeText(link); } catch (e) { /* clipboard may be unavailable; link is still valid to share manually */ }
    flash(`Invited · link copied for ${firstName(name)}`);
  } catch (e) {
    state.inviteSaving = false;
    flash(e.message || 'Could not create the invite.');
    render();
  }
}

// ---------- accept invite ----------

async function submitAcceptInvite() {
  const ai = state.acceptInvite;
  if (!ai.password || ai.password.length < 8) { ai.error = 'Password needs at least 8 characters.'; return render(); }
  ai.loading = true;
  ai.error = null;
  render();
  try {
    await api('/auth/accept-invite', { method: 'POST', auth: false, body: { token: ai.token, password: ai.password } });
    const tok = await api('/auth/login', { method: 'POST', auth: false, form: { username: ai.email, password: ai.password } });
    state.token = tok.access_token;
    localStorage.setItem(TOKEN_KEY, state.token);
    state.me = await api('/auth/me');
    history.replaceState(null, '', location.pathname);
    await afterAuth();
  } catch (e) {
    ai.loading = false;
    ai.error = e.message || 'Could not join.';
    render();
  }
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

// ---------- edit expense shares (admin) ----------

function openEditSharesSheet(expenseId) {
  const expense = state.expenses.find((e) => e.id === expenseId);
  if (!expense) return;
  const draft = {};
  for (const s of expense.shares) draft[s.user_id] = s.share;
  state.editShares = { expenseId, amount: expense.amount, draft };
  state.sheet = 'editShares';
  render();
}

function toggleShareParticipant(userId) {
  const draft = state.editShares.draft;
  if (draft[userId] !== undefined) delete draft[userId];
  else draft[userId] = 1;
  render();
}

function bumpShare(userId, delta) {
  const draft = state.editShares.draft;
  if (draft[userId] === undefined) return;
  draft[userId] = Math.max(1, draft[userId] + delta);
  render();
}

async function saveShares() {
  const es = state.editShares;
  const entries = Object.entries(es.draft);
  if (!entries.length) return flash('Tag at least one person.');
  state.editSharesSaving = true;
  render();
  try {
    await api(`/expenses/${es.expenseId}/shares`, {
      method: 'PATCH',
      body: { participants: entries.map(([userId, share]) => ({ user_id: Number(userId), share })) },
    });
    state.sheet = null;
    state.editSharesSaving = false;
    await refreshData();
    flash('Split updated');
  } catch (e) {
    state.editSharesSaving = false;
    flash(e.message || 'Could not update the split.');
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
      kind: 'expense', id: e.id, sortKey: e.date, month: monthLabel(e.date), day: dayOfMonth(e.date),
      desc: e.description, cat: e.category, amount: e.amount,
      payerLabel: `${e.payer.name} paid`,
      // Most expenses are logged by the payer themselves -- only call out
      // who actually entered it when that's not the case, to avoid clutter.
      loggedByLabel: e.created_by.id !== e.payer.id ? `logged by ${e.created_by.name}` : null,
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
      loggedByLabel: null,
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
  const isMovedOut = state.me.status === 'moved_out';
  const householdName = state.household ? state.household.name : 'this household';
  const topLabel = isMovedOut ? `${escapeHtml(householdName)} · past flat` : householdLabel(state.users.length);

  return `
    <div class="screen">
      <div class="topbar">
        <div class="topbar-left">
          <div class="avatar-stack">
            ${stackUsers.map((u) => `<div class="avatar ${avatarClass(u.id, u.id === state.me.id)}">${initials(u.name)}</div>`).join('')}
          </div>
          <div class="household-label">${topLabel}</div>
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

      ${!isMovedOut ? `
        <button class="btn-primary" style="margin-top:34px;display:flex;align-items:center;justify-content:center;gap:10px" data-action="openAdd">
          <span style="font-size:20px;line-height:1">+</span><span>Add expense</span>
        </button>
      ` : `
        <div class="restricted-card">
          <div class="eyebrow">Moved out</div>
          <p>You’re no longer in ${escapeHtml(householdName)}. You can settle what’s outstanding and look back through history — logging new expenses is off.</p>
        </div>
      `}

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
          ${g.items.map((e) => {
            const editable = state.me.role === 'admin' && e.kind === 'expense';
            const tag = editable ? 'button' : 'div';
            const attrs = editable ? `data-action="history.editShares" data-expense-id="${e.id}"` : '';
            return `
            <${tag} class="history-row" ${attrs}>
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
                  ${e.loggedByLabel ? `<span class="payer-label">${escapeHtml(e.loggedByLabel)}</span>` : ''}
                </div>
              </div>
            </${tag}>`;
          }).join('')}
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

function renderEditSharesSheet() {
  const es = state.editShares;
  const draft = es.draft;
  const totalWeight = Object.values(draft).reduce((a, b) => a + b, 0);

  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head">
        <div class="sheet-title">Edit split</div>
        <button class="sheet-cancel" data-action="sheet.close">Cancel</button>
      </div>

      <div class="split-block">
        <div class="split-head"><div style="font-size:14.5px" class="muted">Shares</div><div style="font-size:12.5px" class="faint">${fmt(es.amount)} toman total</div></div>
        ${state.users.map((u) => {
          const on = draft[u.id] !== undefined;
          const share = draft[u.id] || 1;
          const dollar = on && totalWeight > 0 ? fmt(es.amount * share / totalWeight) : '';
          return `
            <div class="share-row">
              <button class="share-toggle ${on ? 'share-toggle--on' : ''}" data-action="shares.togglePerson" data-user-id="${u.id}">
                <span class="avatar avatar-sm ${avatarClass(u.id, u.id === state.me.id)}">${initials(u.name)}</span>
                <span class="share-name">${escapeHtml(u.name)}</span>
              </button>
              ${on ? `
                <span class="share-dollar tabular faint">${dollar}</span>
                <div class="stepper">
                  <button class="stepper-btn" data-action="shares.dec" data-user-id="${u.id}">−</button>
                  <span class="stepper-value tabular">${share}</span>
                  <button class="stepper-btn" data-action="shares.inc" data-user-id="${u.id}">+</button>
                </div>
              ` : ''}
            </div>
          `;
        }).join('')}
      </div>

      <button class="btn-primary" style="margin-top:18px" data-action="shares.save" ${state.editSharesSaving ? 'disabled' : ''}>${state.editSharesSaving ? 'Saving…' : 'Save split'}</button>
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
  const former = state.former;
  const waitingCount = pending.length;
  const householdName = state.household ? state.household.name : '';

  return `
    <div class="screen">
      <div class="topbar">
        <button class="icon-btn" data-action="toHome"><div class="chevron"></div></button>
        <div class="eyebrow">Admin</div>
      </div>

      <div style="margin-top:26px">
        <button class="hh-title-btn" data-action="household.openRename">
          <span class="hh-title">${escapeHtml(householdName)}</span>
          <span class="hh-rename-hint">rename</span>
        </button>
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
                <span class="faint" style="font-size:12.5px">${p.invited ? 'Invited as' : 'Joins as'}</span>
                <button class="role-pill-btn" data-action="household.toggleRole" data-user-id="${p.id}">${role === 'admin' ? 'Admin' : 'Member'}</button>
                <button class="role-swap-link" data-action="household.toggleRole" data-user-id="${p.id}">${role === 'admin' ? 'make member instead' : 'make admin instead'}</button>
                <button class="request-decline-link" data-action="household.decline" data-user-id="${p.id}">Decline</button>
              </div>
            </div>
          `;
        }).join('')}
      </div>

      <div class="hh-section">
        <div class="hh-section-head"><div class="eyebrow">Members</div><div style="font-size:12px" class="faint">${members.length} approved</div></div>
        ${members.map((m) => `
          <button class="member-row" data-action="member.open" data-user-id="${m.id}">
            <div class="avatar avatar-30 ${avatarClass(m.id, m.id === state.me.id)}">${initials(m.name)}</div>
            <div style="flex:1;min-width:0">
              <div class="member-name">${escapeHtml(m.name)}</div>
              <div class="member-email">${escapeHtml(m.email)}</div>
            </div>
            <div class="member-role-badge ${m.role === 'admin' ? 'member-role-badge--admin' : 'member-role-badge--member'}">${m.role === 'admin' ? 'Admin' : 'Member'}</div>
            <div class="chevron-right"></div>
          </button>
        `).join('')}
        <button class="hh-add-btn" data-action="household.openInvite">
          <span style="font-size:18px;line-height:1;font-weight:400">+</span><span>Add someone manually</span>
        </button>
      </div>

      ${former.length ? `
        <div class="hh-section">
          <div class="hh-section-head"><div class="eyebrow">No longer in the flat</div><div style="font-size:12px" class="faint">history kept</div></div>
          ${former.map((m) => `
            <button class="former-row" data-action="member.open" data-user-id="${m.id}">
              <div class="avatar-dashed">${initials(m.name)}</div>
              <div class="former-name">${escapeHtml(m.name)}</div>
              <div class="former-standing tabular">${escapeHtml(formerStandingShort(m))}</div>
              <div class="chevron-right"></div>
            </button>
          `).join('')}
          <div class="hh-footnote">Their expenses, shares and any outstanding balance stay in the books.</div>
        </div>
      ` : ''}
      <div style="height:20px"></div>
    </div>
  `;
}

function renderMemberSheet() {
  const selected = findMember(state.selectedMemberId);
  if (!selected) return '';
  const isActive = selected.status === 'approved';
  const isSelf = selected.id === state.me.id;
  const isLastAdmin = selected.role === 'admin' && selected.status === 'approved' && activeAdminCount() <= 1;

  const roleOptionsHtml = ['member', 'admin'].map((r) => {
    const on = selected.role === r;
    const locked = isLastAdmin && r === 'member';
    const cls = on ? 'role-option-btn role-option-btn--on' : locked ? 'role-option-btn role-option-btn--locked' : 'role-option-btn';
    return `<button class="${cls}" data-action="member.pickRole" data-role="${r}">${r === 'admin' ? 'Admin' : 'Member'}</button>`;
  }).join('');
  const roleNote = selected.role === 'admin'
    ? 'Admins approve sign-ups, add and remove people, and rename the household.'
    : 'Members log and settle expenses. They never see this screen.';

  const st = selected.status;
  const firstNm = firstName(selected.name);
  const rawFirst = st === 'approved'
    ? { label: 'Mark as moved out', kind: 'quiet', to: 'moved_out' }
    : { label: `Move ${firstNm} back in`, kind: 'quiet', to: 'approved' };
  const rawSecond = st === 'removed'
    ? { label: 'Give sign-in back (moved out)', kind: 'quiet', to: 'moved_out' }
    : { label: 'Revoke sign-in entirely', kind: 'danger', to: 'removed' };
  const actionsHtml = [rawFirst, rawSecond].map((a) => {
    const blocked = (isSelf || isLastAdmin) && a.to !== 'approved';
    const label = blocked ? (isSelf ? 'You can’t change your own access' : 'The last admin keeps access') : a.label;
    const cls = blocked ? 'access-action-btn access-action-btn--locked' : a.kind === 'danger' ? 'access-action-btn access-action-btn--danger' : 'access-action-btn';
    return `<button class="${cls}" data-action="member.runAccess" data-to="${a.to}" data-blocked="${blocked ? '1' : ''}">${escapeHtml(label)}</button>`;
  }).join('');

  const accessLabel = st === 'approved' ? 'In the flat' : st === 'moved_out' ? 'Moved out' : 'No access';
  const accessNote = st === 'approved'
    ? 'Moved out keeps their sign-in: they can see the balance, look back through history and settle up, but not log new expenses or be tagged on them. Revoking takes sign-in away entirely — the records stay either way.'
    : st === 'moved_out'
      ? 'They can still sign in to settle what’s outstanding. Nothing they logged was undone.'
      : 'No sign-in. Their expenses, shares and balance are all still in the books.';

  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="member-sheet-head">
        <div class="avatar avatar-38 ${avatarClass(selected.id, selected.id === state.me.id)}">${initials(selected.name)}</div>
        <div style="flex:1;min-width:0">
          <div class="member-sheet-title">${escapeHtml(selected.name)}</div>
          <div class="member-sheet-email">${escapeHtml(selected.email)}</div>
        </div>
        <button class="sheet-cancel" data-action="sheet.close">Done</button>
      </div>

      <div class="detail-row"><div class="detail-label">Standing</div><div class="detail-value tabular">${escapeHtml(standingLabel(selected.id))}</div></div>

      ${isActive ? `
        <div class="detail-row">
          <div class="detail-label">Role</div>
          <div class="role-options-row">${roleOptionsHtml}</div>
        </div>
        <div class="detail-note role-note">${escapeHtml(roleNote)}</div>
      ` : ''}

      <div class="detail-row"><div class="detail-label">Access</div><div class="detail-value">${escapeHtml(accessLabel)}</div></div>
      <div class="access-actions">${actionsHtml}</div>
      <div class="detail-note" style="margin-top:12px">${escapeHtml(accessNote)}</div>
    </div>
  `;
}

function renderInviteSheet() {
  const f = state.inviteForm;
  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head">
        <div class="sheet-title">Add someone</div>
        <button class="sheet-cancel" data-action="sheet.close">Cancel</button>
      </div>

      <input class="underline-input" style="margin-top:18px" data-field="invite.name" value="${escapeHtml(f.name)}" placeholder="Name" />
      <input class="underline-input" style="margin-top:14px" type="email" data-field="invite.email" value="${escapeHtml(f.email)}" placeholder="Email" />

      <div class="invite-role-row">
        <div class="detail-label">Joins as</div>
        <div class="role-options-row">
          ${['member', 'admin'].map((r) => `<button class="role-option-btn ${f.role === r ? 'role-option-btn--on' : ''}" data-action="invite.pickRole" data-role="${r}">${r === 'admin' ? 'Admin' : 'Member'}</button>`).join('')}
        </div>
      </div>

      <button class="btn-primary" style="margin-top:22px" data-action="invite.send" ${state.inviteSaving ? 'disabled' : ''}>${state.inviteSaving ? 'Creating…' : 'Copy invite link'}</button>
      <div class="detail-note" style="margin-top:12px">They’ll appear under Requests as invited, already approved the moment they set a password.</div>
    </div>
  `;
}

function renderRenameSheet() {
  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head">
        <div class="sheet-title">Household name</div>
        <button class="sheet-cancel" data-action="sheet.close">Cancel</button>
      </div>
      <input class="underline-input underline-input--serif" style="margin-top:18px" data-field="rename.draft" value="${escapeHtml(state.renameDraft)}" />
      <button class="btn-primary" style="margin-top:22px" data-action="household.saveRename" ${state.renameSaving ? 'disabled' : ''}>${state.renameSaving ? 'Saving…' : 'Save'}</button>
    </div>
  `;
}

function renderAcceptInvite() {
  const ai = state.acceptInvite || {};
  if (!ai.loaded) {
    return `
      <div class="screen screen--auth">
        <div class="brand">Halves</div>
        ${ai.error
          ? `<div class="error-row" style="margin-top:20px"><div class="error-dot"></div><div class="error-text">${escapeHtml(ai.error)}</div></div>`
          : `<div class="tagline">Loading your invite…</div>`}
        <div class="link-row"><button data-action="acceptInvite.toLogin">Back to sign in</button></div>
      </div>
    `;
  }
  return `
    <div class="screen screen--auth">
      <div class="brand">Halves</div>
      <div class="tagline">${escapeHtml(ai.name)}, set a password to join ${escapeHtml(ai.householdName)}.</div>
      <div class="form-stack">
        <div class="field">
          <label>Password</label>
          <input type="password" data-field="acceptInvite.password" value="${escapeHtml(ai.password)}" placeholder="At least 8 characters" />
        </div>
      </div>
      ${ai.error ? `<div class="error-row"><div class="error-dot"></div><div class="error-text">${escapeHtml(ai.error)}</div></div>` : ''}
      <button class="btn-primary" style="margin-top:26px" data-action="acceptInvite.submit" ${ai.loading ? 'disabled' : ''}>${ai.loading ? 'Joining…' : 'Join ' + escapeHtml(ai.householdName)}</button>
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
    case 'accept-invite': html = renderAcceptInvite(); break;
    default: html = renderLoading();
  }
  if (state.sheet === 'add') html += renderAddSheet();
  if (state.sheet === 'editShares') html += renderEditSharesSheet();
  if (state.sheet === 'settle') html += renderSettleSheet();
  if (state.sheet === 'menu') html += renderMenuSheet();
  if (state.sheet === 'member') html += renderMemberSheet();
  if (state.sheet === 'invite') html += renderInviteSheet();
  if (state.sheet === 'rename') html += renderRenameSheet();
  if (state.toast) html += `<div class="toast">${escapeHtml(state.toast)}</div>`;
  return html;
}

let lastRenderedSheet = null;

function render() {
  const app = document.getElementById('app');
  const active = document.activeElement;
  const activeField = active && active.matches && active.matches('[data-field]') ? active.dataset.field : null;
  const selStart = active && typeof active.selectionStart === 'number' ? active.selectionStart : null;
  const selEnd = active && typeof active.selectionEnd === 'number' ? active.selectionEnd : null;

  const isNewSheet = state.sheet !== lastRenderedSheet;
  lastRenderedSheet = state.sheet;

  app.innerHTML = buildHtml();

  if (!isNewSheet) {
    const sheetPanel = app.querySelector('.sheet-panel');
    const sheetOverlay = app.querySelector('.sheet-overlay');
    if (sheetPanel) sheetPanel.style.animation = 'none';
    if (sheetOverlay) sheetOverlay.style.animation = 'none';
  }

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
    case 'invite.name': state.inviteForm.name = value; break;
    case 'invite.email': state.inviteForm.email = value; break;
    case 'rename.draft': state.renameDraft = value; break;
    case 'acceptInvite.password': state.acceptInvite.password = value; state.acceptInvite.error = null; break;
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
    case 'household.decline': return declineRequest(Number(el.dataset.userId));
    case 'household.openRename': return openRenameSheet();
    case 'household.saveRename': return saveRename();
    case 'household.openInvite': return openInviteSheet();
    case 'invite.pickRole': state.inviteForm.role = el.dataset.role; return render();
    case 'invite.send': return sendInvite();
    case 'member.open': return openMemberSheet(Number(el.dataset.userId));
    case 'member.pickRole': return pickMemberRole(el.dataset.role);
    case 'member.runAccess': return runAccessAction(el.dataset.to, el.dataset.blocked === '1');
    case 'acceptInvite.submit': return submitAcceptInvite();
    case 'acceptInvite.toLogin':
      history.replaceState(null, '', location.pathname);
      state.route = 'login';
      return render();
    case 'draft.pickCategory': state.draft.category = el.dataset.cat; return render();
    case 'draft.togglePerson': return toggleDraftPerson(Number(el.dataset.userId));
    case 'draft.cyclePayer': return cyclePayer();
    case 'draft.save': return saveExpense();
    case 'history.editShares': return openEditSharesSheet(Number(el.dataset.expenseId));
    case 'shares.togglePerson': return toggleShareParticipant(Number(el.dataset.userId));
    case 'shares.inc': return bumpShare(Number(el.dataset.userId), 1);
    case 'shares.dec': return bumpShare(Number(el.dataset.userId), -1);
    case 'shares.save': return saveShares();
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
    if (el.dataset.field === 'acceptInvite.password') submitAcceptInvite();
  });
}

// ---------- boot ----------

initEvents();
render();
boot();
