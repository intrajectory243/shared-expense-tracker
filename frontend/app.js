'use strict';

const TOKEN_KEY = 'halves_token';
const EPS = 0.005;
const PALETTE = ['avatar--sage', 'avatar--amber', 'avatar--blue'];
const NUMBER_WORDS = ['Zero', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];

const LOCALE_TAGS = {
  en: 'en-US',
  fa: 'fa-IR-u-ca-persian-nu-arabext', // Jalali calendar + Persian digits, in one locale tag
};

const CURRENCY_META = {
  toman: { mode: 'suffix', labelKey: 'currency.toman', nameKey: 'currency.name.toman' },
  rial: { mode: 'suffix', labelKey: 'currency.rial', nameKey: 'currency.name.rial' },
  usd: { mode: 'prefix', symbol: '$', nameKey: 'currency.name.usd' },
  eur: { mode: 'prefix', symbol: '€', nameKey: 'currency.name.eur' },
  aed: { mode: 'suffix', labelKey: 'currency.aed', nameKey: 'currency.name.aed' },
};
const CURRENCY_ORDER = ['toman', 'rial', 'usd', 'eur', 'aed'];

// Own name in each language + a neutral English descriptor that stays
// English regardless of UI language (matches the design mockup's picker).
const LANG_OPTIONS = [
  { code: 'en', native: 'English', descKey: 'lang.englishDesc', font: "'Instrument Sans',system-ui" },
  { code: 'fa', native: 'فارسی', descKey: 'lang.persianDesc', font: "'Vazirmatn',sans-serif" },
];

// 'Settled' is a synthetic history-row tag (not a real category -- see
// history grouping below), the one thing here still worth translating.
// Real categories are household-owned data now (roadmap Phase 9): seeded
// once in whoever's request first reads them, then just editable strings
// -- translating them centrally after that would fight a household that
// deliberately renamed one, so catLabel() below leaves them exactly as
// the household spelled them.
const CAT_LABELS = {
  en: { Settled: 'Settled' },
  fa: { Settled: 'تسویه' },
};

function defaultSignupForm() {
  return { name: '', email: '', password: '', mode: 'create', householdName: '', householdId: '', currency: 'toman', households: [], error: null, loading: false };
}

// Device guess before login; the account's own saved preference (synced via
// syncLangFromMe) always wins once we know who's signed in.
function initLang() {
  const saved = localStorage.getItem('halves_lang');
  if (saved === 'en' || saved === 'fa') return saved;
  const guess = (navigator.language || 'en').slice(0, 2).toLowerCase() === 'fa' ? 'fa' : 'en';
  localStorage.setItem('halves_lang', guess);
  return guess;
}

function syncLangFromMe() {
  state.lang = state.me.language;
  localStorage.setItem('halves_lang', state.lang);
}

const state = {
  lang: initLang(),
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
  draft: { amount: '', desc: '', category: null, participantIds: [], payerId: null, date: '' },
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
  // Trash (admin only): soft-deleted expenses/settlements, kept for a grace
  // window so a mistaken delete can be undone. Populated in refreshData.
  deletedExpenses: [],
  deletedSettlements: [],
  trashOpen: false,
  // Spending breakdown screen: a day-accurate date range + which person row
  // is expanded. Range is set the first time the screen is opened.
  breakdownFrom: null,
  breakdownTo: null,
  breakdownPerson: null,
  pushEnabled: false,
  draftCurrency: 'toman',
  currencySaving: false,
  // Roadmap Phase 8 UI (backup/restore).
  backupFile: null,       // null | 'ok' | 'rejected' -- a picked file's state (see doRestore)
  backupFileHandle: null, // the real File object once picked
  holdPct: 0,
  lastCopy: null,         // display string, e.g. "just now · 96 KB" -- session-only, not persisted
  restoreResult: null,    // { expenses_restored, settlements_restored, unclaimed_users_created }
  // Roadmap Phase 9 (editable categories).
  catList: [],            // [{ id, name, usage }], server-owned -- see app/routers/categories.py
};

// ---------- helpers ----------

function fmt(n) {
  return Math.round(n || 0).toLocaleString(LOCALE_TAGS[state.lang] || 'en-US');
}

function money(n) {
  const cur = CURRENCY_META[state.household ? state.household.currency : 'toman'];
  const amt = fmt(n); // identical digits regardless of currency choice -- no conversion, ever
  return cur.symbol ? `${cur.symbol}${amt}` : `${amt} ${t(cur.labelKey)}`;
}

// For two-part layouts (a big amount plus a smaller trailing unit label).
// Prefix currencies (symbol) fold into the amount itself and leave no
// separate unit; suffix currencies (toman/rial/AED) split as before.
function moneyParts(n) {
  const cur = CURRENCY_META[state.household ? state.household.currency : 'toman'];
  const amt = fmt(n);
  return cur.symbol ? { amount: `${cur.symbol}${amt}`, unit: '' } : { amount: amt, unit: t(cur.labelKey) };
}

function currencyUnitLabel() {
  const cur = CURRENCY_META[state.household ? state.household.currency : 'toman'];
  return cur.symbol || t(cur.labelKey);
}

// Sample formatting for a currency, independent of the household's actual
// currency -- used by the currency picker to preview a choice before saving.
function currencyPreviewText(code) {
  const cur = CURRENCY_META[code];
  const sample = code === 'rial' ? 12345670 : code === 'toman' ? 1234567 : 1235;
  const amt = Math.round(sample).toLocaleString(LOCALE_TAGS[state.lang] || 'en-US');
  return cur.symbol ? `${cur.symbol}${amt}` : `${amt} ${t(cur.labelKey)}`;
}

function catLabel(cat) {
  return (CAT_LABELS[state.lang] || {})[cat] || cat;
}

// Persian (U+06F0-U+06F9) and Arabic-Indic (U+0660-U+0669) digits -> ASCII.
// Needed because fa formatting (fmt) emits Persian digits and a Persian
// keyboard types them, and parseAmount below only keeps [0-9] -- without
// this, every keystroke in an amount field discards the digits already
// there and the field can never hold more than the one just typed.
function toLatinDigits(str) {
  return String(str ?? '')
    .replace(/[۰-۹]/g, (d) => d.charCodeAt(0) - 0x06f0)
    .replace(/[٠-٩]/g, (d) => d.charCodeAt(0) - 0x0660);
}

function parseAmount(str) {
  const digits = toLatinDigits(str).replace(/[^0-9]/g, '');
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
  // userId is a uuid string, not a number -- sum char codes for a stable pick.
  let hash = 0;
  for (const ch of String(userId)) hash = (hash + ch.charCodeAt(0)) | 0;
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

function householdLabel(count) {
  if (count <= 1) return t('home.justYou');
  if (state.lang !== 'en') return t('home.membersCount', { n: fmt(count) });
  const word = NUMBER_WORDS[count] || String(count);
  return `${word} of us`;
}

function nameOf(id) {
  const u = state.users.find((x) => x.id === id) || state.former.find((x) => x.id === id);
  return u ? u.name : t('common.someone');
}

// state.users includes unclaimed stubs (roadmap Phase 8 restore) so their
// name/balance still render correctly wherever a member is looked up by
// id -- but they can't be tagged, paid, or shown as an active participant,
// so anywhere that builds a list of *actionable* people needs this instead.
function activeMembers() {
  return state.users.filter((u) => u.status !== 'unclaimed');
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
  if (Math.abs(n) <= EPS) return t('standing.square');
  return t(n > 0 ? 'standing.owed' : 'standing.owes', { amount: money(n > 0 ? n : -n) });
}

function formerStandingShort(m) {
  const n = netOf(m.id);
  const tier = t(m.status === 'moved_out' ? 'formerTier.movedOut' : 'formerTier.noAccess');
  const bal = Math.abs(n) <= EPS ? t('formerStanding.square') : t(n > 0 ? 'formerStanding.owed' : 'formerStanding.owes', { amount: fmt(n > 0 ? n : -n) });
  return `${tier} · ${bal}`;
}

function monthLabel(isoDate) {
  const dt = new Date(isoDate + 'T00:00:00');
  return dt.toLocaleString(LOCALE_TAGS[state.lang] || 'en-US', { month: 'long', year: 'numeric' });
}

function dayOfMonth(isoDate) {
  return isoDate.slice(8, 10);
}

// Local calendar day as YYYY-MM-DD (not toISOString, which is UTC and can
// land on the wrong day). This is what the date picker defaults to and
// what we send, so the expense date is the user's "today", not the
// server's.
function localISO(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function todayISO() {
  return localISO(new Date());
}

function fmtDate(isoDate) {
  return new Date(isoDate + 'T00:00:00').toLocaleDateString(LOCALE_TAGS[state.lang] || 'en-US', { day: 'numeric', month: 'short', year: 'numeric' });
}

// Full timestamp of when a record was actually entered (created_at). ISO
// string from the API is UTC; toLocaleString renders it in the reader's
// zone and calendar (Jalali + Persian digits for fa).
function fmtDateTime(iso) {
  return new Date(iso).toLocaleString(LOCALE_TAGS[state.lang] || 'en-US', { dateStyle: 'medium', timeStyle: 'short' });
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
    syncLangFromMe();
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
    state.acceptInvite.error = e.message || t('error.invalidInvite');
  }
  render();
}

async function afterAuth() {
  // Only truly-pending sign-ups get parked; moved_out still gets full home
  // access (read + settle), just restricted from logging new expenses.
  if (state.me.status === 'pending') { state.route = 'pending'; state.noHousehold = false; return render(); }
  if (!state.me.household_id) { state.route = 'pending'; state.noHousehold = true; return render(); }
  await loadHome();
  refreshPushState();
}

async function doLogin() {
  const { email, password } = state.loginForm;
  if (!email || !password) { state.loginForm.error = t('error.enterEmailPassword'); return render(); }
  state.loginForm.loading = true;
  state.loginForm.error = null;
  render();
  try {
    const tok = await api('/auth/login', { method: 'POST', auth: false, form: { username: email, password } });
    state.token = tok.access_token;
    localStorage.setItem(TOKEN_KEY, state.token);
    state.me = await api('/auth/me');
    syncLangFromMe();
    state.loginForm = { email: '', password: '', error: null, loading: false };
    await afterAuth();
  } catch (e) {
    state.loginForm.loading = false;
    state.loginForm.error = e.message || t('error.couldNotSignIn');
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
  if (!f.name || !f.email || !f.password) { f.error = t('error.fillNameEmailPassword'); return render(); }
  if (f.password.length < 8) { f.error = t('error.passwordMin8'); return render(); }
  if (f.mode === 'create' && !f.householdName) { f.error = t('error.nameHousehold'); return render(); }
  if (f.mode === 'join' && !f.householdId) { f.error = t('error.pickHousehold'); return render(); }
  f.loading = true;
  f.error = null;
  render();
  try {
    const payload = { name: f.name, email: f.email, password: f.password, language: state.lang };
    if (f.mode === 'create') { payload.household_name = f.householdName; payload.household_currency = f.currency; }
    else payload.household_id = Number(f.householdId);
    await api('/auth/signup', { method: 'POST', auth: false, body: payload });
    const tok = await api('/auth/login', { method: 'POST', auth: false, form: { username: f.email, password: f.password } });
    state.token = tok.access_token;
    localStorage.setItem(TOKEN_KEY, state.token);
    state.me = await api('/auth/me');
    syncLangFromMe();
    state.signupForm = defaultSignupForm();
    await afterAuth();
  } catch (e) {
    f.loading = false;
    f.error = e.message || t('error.couldNotCreateAccount');
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
    inviteForm: { name: '', email: '', role: 'member' }, toast: null, pushEnabled: false,
  });
  render();
}

// ---------- push notifications ----------

function pushSupported() {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

function urlBase64ToUint8Array(base64) {
  const padding = '='.repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'));
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

async function refreshPushState() {
  if (!pushSupported()) return;
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    state.pushEnabled = !!sub;
    render();
  } catch (e) { /* best-effort status check, safe to skip on failure */ }
}

async function toggleNotifications() {
  if (!pushSupported()) {
    flash(t('toast.notificationsUnsupported'));
    return;
  }
  try {
    const reg = await navigator.serviceWorker.ready;
    const existing = await reg.pushManager.getSubscription();

    if (existing) {
      await existing.unsubscribe();
      await api('/push/unsubscribe', { method: 'POST', body: { endpoint: existing.endpoint } });
      state.pushEnabled = false;
      flash(t('toast.notificationsOff'));
      return render();
    }

    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      flash(t(permission === 'denied' ? 'toast.notificationsBlocked' : 'toast.notificationsNeedPermission'));
      return render();
    }

    const { public_key } = await api('/push/vapid-public-key');
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(public_key),
    });
    await api('/push/subscribe', { method: 'POST', body: sub.toJSON() });
    state.pushEnabled = true;
    flash(t('toast.notificationsOn'));
  } catch (e) {
    flash(e.message || t('toast.couldNotUpdateNotifications'));
  }
  render();
}

// ---------- data loading ----------

async function refreshData() {
  const isAdmin = state.me.role === 'admin';
  const calls = [api('/users'), api('/expenses'), api('/balances'), api('/settlements'), api('/households', { auth: false }), api('/categories')];
  if (isAdmin) {
    calls.push(
      api('/users/pending'),
      api('/users/former'),
      api('/expenses?include_deleted=true'),
      api('/settlements?include_deleted=true'),
    );
  }
  const results = await Promise.all(calls);
  state.users = results[0];
  state.expenses = results[1];
  state.balance = results[2];
  state.settlements = results[3];
  state.household = results[4].find((h) => h.id === state.me.household_id) || null;
  state.catList = results[5];
  state.pending = isAdmin ? results[6] : [];
  state.former = isAdmin ? results[7] : [];
  state.deletedExpenses = isAdmin ? results[8].filter((e) => e.deleted_at) : [];
  state.deletedSettlements = isAdmin ? results[9].filter((s) => s.deleted_at) : [];
  if (!state.deletedExpenses.length && !state.deletedSettlements.length) state.trashOpen = false;
}

async function loadHome() {
  try {
    await refreshData();
    state.route = 'home';
  } catch (e) {
    flash(e.message || t('toast.couldNotLoadHousehold'));
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
    flash(e.message || t('toast.couldNotRefreshHousehold'));
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
    const name = person ? person.name.split(' ')[0] : t('common.someone');
    flash(t(role === 'admin' ? 'toast.approvedAsAdmin' : 'toast.approvedAsMember', { name }));
  } catch (e) {
    flash(e.message || t('toast.couldNotApprove'));
  }
  render();
}

async function declineRequest(userId) {
  const person = state.pending.find((p) => p.id === userId);
  try {
    await api(`/users/${userId}`, { method: 'DELETE' });
    await refreshData();
    flash(person ? t('toast.declined', { name: firstName(person.name) }) : t('toast.declinedGeneric'));
  } catch (e) {
    flash(e.message || t('toast.couldNotDecline'));
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

function copySignupLink() {
  // No special token needed -- the "claim" is just uuid5(email) landing on
  // the same id the restore already created (see app/routers/auth.py's
  // signup claim branch). Any signup link works; this is the one on hand.
  const link = location.origin + '/';
  state.sheet = null;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(link).catch(() => {});
  }
  flash(t('memberSheet.signupLinkCopiedToast'));
  render();
}

async function pickMemberRole(role) {
  const selected = findMember(state.selectedMemberId);
  if (!selected || selected.role === role) return;
  try {
    await api(`/users/${selected.id}`, { method: 'PATCH', body: { role } });
    await refreshData();
    flash(t(role === 'admin' ? 'toast.roleChangedAdmin' : 'toast.roleChangedMember', { name: firstName(selected.name) }));
  } catch (e) {
    flash(e.message || t('toast.couldNotChangeRole'));
  }
  render();
}

async function runAccessAction(to, blocked) {
  const selected = findMember(state.selectedMemberId);
  if (!selected) return;
  if (blocked) {
    flash(t(selected.id === state.me.id ? 'toast.askAnotherAdmin' : 'toast.needOneAdmin'));
    return;
  }
  try {
    await api(`/users/${selected.id}`, { method: 'PATCH', body: { status: to } });
    await refreshData();
    const who = firstName(selected.name);
    state.sheet = null;
    flash(t(to === 'approved' ? 'toast.backIn' : to === 'moved_out' ? 'toast.movedOutHistory' : 'toast.noLongerSignIn', { name: who }));
  } catch (e) {
    flash(e.message || t('toast.couldNotUpdateAccess'));
  }
  render();
}

// ---------- language + currency ----------

function pickLanguage(code) {
  state.sheet = null;
  state.lang = code;
  localStorage.setItem('halves_lang', code);
  render();
  flash(t(code === 'en' ? 'toast.langEn' : 'toast.langFa'));
  if (state.me) api('/users/me/language', { method: 'PATCH', body: { language: code } }).catch(() => {});
}

function openCurrencySheet() {
  state.draftCurrency = state.household ? state.household.currency : 'toman';
  state.currencySaving = false;
  state.sheet = 'currency';
  render();
}

async function saveCurrency() {
  const code = state.draftCurrency;
  state.currencySaving = true;
  render();
  try {
    await api(`/households/${state.me.household_id}`, { method: 'PATCH', body: { currency: code } });
    await refreshData();
    state.sheet = null;
    state.currencySaving = false;
    flash(t('toast.currencySet', { name: t(CURRENCY_META[code].nameKey) }));
  } catch (e) {
    state.currencySaving = false;
    flash(e.message || t('toast.couldNotUpdateCurrency'));
  }
  render();
}

// ---------- backup / restore (roadmap Phase 8) ----------

let holdTimer = null;

function openBackupSheet() {
  state.backupFile = null;
  state.backupFileHandle = null;
  state.holdPct = 0;
  state.sheet = 'backup';
  render();
}

function pickBackupFile() {
  const input = document.getElementById('backupFileInput');
  if (input) input.click();
}

function onBackupFileChosen(file) {
  if (!file) return;
  state.backupFileHandle = file;
  state.backupFile = 'ok';
  state.holdPct = 0;
  render();
}

// Filename the backend suggests via Content-Disposition -- falls back to a
// generic name only if that header is somehow missing.
function filenameFromContentDisposition(header) {
  const match = /filename="?([^";]+)"?/.exec(header || '');
  return match ? match[1] : 'halves-backup.db';
}

async function exportBackup() {
  try {
    const res = await fetch(`/households/${state.me.household_id}/export`, {
      headers: { Authorization: 'Bearer ' + state.token },
    });
    if (!res.ok) throw new Error(t('toast.couldNotExport'));
    const blob = await res.blob();
    const filename = filenameFromContentDisposition(res.headers.get('content-disposition'));
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    state.lastCopy = t('backup.justNow', { size: Math.max(1, Math.round(blob.size / 1024)) });
    flash(t('backup.savedToast', { filename }));
    render();
  } catch (e) {
    flash(e.message || t('toast.couldNotExport'));
  }
}

function startHold() {
  if (holdTimer || state.backupFile !== 'ok') return;
  holdTimer = setInterval(() => {
    const p = (state.holdPct || 0) + 8;
    if (p >= 100) {
      clearInterval(holdTimer);
      holdTimer = null;
      state.holdPct = 100;
      render();
      doRestore();
    } else {
      state.holdPct = p;
      render();
    }
  }, 45);
}

function endHold() {
  clearInterval(holdTimer);
  holdTimer = null;
  if (state.holdPct && state.holdPct < 100) {
    state.holdPct = 0;
    render();
  }
}

async function doRestore() {
  const form = new FormData();
  form.append('file', state.backupFileHandle);
  try {
    const res = await fetch(`/households/${state.me.household_id}/restore`, {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + state.token },
      body: form,
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      // Not shown to the user (the card always renders a fixed, localized
      // explanation, not the backend's raw English detail text) -- but the
      // 400 branch is real and distinct from a network failure below.
      state.backupFile = 'rejected';
      state.holdPct = 0;
      render();
      return;
    }
    state.restoreResult = data;
    state.sheet = null;
    state.backupFile = null;
    state.backupFileHandle = null;
    state.holdPct = 0;
    state.route = 'restored';
    render();
    await refreshData();
    render();
  } catch (e) {
    state.backupFile = 'rejected';
    state.holdPct = 0;
    render();
  }
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
  if (!name) return flash(t('toast.giveHouseholdName'));
  state.renameSaving = true;
  render();
  try {
    await api(`/households/${state.me.household_id}`, { method: 'PATCH', body: { name } });
    await refreshData();
    state.sheet = null;
    state.renameSaving = false;
    flash(t('toast.renamed', { name }));
  } catch (e) {
    state.renameSaving = false;
    flash(e.message || t('toast.couldNotRename'));
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
  if (!name || !email) return flash(t('toast.nameAndEmail'));
  state.inviteSaving = true;
  render();
  try {
    const res = await api('/users/invite', { method: 'POST', body: { name, email, role: f.role } });
    await refreshData();
    state.sheet = null;
    state.inviteSaving = false;
    const link = `${location.origin}${location.pathname}?invite=${res.invite_token}`;
    try { await navigator.clipboard.writeText(link); } catch (e) { /* clipboard may be unavailable; link is still valid to share manually */ }
    flash(t('toast.invited', { name: firstName(name) }));
  } catch (e) {
    state.inviteSaving = false;
    flash(e.message || t('toast.couldNotInvite'));
    render();
  }
}

// ---------- accept invite ----------

async function submitAcceptInvite() {
  const ai = state.acceptInvite;
  if (!ai.password || ai.password.length < 8) { ai.error = t('error.passwordMin8'); return render(); }
  ai.loading = true;
  ai.error = null;
  render();
  try {
    await api('/auth/accept-invite', { method: 'POST', auth: false, body: { token: ai.token, password: ai.password } });
    const tok = await api('/auth/login', { method: 'POST', auth: false, form: { username: ai.email, password: ai.password } });
    state.token = tok.access_token;
    localStorage.setItem(TOKEN_KEY, state.token);
    state.me = await api('/auth/me');
    syncLangFromMe();
    history.replaceState(null, '', location.pathname);
    await afterAuth();
  } catch (e) {
    ai.loading = false;
    ai.error = e.message || t('error.couldNotJoin');
    render();
  }
}

// ---------- add expense ----------

function openAddSheet() {
  state.draft = {
    amount: '', desc: '', category: null,
    participantIds: activeMembers().map((u) => u.id),
    payerId: state.me.id,
    date: todayISO(),
  };
  state.sheet = 'add';
  render();
}

function toggleDraftPerson(id) {
  const ids = state.draft.participantIds;
  state.draft.participantIds = ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
  render();
}

// The visible date pill is a plain button; the real <input type="date"> is
// kept off-screen (styling a date input to fill a button is unreliable
// across browsers, especially Safari). showPicker() opens the native
// picker from the button's click gesture; .click() is the fallback for
// anything without showPicker. The button carries data-picker-target with
// the id of its hidden input (one screen can have several).
function openDatePicker(btn) {
  const id = btn && btn.dataset ? btn.dataset.pickerTarget : null;
  const input = id ? document.getElementById(id) : document.querySelector('.date-hidden-input');
  if (!input) return;
  try {
    if (typeof input.showPicker === 'function') input.showPicker();
    else input.click();
  } catch (e) {
    input.click();
  }
}

function cyclePayer() {
  const ids = activeMembers().map((u) => u.id);
  if (!ids.length) return;
  const idx = ids.indexOf(state.draft.payerId);
  state.draft.payerId = ids[(idx + 1) % ids.length];
  render();
}

async function saveExpense() {
  const d = state.draft;
  const amount = parseAmount(d.amount);
  if (!amount) return flash(t('toast.enterAmount'));
  if (!d.participantIds.length) return flash(t('toast.tagAtLeastOne'));
  state.draftSaving = true;
  render();
  try {
    await api('/expenses', {
      method: 'POST',
      body: {
        amount,
        description: d.desc || d.category || t('addSheet.fallbackDesc'),
        category: d.category || (state.catList[0] ? state.catList[0].name : 'Other'),
        date: d.date || todayISO(),
        participant_ids: d.participantIds,
        payer_id: d.payerId,
      },
    });
    state.sheet = null;
    state.draftSaving = false;
    await refreshData();
    flash(t('toast.savedExpense', { amount: money(amount) }));
  } catch (e) {
    state.draftSaving = false;
    flash(e.message || t('toast.couldNotSaveExpense'));
    render();
  }
}

// ---------- categories (roadmap Phase 9) ----------

function openCategoriesSheet() {
  state.sheet = 'categories';
  render();
}

function closeCategoriesSheet() {
  // Reopens the Add-expense sheet, not just closes -- that's the only
  // entry point (the "edit" chip in its category row), and the draft it
  // was already filling out stays intact underneath.
  state.sheet = 'add';
  render();
}

async function renameCategory(id, name) {
  const trimmed = (name || '').trim();
  if (!trimmed) return flash(t('toast.catNameFirst'));
  const current = state.catList.find((c) => c.id === id);
  if (current && current.name === trimmed) return;
  try {
    await api(`/categories/${id}`, { method: 'PATCH', body: { name: trimmed } });
    if (state.draft.category === (current && current.name)) state.draft.category = trimmed;
    await refreshData();
  } catch (e) {
    flash(e.message || t('toast.couldNotSaveCategory'));
  }
  render();
}

async function addCategory(rawName) {
  const name = (rawName || '').trim();
  if (!name) return flash(t('toast.catNameFirst'));
  if (state.catList.some((c) => c.name.toLowerCase() === name.toLowerCase())) {
    return flash(t('toast.catAlreadyExists'));
  }
  try {
    await api('/categories', { method: 'POST', body: { name } });
    state.draft.category = name;
    await refreshData();
    flash(t('toast.catAdded', { name }));
  } catch (e) {
    flash(e.message || t('toast.couldNotSaveCategory'));
    render();
  }
}

async function removeCategory(id) {
  const category = state.catList.find((c) => c.id === id);
  if (!category) return;
  if (category.usage > 0) {
    return flash(t(category.usage === 1 ? 'toast.catStillOnOne' : 'toast.catStillOnMany', { name: category.name, n: fmt(category.usage) }));
  }
  if (state.catList.length <= 1) return flash(t('toast.catKeepAtLeastOne'));
  try {
    await api(`/categories/${id}`, { method: 'DELETE' });
    await refreshData();
    flash(t('toast.catRemoved', { name: category.name }));
  } catch (e) {
    flash(e.message || t('toast.couldNotRemoveCategory'));
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
  if (!entries.length) return flash(t('toast.tagAtLeastOne'));
  state.editSharesSaving = true;
  render();
  try {
    await api(`/expenses/${es.expenseId}/shares`, {
      method: 'PATCH',
      body: { participants: entries.map(([userId, share]) => ({ user_id: userId, share })) },
    });
    state.sheet = null;
    state.editSharesSaving = false;
    await refreshData();
    flash(t('toast.splitUpdated'));
  } catch (e) {
    state.editSharesSaving = false;
    flash(e.message || t('toast.couldNotUpdateSplit'));
    render();
  }
}

async function deleteExpenseFromSheet() {
  const es = state.editShares;
  const expense = state.expenses.find((e) => e.id === es.expenseId);
  state.editSharesSaving = true;
  render();
  try {
    await api(`/expenses/${es.expenseId}`, { method: 'DELETE' });
    state.sheet = null;
    state.editSharesSaving = false;
    await refreshData();
    flash(t('toast.expenseDeleted', { desc: expense ? expense.description : '' }));
  } catch (e) {
    state.editSharesSaving = false;
    flash(e.message || t('toast.couldNotDeleteExpense'));
    render();
  }
}

async function restoreExpense(id) {
  try {
    await api(`/expenses/${id}/restore`, { method: 'POST' });
    await refreshData();
    flash(t('toast.expenseRestored'));
  } catch (e) {
    flash(e.message || t('toast.couldNotRestore'));
    render();
  }
}

async function deleteSettlement(id) {
  if (!window.confirm(t('trash.confirmSettlement'))) return;
  try {
    await api(`/settlements/${id}`, { method: 'DELETE' });
    await refreshData();
    flash(t('toast.settlementDeleted'));
  } catch (e) {
    flash(e.message || t('toast.couldNotDeleteSettlement'));
    render();
  }
}

async function restoreSettlement(id) {
  try {
    await api(`/settlements/${id}/restore`, { method: 'POST' });
    await refreshData();
    flash(t('toast.settlementRestored'));
  } catch (e) {
    flash(e.message || t('toast.couldNotRestore'));
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
  const target = getSettleTarget();
  const amount = parseAmount(state.settleDraft.amount);
  if (!target || !amount) return flash(t('toast.pickPersonAmount'));
  state.settleSaving = true;
  render();
  try {
    const payload = target.iOwe
      ? { from_user_id: state.me.id, to_user_id: target.debt.to_user_id, amount }
      : { from_user_id: target.debt.from_user_id, to_user_id: state.me.id, amount };
    await api('/settlements', { method: 'POST', body: payload });
    state.sheet = null;
    state.settleSaving = false;
    await refreshData();
    flash(t('toast.logged', { amount: money(amount) }));
  } catch (e) {
    state.settleSaving = false;
    flash(e.message || t('toast.couldNotLogSettlement'));
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
      // Only surface when it was entered if that wasn't the same day as the
      // expense itself -- i.e. it was backdated. Same-day is the norm and
      // the day marker already says it.
      loggedAtLabel: localISO(new Date(e.created_at)) !== e.date
        ? t('history.loggedAt', { when: fmtDateTime(e.created_at) })
        : null,
      payerLabel: t('history.paidBy', { name: e.payer.name }),
      // Most expenses are logged by the payer themselves -- only call out
      // who actually entered it when that's not the case, to avoid clutter.
      loggedByLabel: e.created_by.id !== e.payer.id ? t('history.loggedBy', { name: e.created_by.name }) : null,
      parts: e.participants.map((p) => initials(p.name)),
      shareLabel: e.participants.length > 1 ? t('history.splitWays', { n: fmt(e.participants.length) }) : t('history.allName', { name: e.participants[0] ? e.participants[0].name : '' }),
      titleColor: 'var(--ink)', tagClass: 'tag',
    });
  }
  for (const s of state.settlements) {
    const fromName = nameOf(s.from_user_id);
    const toName = nameOf(s.to_user_id);
    rows.push({
      kind: 'settle', id: s.id, sortKey: s.date, month: monthLabel(s.date), day: dayOfMonth(s.date),
      desc: t('history.settlementDesc', { from: fromName, to: toName }), cat: 'Settled', amount: s.amount,
      payerLabel: t('history.repayment'),
      loggedByLabel: null,
      loggedAtLabel: s.created_at && localISO(new Date(s.created_at)) !== s.date
        ? t('history.loggedAt', { when: fmtDateTime(s.created_at) })
        : null,
      parts: [initials(fromName), initials(toName)],
      shareLabel: t('history.balanceReduced'),
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
    total: t('history.spent', { amount: fmt(items.filter((i) => i.kind === 'expense').reduce((a, b) => a + b.amount, 0)) }),
  }));
}

// ---------- spending breakdown ----------

// Every rollup here is client-side arithmetic over state.expenses, which
// refreshData() loads in full (GET /expenses is unbounded). If that
// endpoint ever gets paginated this must move to a server aggregation.

function firstOfThisMonthISO() {
  return todayISO().slice(0, 8) + '01';
}

function breakdownPresetRange(preset) {
  const today = todayISO();
  const p = (n) => String(n).padStart(2, '0');
  if (preset === 'lastMonth') {
    const [y, m] = today.split('-').map(Number);
    const lm = m === 1 ? 12 : m - 1;
    const ly = m === 1 ? y - 1 : y;
    const lastDay = new Date(ly, lm, 0).getDate();
    return { from: `${ly}-${p(lm)}-01`, to: `${ly}-${p(lm)}-${p(lastDay)}` };
  }
  if (preset === 'all') {
    const earliest = state.expenses.map((e) => e.date).sort()[0];
    return { from: earliest || today, to: today };
  }
  return { from: firstOfThisMonthISO(), to: today };
}

function buildBreakdown() {
  const { breakdownFrom: from, breakdownTo: to } = state;
  const inRange = state.expenses.filter((e) => e.date >= from && e.date <= to);

  const catMap = new Map();
  const personMap = new Map();
  let grand = 0;

  for (const e of inRange) {
    grand += e.amount;
    const c = catMap.get(e.category) || { total: 0, count: 0 };
    c.total += e.amount;
    c.count += 1;
    catMap.set(e.category, c);

    const totalWeight = e.shares.reduce((a, s) => a + s.share, 0) || 1;
    for (const s of e.shares) {
      const amt = (e.amount * s.share) / totalWeight;
      const name = (e.participants.find((pp) => pp.id === s.user_id) || {}).name || nameOf(s.user_id);
      const person = personMap.get(s.user_id) || { id: s.user_id, name, total: 0, byCat: new Map() };
      person.total += amt;
      person.byCat.set(e.category, (person.byCat.get(e.category) || 0) + amt);
      personMap.set(s.user_id, person);
    }
  }

  const byCategory = [...catMap.entries()]
    .map(([cat, v]) => ({ cat, total: v.total, count: v.count }))
    .sort((a, b) => b.total - a.total);

  const byPerson = [...personMap.values()]
    .map((pn) => ({
      id: pn.id,
      name: pn.name,
      total: pn.total,
      byCat: [...pn.byCat.entries()].map(([cat, total]) => ({ cat, total })).sort((a, b) => b.total - a.total),
    }))
    .sort((a, b) => b.total - a.total);

  return { from, to, grand, byCategory, byPerson, isEmpty: inRange.length === 0 };
}

// ---------- render: screens ----------

function renderLoading() {
  return `<div class="loading-screen">${t('common.loading')}</div>`;
}

function renderLogin() {
  const f = state.loginForm;
  return `
    <div class="screen screen--auth">
      <div class="brand">Halves</div>
      <div class="tagline">${t('login.tagline')}</div>
      <div class="chip-row" style="margin-top:22px">
        ${LANG_OPTIONS.map((l) => `<button class="chip ${state.lang === l.code ? 'chip--active' : ''}" style="font-family:${l.font}" data-action="login.pickLang" data-lang="${l.code}">${l.native}</button>`).join('')}
      </div>
      <div class="form-stack">
        <div class="field">
          <label>${t('login.labelEmail')}</label>
          <input type="text" inputmode="email" autocapitalize="off" spellcheck="false" dir="ltr" data-field="login.email" value="${escapeHtml(f.email)}" placeholder="you@example.com" autocomplete="username" />
        </div>
        <div class="field">
          <label>${t('login.labelPassword')}</label>
          <input type="password" data-field="login.password" value="${escapeHtml(f.password)}" placeholder="••••••••" autocomplete="current-password" />
        </div>
      </div>
      ${f.error ? `<div class="error-row"><div class="error-dot"></div><div class="error-text">${escapeHtml(f.error)}</div></div>` : ''}
      <button class="btn-primary" style="margin-top:26px" data-action="login.submit" ${f.loading ? 'disabled' : ''}>${f.loading ? t('login.signingIn') : t('login.signIn')}</button>
      <div class="link-row">
        <button data-action="signup.open">${t('login.createAccount')}</button>
      </div>
    </div>
  `;
}

function renderSignup() {
  const f = state.signupForm;
  const isCreate = f.mode === 'create';
  return `
    <div class="screen screen--auth">
      <div class="brand">Halves</div>
      <div class="tagline">${t(isCreate ? 'signup.taglineCreate' : 'signup.taglineJoin')}</div>
      <div class="form-stack">
        <div class="field"><label>${t('signup.labelName')}</label><input data-field="signup.name" value="${escapeHtml(f.name)}" placeholder="${t('signup.labelName')}" /></div>
        <div class="field"><label>${t('signup.labelEmail')}</label><input type="text" inputmode="email" autocapitalize="off" spellcheck="false" dir="ltr" data-field="signup.email" value="${escapeHtml(f.email)}" placeholder="you@example.com" /></div>
        <div class="field"><label>${t('signup.labelPassword')}</label><input type="password" data-field="signup.password" value="${escapeHtml(f.password)}" placeholder="${t('signup.passwordHint')}" /></div>
      </div>

      <div class="tabs-row">
        <button class="tab-btn ${isCreate ? 'tab-btn--active' : ''}" data-action="signup.modeCreate">${t('signup.tabCreate')}</button>
        <button class="tab-btn ${!isCreate ? 'tab-btn--active' : ''}" data-action="signup.modeJoin">${t('signup.tabJoin')}</button>
      </div>

      ${isCreate ? `
        <div class="field" style="margin-top:10px">
          <label>${t('signup.labelHouseholdName')}</label>
          <input data-field="signup.householdName" value="${escapeHtml(f.householdName)}" placeholder="${t('signup.labelHouseholdName')}" />
        </div>
      ` : `
        <div class="field" style="margin-top:10px">
          <label>${t('signup.labelHouseholdSelect')}</label>
          <select data-field="signup.householdId">
            <option value="">${t('signup.choosePlaceholder')}</option>
            ${f.households.map((h) => `<option value="${h.id}" ${String(h.id) === String(f.householdId) ? 'selected' : ''}>${escapeHtml(h.name)}</option>`).join('')}
          </select>
        </div>
      `}

      <div style="margin-top:22px;padding-top:14px;border-top:1px solid var(--border)">
        <div class="eyebrow">${t('signup.languageTitle')}</div>
        <div class="chip-row" style="margin-top:10px">
          ${LANG_OPTIONS.map((l) => `<button class="chip ${state.lang === l.code ? 'chip--active' : ''}" style="font-family:${l.font}" data-action="signup.pickLang" data-lang="${l.code}">${l.native}</button>`).join('')}
        </div>
      </div>

      ${isCreate ? `
        <div style="margin-top:20px;padding-top:14px;border-top:1px solid var(--border)">
          <div style="display:flex;align-items:baseline;justify-content:space-between;gap:10px">
            <div class="eyebrow">${t('signup.currencyTitle')}</div>
            <div style="font-size:12px" class="faint">${t('signup.currencyDisplayOnly')}</div>
          </div>
          <div class="chip-row" style="margin-top:10px">
            ${CURRENCY_ORDER.map((code) => `<button class="chip ${f.currency === code ? 'chip--active' : ''}" data-action="signup.pickCurrency" data-currency="${code}">${t(CURRENCY_META[code].nameKey)}</button>`).join('')}
          </div>
          <div style="margin-top:14px;display:flex;align-items:baseline;justify-content:space-between;gap:10px;background:var(--card);border:1px solid var(--border-soft);border-radius:14px;padding:12px 15px">
            <div style="font-size:12.5px" class="faint">${t('signup.amountsWillRead')}</div>
            <div class="tabular" style="font-family:var(--font-serif);font-size:21px">${currencyPreviewText(f.currency)}</div>
          </div>
          <div style="margin-top:9px;font-size:12.5px;line-height:1.5" class="faint">${t('signup.currencyFootnote')}</div>
        </div>
      ` : ''}

      ${f.error ? `<div class="error-row"><div class="error-dot"></div><div class="error-text">${escapeHtml(f.error)}</div></div>` : ''}

      <button class="btn-primary" style="margin-top:22px" data-action="signup.submit" ${f.loading ? 'disabled' : ''}>${f.loading ? t(isCreate ? 'signup.submitCreating' : 'signup.submitJoining') : t(isCreate ? 'signup.submitCreate' : 'signup.submitJoin')}</button>
      <div class="link-row"><button data-action="signup.toLogin">${t('signup.backToSignIn')}</button></div>
    </div>
  `;
}

function renderPending() {
  const title = t(state.noHousehold ? 'pending.titleNoHousehold' : 'pending.titleWaiting');
  const copy = t(state.noHousehold ? 'pending.copyNoHousehold' : 'pending.copyWaiting');
  return `
    <div class="screen screen--auth">
      <div class="brand">Halves</div>
      <div class="pending-dot-ring"><div class="pending-dot"></div></div>
      <div class="pending-title">${escapeHtml(title)}</div>
      <div class="pending-copy">${escapeHtml(copy)}</div>
      <button class="btn-secondary" style="margin-top:auto" data-action="logout">${t('common.backToSignIn')}</button>
    </div>
  `;
}

function renderHome() {
  const { net, square, owed, debts } = computeBalanceView();
  const balanceParts = moneyParts(Math.abs(net));
  const isEmpty = state.expenses.length === 0;
  const showSettle = !square && debts.length > 0;
  const recent = state.expenses.slice(0, 3);
  const stackUsers = activeMembers().slice(0, 4);
  const isMovedOut = state.me.status === 'moved_out';
  const householdName = state.household ? state.household.name : 'this household';
  const topLabel = isMovedOut ? t('home.pastFlat', { name: escapeHtml(householdName) }) : householdLabel(activeMembers().length);

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
          <button class="icon-btn" data-action="menu.open" title="${t('home.menuTitle')}">
            <div class="dot"></div><div class="dot"></div><div class="dot"></div>
          </button>
          ${state.me.role === 'admin' && state.pending.length > 0 ? `<div class="badge-count">${state.pending.length}</div>` : ''}
        </div>
      </div>

      <div class="balance-block">
        <div class="eyebrow">${t(isEmpty ? 'home.eyebrowEmpty' : square ? 'home.eyebrowSquare' : owed ? 'home.eyebrowOwed' : 'home.eyebrowOwe')}</div>
        <div class="balance-amount-row">
          <div class="balance-amount tabular">${balanceParts.amount}</div>
          ${balanceParts.unit ? `<div class="balance-unit">${balanceParts.unit}</div>` : ''}
        </div>
        <div class="balance-sub">${
          isEmpty
            ? t('home.subEmpty')
            : square
              ? t('home.subSquare')
              : t(state.expenses.length === 1 ? 'home.subPositionOne' : 'home.subPositionMany', { n: fmt(state.expenses.length) })
        }</div>
      </div>

      ${debts.length ? `
        <div class="debts-list">
          ${debts.map((d) => {
            const iAmFrom = d.from_user_id === state.me.id;
            const otherName = iAmFrom ? d.to_name : d.from_name;
            return `<div class="debt-row">
              <div class="avatar avatar--muted avatar-sm">${initials(otherName)}</div>
              <div class="debt-line">${t(iAmFrom ? 'home.debtLinePay' : 'home.debtLinePayback', { name: escapeHtml(otherName) })}</div>
              <div class="debt-amount tabular">${fmt(d.amount)}</div>
            </div>`;
          }).join('')}
        </div>
      ` : ''}

      ${!isMovedOut ? `
        <button class="btn-primary" style="margin-top:34px;display:flex;align-items:center;justify-content:center;gap:10px" data-action="openAdd">
          <span style="font-size:20px;line-height:1">+</span><span>${t('home.addExpense')}</span>
        </button>
      ` : `
        <div class="restricted-card">
          <div class="eyebrow">${t('home.movedOutEyebrow')}</div>
          <p>${t('home.movedOutBody', { household: escapeHtml(householdName) })}</p>
        </div>
      `}

      ${showSettle ? `<button class="btn-secondary" style="margin-top:10px" data-action="openSettle">${t('common.settleUp')}</button>` : ''}

      ${!isEmpty ? `
        <div style="margin-top:40px">
          <div class="section-head">
            <div class="eyebrow">${t('home.recent')}</div>
            <a href="#" data-action="toHistory" style="font-size:13.5px;color:var(--sage-soft-text);text-decoration:none">${t(state.expenses.length === 1 ? 'common.allExpensesOne' : 'common.allExpensesMany', { n: fmt(state.expenses.length) })}</a>
          </div>
          <div class="recent-card">
            ${recent.map((e) => `
              <div class="recent-row">
                <div style="flex:1;min-width:0">
                  <div class="recent-desc">${escapeHtml(e.description)}</div>
                  <div class="recent-meta">${t(e.participants.length > 1 ? 'home.recentMetaSplit' : 'home.recentMetaAll', { payer: escapeHtml(e.payer.name), cat: escapeHtml(catLabel(e.category)), n: fmt(e.participants.length), name: escapeHtml(e.participants[0] ? e.participants[0].name : '') })}</div>
                </div>
                <div class="recent-amount tabular">${fmt(e.amount)}</div>
              </div>
            `).join('')}
          </div>
        </div>
      ` : `
        <div class="empty-state">
          <h3>${t('home.emptyTitle')}</h3>
          <p>${t('home.emptyBody')}</p>
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
        <div class="eyebrow">${t(state.expenses.length === 1 ? 'common.allExpensesOne' : 'common.allExpensesMany', { n: fmt(state.expenses.length) })}</div>
      </div>
      <div style="margin-top:26px;display:flex;align-items:flex-end;justify-content:space-between;gap:14px">
        <div>
          <div style="font-family:var(--font-serif);font-size:32px;line-height:1.1">${t('history.title')}</div>
          <div style="margin-top:7px;font-size:13.5px" class="muted">${t(square ? 'home.eyebrowSquare' : owed ? 'home.eyebrowOwed' : 'home.eyebrowOwe')} <span class="tabular" style="color:var(--ink)">${fmt(Math.abs(net))}</span></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:8px;align-items:flex-end">
          ${showSettle ? `<button class="btn-pill-sage" data-action="openSettle">${t('common.settleUp')}</button>` : ''}
          ${!isEmpty ? `<button class="btn-pill-ghost" data-action="toBreakdown">${t('breakdown.link')}</button>` : ''}
        </div>
      </div>

      ${isEmpty ? `<div class="empty-state" style="margin-top:36px"><p style="margin:0">${t('history.emptyLine1')}<br />${t('history.emptyLine2')}</p></div>` : ''}

      ${groups.map((g) => `
        <div class="month-group">
          <div class="month-head"><div class="eyebrow">${escapeHtml(g.month)}</div><div class="month-total tabular">${g.total}</div></div>
          ${g.items.map((e) => {
            const isAdmin = state.me.role === 'admin';
            const editable = isAdmin && (e.kind === 'expense' || e.kind === 'settle');
            const tag = editable ? 'button' : 'div';
            const attrs = e.kind === 'expense' && isAdmin
              ? `data-action="history.editShares" data-expense-id="${e.id}"`
              : e.kind === 'settle' && isAdmin
                ? `data-action="history.deleteSettlement" data-settlement-id="${e.id}"`
                : '';
            return `
            <${tag} class="history-row" ${attrs}>
              <div class="history-day tabular">${e.day}</div>
              <div class="history-main">
                <div class="history-top">
                  <div class="history-desc" style="color:${e.titleColor}">${escapeHtml(e.desc)}</div>
                  <div class="history-amount tabular" style="color:${e.titleColor}">${fmt(e.amount)}</div>
                </div>
                <div class="history-tags">
                  <span class="${e.tagClass}">${escapeHtml(catLabel(e.cat))}</span>
                  <span class="payer-label">${escapeHtml(e.payerLabel)}</span>
                  <span class="parts-row">${e.parts.map((p) => `<span class="avatar avatar-xs">${p}</span>`).join('')}</span>
                  <span class="share-label">${escapeHtml(e.shareLabel)}</span>
                  ${e.loggedByLabel ? `<span class="payer-label">${escapeHtml(e.loggedByLabel)}</span>` : ''}
                  ${e.loggedAtLabel ? `<span class="payer-label faint">${escapeHtml(e.loggedAtLabel)}</span>` : ''}
                </div>
              </div>
            </${tag}>`;
          }).join('')}
        </div>
      `).join('')}
      ${renderTrash()}
      <div style="height:20px"></div>
    </div>
  `;
}

function renderTrash() {
  if (state.me.role !== 'admin') return '';
  const items = [
    ...state.deletedExpenses.map((e) => ({
      id: e.id, kind: 'expense', desc: e.description, amount: e.amount, deleted_at: e.deleted_at, by: e.deleted_by_id,
    })),
    ...state.deletedSettlements.map((s) => ({
      id: s.id, kind: 'settle', deleted_at: s.deleted_at, by: s.deleted_by_id, amount: s.amount,
      desc: t('history.settlementDesc', { from: nameOf(s.from_user_id), to: nameOf(s.to_user_id) }),
    })),
  ].sort((a, b) => String(b.deleted_at).localeCompare(String(a.deleted_at)));
  if (!items.length) return '';

  return `
    <div class="month-group">
      <button class="month-head" style="width:100%;border:0;background:transparent;padding:0;cursor:pointer" data-action="history.toggleTrash">
        <div class="eyebrow">${t(items.length === 1 ? 'trash.titleOne' : 'trash.titleMany', { n: fmt(items.length) })}</div>
        <div class="faint" style="font-size:12px">${state.trashOpen ? t('trash.hide') : t('trash.show')}</div>
      </button>
      ${state.trashOpen ? items.map((it) => `
        <div class="history-row" style="opacity:.7">
          <div class="history-main">
            <div class="history-top">
              <div class="history-desc" style="text-decoration:line-through">${escapeHtml(it.desc)}</div>
              <div class="history-amount tabular">${fmt(it.amount)}</div>
            </div>
            <div class="history-tags">
              <span class="payer-label faint">${escapeHtml(t('trash.deletedBy', { name: it.by ? nameOf(it.by) : '—', when: fmtDateTime(it.deleted_at) }))}</span>
            </div>
          </div>
          <button class="btn-pill-ghost" data-action="${it.kind === 'expense' ? 'history.restoreExpense' : 'history.restoreSettlement'}" data-id="${it.id}">${t('trash.restore')}</button>
        </div>
      `).join('') : ''}
    </div>
  `;
}

function renderBreakdown() {
  if (!state.breakdownFrom || !state.breakdownTo) {
    const r = breakdownPresetRange('thisMonth');
    state.breakdownFrom = r.from;
    state.breakdownTo = r.to;
  }
  const b = buildBreakdown();
  const maxCat = b.byCategory.length ? b.byCategory[0].total : 0;

  return `
    <div class="screen">
      <div class="topbar">
        <button class="icon-btn" data-action="toHistory"><div class="chevron"></div></button>
        <div class="eyebrow">${t('breakdown.link')}</div>
      </div>

      <div style="margin-top:26px;font-family:var(--font-serif);font-size:32px;line-height:1.1">${t('breakdown.title')}</div>

      <div class="bd-range">
        <button type="button" class="date-btn" data-action="breakdown.pickFrom" data-picker-target="bdFromInput">${t('breakdown.from')} · ${fmtDate(b.from)}</button>
        <input type="date" id="bdFromInput" class="date-hidden-input" data-field="breakdown.from" value="${b.from}" max="${b.to}" tabindex="-1" aria-hidden="true" />
        <span class="bd-range-dash">–</span>
        <button type="button" class="date-btn" data-action="breakdown.pickTo" data-picker-target="bdToInput">${t('breakdown.to')} · ${fmtDate(b.to)}</button>
        <input type="date" id="bdToInput" class="date-hidden-input" data-field="breakdown.to" value="${b.to}" min="${b.from}" max="${todayISO()}" tabindex="-1" aria-hidden="true" />
      </div>
      <div class="chip-row" style="margin-top:10px">
        <button class="chip" data-action="breakdown.preset" data-preset="thisMonth">${t('breakdown.presetThisMonth')}</button>
        <button class="chip" data-action="breakdown.preset" data-preset="lastMonth">${t('breakdown.presetLastMonth')}</button>
        <button class="chip" data-action="breakdown.preset" data-preset="all">${t('breakdown.presetAll')}</button>
      </div>

      ${b.isEmpty ? `<div class="empty-state" style="margin-top:40px"><p style="margin:0">${t('breakdown.empty')}</p></div>` : `
        <div class="bd-section">
          <div class="eyebrow">${t('breakdown.byCategory')}</div>
          ${b.byCategory.map((c) => `
            <div class="bd-row">
              <span class="bd-name">${escapeHtml(catLabel(c.cat))}</span>
              <span class="bd-amount tabular">${fmt(c.total)}</span>
              <div class="bd-bar"><div class="bd-bar-fill" style="inline-size:${maxCat > 0 ? Math.round((c.total / maxCat) * 100) : 0}%"></div></div>
            </div>
          `).join('')}
          <div class="bd-row bd-row--total">
            <span class="bd-name">${t('breakdown.total')}</span>
            <span class="bd-amount tabular">${fmt(b.grand)}</span>
          </div>
        </div>

        <div class="bd-section">
          <div class="eyebrow">${t('breakdown.byPerson')}</div>
          ${b.byPerson.map((pn) => {
            const open = state.breakdownPerson === pn.id;
            return `
            <button class="bd-row bd-row--person ${open ? 'bd-row--open' : ''}" data-action="breakdown.togglePerson" data-user-id="${escapeHtml(pn.id)}">
              <span class="bd-name">${escapeHtml(pn.name)}</span>
              <span class="bd-amount tabular">${fmt(pn.total)}</span>
            </button>
            ${open ? `<div class="bd-subrows">${pn.byCat.map((c) => `
              <div class="bd-row bd-row--sub">
                <span class="bd-name">${escapeHtml(catLabel(c.cat))}</span>
                <span class="bd-amount tabular">${fmt(c.total)}</span>
              </div>
            `).join('')}</div>` : ''}
          `;
          }).join('')}
        </div>
      `}
      <div style="height:24px"></div>
    </div>
  `;
}

// ---------- render: sheets ----------

function renderAddSheet() {
  const d = state.draft;
  const amt = parseAmount(d.amount);
  const splitHint = d.participantIds.length === 0
    ? t('addSheet.hintNone')
    : !amt
      ? t('addSheet.hintTagged', { n: fmt(d.participantIds.length) })
      : d.participantIds.length === 1
        ? t('addSheet.hintCarriesAll', { name: nameOf(d.participantIds[0]) })
        : t('addSheet.hintEach', { amount: fmt(amt / d.participantIds.length) });
  const payer = activeMembers().find((u) => u.id === d.payerId) || state.me;
  const draftDate = d.date || todayISO();

  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head">
        <div class="sheet-title">${t('addSheet.title')}</div>
        <button class="sheet-cancel" data-action="sheet.close">${t('common.cancel')}</button>
      </div>

      <div class="amount-row">
        <input class="amount-input tabular" inputmode="numeric" data-field="draft.amount" value="${escapeHtml(d.amount)}" placeholder="0" />
        <div class="amount-unit">${currencyUnitLabel()}</div>
      </div>
      <div class="amount-rule"></div>

      <input class="desc-input" data-field="draft.desc" value="${escapeHtml(d.desc)}" placeholder="${t('addSheet.descPlaceholder')}" />

      <div class="chip-row" style="margin-top:16px">
        ${state.catList.map((c) => `<button class="chip ${d.category === c.name ? 'chip--active' : ''}" data-action="draft.pickCategory" data-cat="${escapeHtml(c.name)}">${escapeHtml(c.name)}</button>`).join('')}
        <button class="chip chip--edit" data-action="draft.openCategories">${t('cats.edit')}</button>
      </div>

      <div class="split-block">
        <div class="split-head"><div style="font-size:14.5px" class="muted">${t('addSheet.splitBetween')}</div><div style="font-size:12.5px" class="faint">${splitHint}</div></div>
        <div class="people-row">
          ${activeMembers().map((u) => `
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
          <span>${t('addSheet.payerPaid', { name: escapeHtml(payer.name) })}</span>
        </button>
        <button type="button" class="date-btn" data-action="draft.pickDate" data-picker-target="draftDateInput">${draftDate === todayISO() ? t('addSheet.today') : fmtDate(draftDate)}</button>
        <input type="date" id="draftDateInput" class="date-hidden-input" data-field="draft.date" value="${draftDate}" max="${todayISO()}" tabindex="-1" aria-hidden="true" />
      </div>

      <button class="btn-primary" style="margin-top:16px" data-action="draft.save" ${state.draftSaving ? 'disabled' : ''}>${state.draftSaving ? t('common.saving') : t('addSheet.save')}</button>
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
        <div class="sheet-title">${t('editShares.title')}</div>
        <button class="sheet-cancel" data-action="sheet.close">${t('common.cancel')}</button>
      </div>

      <div class="split-block">
        <div class="split-head"><div style="font-size:14.5px" class="muted">${t('editShares.shares')}</div><div style="font-size:12.5px" class="faint">${t('editShares.total', { amount: money(es.amount) })}</div></div>
        ${activeMembers().map((u) => {
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

      <button class="btn-primary" style="margin-top:18px" data-action="shares.save" ${state.editSharesSaving ? 'disabled' : ''}>${state.editSharesSaving ? t('common.saving') : t('editShares.save')}</button>
      <button class="sheet-cancel" style="display:block;margin:14px auto 0;color:var(--danger-text)" data-action="shares.deleteExpense" ${state.editSharesSaving ? 'disabled' : ''}>${t('editShares.delete')}</button>
    </div>
  `;
}

function renderCategoriesSheet() {
  return `
    <div class="sheet-overlay" data-action="categories.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head">
        <div class="sheet-title">${t('cats.title')}</div>
        <button class="sheet-cancel" data-action="categories.close">${t('common.done')}</button>
      </div>
      <div style="margin-top:6px;font-size:13px;line-height:1.5" class="faint">${t('cats.subhead')}</div>

      ${state.catList.map((c) => {
        const canRemove = c.usage === 0 && state.catList.length > 1;
        return `
          <div class="cat-row">
            <input class="cat-name-input" data-cat-id="${c.id}" value="${escapeHtml(c.name)}" />
            <div class="cat-usage">${c.usage === 0 ? t('cats.unused') : t(c.usage === 1 ? 'cats.usageOne' : 'cats.usageMany', { n: fmt(c.usage) })}</div>
            <button class="cat-remove-btn ${canRemove ? 'cat-remove-btn--active' : 'cat-remove-btn--disabled'}" data-action="categories.remove" data-cat-id="${c.id}">×</button>
          </div>
        `;
      }).join('')}

      <div class="cat-add-row">
        <input class="cat-add-input" id="catAddInput" placeholder="${t('cats.addPlaceholder')}" />
        <button class="cat-add-btn" data-action="categories.add">${t('cats.add')}</button>
      </div>
      <div class="cat-footnote">${t('cats.footnote')}</div>
    </div>
  `;
}

function renderSettleSheet() {
  const { debts } = computeBalanceView();
  const sd = state.settleDraft;
  const target = getSettleTarget();
  const targetAmount = target ? target.amount : 0;
  const amt = parseAmount(sd.amount);

  let hint = t('settleSheet.enterAmount');
  if (target && amt) {
    const left = targetAmount - amt;
    if (left > 0.5) hint = t('settleSheet.outstanding', { amount: fmt(left) });
    else if (left < -0.5) hint = t('settleSheet.overpay', { amount: fmt(-left) });
    else hint = t('settleSheet.cleared');
  }

  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head"><div class="sheet-title">${t('common.settleUp')}</div><button class="sheet-cancel" data-action="sheet.close">${t('common.cancel')}</button></div>

      <div class="counterparty-row">
        ${debts.map((d) => {
          const other = d.from_user_id === state.me.id ? d.to_user_id : d.from_user_id;
          const otherName = d.from_user_id === state.me.id ? d.to_name : d.from_name;
          const label = t(d.from_user_id === state.me.id ? 'settleSheet.pay' : 'settleSheet.paysYou', { name: otherName });
          const active = other === sd.counterpartId;
          return `<button class="counterparty-chip ${active ? 'counterparty-chip--active' : ''}" data-action="settle.pick" data-user-id="${other}">
            <span>${escapeHtml(label)}</span><span class="tabular">${money(d.amount)}</span>
          </button>`;
        }).join('')}
      </div>

      <div class="settle-line">${
        target
          ? t(target.iOwe ? 'settleSheet.youOwe' : 'settleSheet.owesYou', { name: escapeHtml(target.iOwe ? target.debt.to_name : target.debt.from_name), amount: fmt(targetAmount) })
          : t('settleSheet.pickWho')
      }</div>

      <div class="amount-row">
        <input class="amount-input amount-input--sm tabular" inputmode="numeric" data-field="settle.amount" value="${escapeHtml(sd.amount)}" />
        <div class="amount-unit">${currencyUnitLabel()}</div>
      </div>
      <div class="amount-rule"></div>

      <div class="quick-row">
        <button class="quick-btn" data-action="settle.full">${t('settleSheet.fullAmount')}</button>
        <button class="quick-btn" data-action="settle.half">${t('settleSheet.half')}</button>
      </div>

      <div class="settle-hint">${hint}</div>

      <button class="btn-sage" style="margin-top:14px" data-action="settle.confirm" ${state.settleSaving ? 'disabled' : ''}>${state.settleSaving ? t('common.saving') : t('settleSheet.markPaid')}</button>
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
          <span style="flex:1">${t('menu.household')}</span>
          ${pendingCount > 0 ? `<span class="menu-badge">${pendingCount}</span>` : ''}
          <span class="faint" style="font-size:13px">${t(pendingCount > 0 ? 'menu.waiting' : 'menu.allIn')}</span>
        </button>
      ` : ''}
      <button class="menu-row" data-action="toHistory">
        <span style="flex:1">${t('history.title')}</span>
        <span class="faint" style="font-size:13px">${t(state.expenses.length === 1 ? 'common.allExpensesOne' : 'common.allExpensesMany', { n: fmt(state.expenses.length) })}</span>
      </button>
      <button class="menu-row" data-action="menu.openLang">
        <span style="flex:1">${t('langSheet.title')}</span>
        <span class="faint" style="font-size:13px">${LANG_OPTIONS.find((l) => l.code === state.lang).native}</span>
      </button>
      ${pushSupported() ? `
        <button class="menu-row" data-action="notifications.toggle">
          <span style="flex:1">${t('menu.notifications')}</span>
          <span class="faint" style="font-size:13px">${t(state.pushEnabled ? 'menu.on' : 'menu.off')}</span>
        </button>
      ` : ''}
      ${isAdmin ? `
        <button class="menu-row" data-action="menu.openBackup">
          <span style="flex:1">${t('menu.backup')}</span>
          <span class="faint" style="font-size:13px">${state.lastCopy ? t('backup.lastCopy', { when: state.lastCopy }) : t('menu.backupNone')}</span>
        </button>
      ` : ''}
      <button class="menu-row menu-row--last" data-action="logout">
        <span style="flex:1;color:var(--ink-soft)">${t('menu.signOut')}</span>
      </button>
      <button class="btn-secondary" style="margin-top:14px" data-action="sheet.close">${t('menu.close')}</button>
    </div>
  `;
}

function renderLangSheet() {
  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head">
        <div class="sheet-title">${t('langSheet.title')}</div>
        <button class="sheet-cancel" data-action="sheet.close">${t('common.done')}</button>
      </div>
      <div style="margin-top:6px;font-size:13px;line-height:1.5" class="faint">${t('langSheet.subtitle')}</div>
      <div style="margin-top:14px;display:flex;flex-direction:column">
        ${LANG_OPTIONS.map((l) => `
          <button class="menu-row" data-action="lang.pick" data-lang="${l.code}" style="padding:15px 2px">
            <span style="flex:1;font-size:17px;font-family:${l.font}">${l.native}</span>
            <span class="faint" style="font-size:13px">${t(l.descKey)}</span>
            <span style="width:16px;flex:none;font-size:15px;color:var(--sage)">${state.lang === l.code ? '✓' : ''}</span>
          </button>
        `).join('')}
      </div>
      <div style="margin-top:14px;font-size:12.5px;line-height:1.5" class="faint">${t('langSheet.footnote')}</div>
    </div>
  `;
}

function renderCurrencySheet() {
  const draft = state.draftCurrency;
  const unchanged = state.household && draft === state.household.currency;
  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head">
        <div class="sheet-title">${t('currencySheet.title')}</div>
        <button class="sheet-cancel" data-action="sheet.close">${t('common.done')}</button>
      </div>
      <div style="margin-top:6px;font-size:13px;line-height:1.5" class="faint">${t('currencySheet.subtitle')}</div>
      <div class="chip-row" style="margin-top:16px">
        ${CURRENCY_ORDER.map((code) => `<button class="chip ${draft === code ? 'chip--active' : ''}" data-action="currency.pick" data-currency="${code}">${t(CURRENCY_META[code].nameKey)}</button>`).join('')}
      </div>
      <div style="margin-top:16px;display:flex;align-items:baseline;justify-content:space-between;gap:10px;background:var(--card);border:1px solid var(--border-soft);border-radius:14px;padding:12px 15px">
        <div style="font-size:12.5px" class="faint">${t('currencySheet.amountsWillRead')}</div>
        <div class="tabular" style="font-family:var(--font-serif);font-size:21px">${currencyPreviewText(draft)}</div>
      </div>
      <div style="margin-top:12px;font-size:12.5px;line-height:1.5" class="faint">${t(unchanged ? 'currencySheet.noteUnchanged' : 'currencySheet.noteChange')}</div>
      <button class="btn-primary" style="margin-top:16px" data-action="currency.save" ${state.currencySaving ? 'disabled' : ''}>${state.currencySaving ? t('common.saving') : t('currencySheet.use', { name: t(CURRENCY_META[draft].nameKey) })}</button>
    </div>
  `;
}

function renderBackupSheet() {
  const householdName = state.household ? state.household.name : '';
  const fileName = state.backupFileHandle ? state.backupFileHandle.name : '';
  const holdLabel = state.holdPct > 0 ? t('backup.holding') : t('backup.hold');
  const holdLabelColor = state.holdPct > 45 ? 'var(--bg)' : 'var(--ink)';

  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head">
        <div class="sheet-title">${t('backup.title')}</div>
        <button class="sheet-cancel" data-action="sheet.close">${t('common.done')}</button>
      </div>
      <div style="margin-top:6px;font-size:13px;line-height:1.5" class="faint">${t('backup.subhead')}</div>

      <button class="btn-primary backup-download-btn" data-action="backup.export">${t('backup.download')}</button>
      <div class="backup-last-copy">${state.lastCopy ? t('backup.lastCopy', { when: state.lastCopy }) : t('backup.noCopyYet')}</div>

      <div class="backup-restore-head">
        <div class="eyebrow">${t('backup.restoreEyebrow')}</div>
        <div style="font-size:12px" class="faint">${t('backup.adminsOnly')}</div>
      </div>
      <div class="backup-restore-warning">${t('backup.restoreWarning', { household: householdName })}</div>

      <input type="file" id="backupFileInput" accept=".db,.sqlite,.sqlite3" style="display:none" />

      ${!state.backupFile ? `
        <button class="backup-dashed-btn" data-action="backup.pickFile">${t('backup.chooseFile')}</button>
      ` : state.backupFile === 'rejected' ? `
        <div class="backup-file-card backup-file-card--rejected">
          <div class="backup-rejected-title">${t('backup.rejectedTitle')}</div>
          <div class="backup-rejected-body">${t('backup.rejectedBody')}</div>
        </div>
        <button class="btn-secondary" style="margin-top:12px" data-action="backup.pickFile">${t('backup.chooseAnother')}</button>
        <div class="backup-touched-note">${t('backup.nothingTouched', { household: householdName })}</div>
      ` : `
        <div class="backup-file-card">
          <div class="backup-file-top">
            <div class="backup-file-name">${escapeHtml(fileName)}</div>
            <button class="backup-file-change" data-action="backup.pickFile">${t('backup.change')}</button>
          </div>
        </div>
        <button class="backup-hold-btn" id="backupHoldBtn">
          <div class="backup-hold-fill" style="width:${state.holdPct}%"></div>
          <div class="backup-hold-label" style="color:${holdLabelColor}">${holdLabel}</div>
        </button>
        <div class="backup-hold-footnote">${t('backup.holdFootnote')}</div>
      `}
    </div>
  `;
}

function renderRestoredScreen() {
  const r = state.restoreResult || { expenses_restored: 0, settlements_restored: 0, unclaimed_users_created: 0 };
  const unclaimed = r.unclaimed_users_created || 0;
  const peopleValue = unclaimed
    ? t('restored.peopleValueWithUnclaimed', { n: fmt(state.users.length - unclaimed), u: fmt(unclaimed) })
    : t('restored.peopleValue', { n: fmt(state.users.length) });
  const newUnclaimed = unclaimed ? state.users.filter((u) => u.status === 'unclaimed').slice(0, unclaimed) : [];

  return `
    <div class="screen" style="padding:62px 22px 48px">
      <div class="eyebrow">${t('restored.eyebrow')}</div>
      <div class="restored-headline">${t('restored.headline')}</div>
      <div class="restored-provenance">${state.household ? escapeHtml(state.household.name) : ''}</div>

      <div class="restored-card">
        <div class="restored-row"><div class="restored-row-label">${t('restored.rowExpenses')}</div><div class="restored-row-value tabular">${t(r.expenses_restored === 1 ? 'restored.expensesValueOne' : 'restored.expensesValueMany', { n: fmt(r.expenses_restored) })}</div></div>
        <div class="restored-row"><div class="restored-row-label">${t('restored.rowSettlements')}</div><div class="restored-row-value tabular">${t(r.settlements_restored === 1 ? 'restored.settlementsValueOne' : 'restored.settlementsValueMany', { n: fmt(r.settlements_restored) })}</div></div>
        <div class="restored-row"><div class="restored-row-label">${t('restored.rowPeople')}</div><div class="restored-row-value tabular">${peopleValue}</div></div>
        <div class="restored-row"><div class="restored-row-label">${t('restored.rowBalance')}</div><div class="restored-row-value">${t('restored.recomputed')}</div></div>
      </div>

      ${unclaimed ? `
        <div class="restored-unclaimed-card">
          ${newUnclaimed.map((u) => `
            <div class="restored-unclaimed-person">
              <div class="avatar avatar-30 avatar--unclaimed">${initials(u.name)}</div>
              <div style="flex:1;min-width:0;font-size:15px">${escapeHtml(u.name)}</div>
              <div style="font-size:12px" class="faint">${t('member.unclaimed')}</div>
            </div>
            <div class="restored-unclaimed-note">${t('restored.unclaimedNote', { name: u.name })}</div>
          `).join('') || `
            <div class="restored-unclaimed-title">${unclaimed === 1 ? t('restored.unclaimedTitleOne') : t('restored.unclaimedTitleMany', { n: fmt(unclaimed) })}</div>
          `}
        </div>
      ` : ''}

      <button class="btn-primary restored-btn-primary" data-action="toHome">${t('restored.backToHome')}</button>
      <button class="btn-secondary restored-btn-secondary" data-action="toHousehold">${t('restored.checkHousehold')}</button>
    </div>
  `;
}

function renderHousehold() {
  const members = state.users.filter((m) => m.status !== 'unclaimed');
  const unclaimedMembers = state.users.filter((m) => m.status === 'unclaimed');
  const pending = state.pending;
  const former = state.former;
  const waitingCount = pending.length;
  const householdName = state.household ? state.household.name : '';

  return `
    <div class="screen">
      <div class="topbar">
        <button class="icon-btn" data-action="toHome"><div class="chevron"></div></button>
        <div class="eyebrow">${t('hhAdmin.eyebrow')}</div>
      </div>

      <div style="margin-top:26px">
        <button class="hh-title-btn" data-action="household.openRename">
          <span class="hh-title">${escapeHtml(householdName)}</span>
          <span class="hh-rename-hint">${t('hhAdmin.renameHint')}</span>
        </button>
        <div style="margin-top:7px;font-size:13.5px" class="muted">${t(members.length === 1 ? 'hhAdmin.memberOne' : 'hhAdmin.memberMany', { n: fmt(members.length) })} · ${waitingCount ? t('hhAdmin.waitingToJoin', { n: fmt(waitingCount) }) : t('hhAdmin.nobodyWaiting')}</div>
      </div>

      <button class="menu-row" data-action="household.openCurrency" style="margin-top:20px;padding:14px 0">
        <div style="flex:1;min-width:0">
          <div style="font-size:15.5px;color:var(--ink)">${t('signup.currencyTitle')}</div>
          <div style="margin-top:3px;font-size:12.5px" class="faint">${t('hhAdmin.currencySub')}</div>
        </div>
        <div style="font-size:14.5px" class="muted">${state.household ? t(CURRENCY_META[state.household.currency].nameKey) : ''}</div>
        <div class="chevron-right"></div>
      </button>

      <div class="hh-section">
        <div class="hh-section-head"><div class="eyebrow">${t('hhAdmin.requests')}</div><div style="font-size:12px" class="faint">${waitingCount ? t('menu.waiting') : t('hhAdmin.clear')}</div></div>
        ${waitingCount === 0 ? `<div class="hh-empty">${t('hhAdmin.emptyRequests')}<br />${t('hhAdmin.emptyRequestsSub')}</div>` : ''}
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
                <button class="approve-btn" data-action="household.approve" data-user-id="${p.id}">${t('hhAdmin.approve')}</button>
              </div>
              <div class="request-role-row">
                <span class="faint" style="font-size:12.5px">${t(p.invited ? 'hhAdmin.invitedAs' : 'hhAdmin.joinsAs')}</span>
                <button class="role-pill-btn" data-action="household.toggleRole" data-user-id="${p.id}">${t(role === 'admin' ? 'common.admin' : 'common.member')}</button>
                <button class="role-swap-link" data-action="household.toggleRole" data-user-id="${p.id}">${t(role === 'admin' ? 'hhAdmin.makeMemberInstead' : 'hhAdmin.makeAdminInstead')}</button>
                <button class="request-decline-link" data-action="household.decline" data-user-id="${p.id}">${t('hhAdmin.decline')}</button>
              </div>
            </div>
          `;
        }).join('')}
      </div>

      <div class="hh-section">
        <div class="hh-section-head"><div class="eyebrow">${t('hhAdmin.members')}</div><div style="font-size:12px" class="faint">${unclaimedMembers.length ? t('hhAdmin.approvedAndUnclaimed', { approved: fmt(members.length), n: fmt(unclaimedMembers.length) }) : t('hhAdmin.approved', { n: fmt(members.length) })}</div></div>
        ${members.map((m) => `
          <button class="member-row" data-action="member.open" data-user-id="${m.id}">
            <div class="avatar avatar-30 ${avatarClass(m.id, m.id === state.me.id)}">${initials(m.name)}</div>
            <div style="flex:1;min-width:0">
              <div class="member-name">${escapeHtml(m.name)}</div>
              <div class="member-email">${escapeHtml(m.email)}</div>
            </div>
            <div class="member-role-badge ${m.role === 'admin' ? 'member-role-badge--admin' : 'member-role-badge--member'}">${t(m.role === 'admin' ? 'common.admin' : 'common.member')}</div>
            <div class="chevron-right"></div>
          </button>
        `).join('')}
        ${unclaimedMembers.map((m) => `
          <button class="member-row" data-action="member.open" data-user-id="${m.id}">
            <div class="avatar avatar-30 avatar--unclaimed">${initials(m.name)}</div>
            <div style="flex:1;min-width:0">
              <div class="member-name">${escapeHtml(m.name)}</div>
              <div class="member-email">${t('member.noAccountYet')}</div>
            </div>
            <div class="member-role-badge member-role-badge--unclaimed">${t('member.unclaimed')}</div>
            <div class="chevron-right"></div>
          </button>
        `).join('')}
        <button class="hh-add-btn" data-action="household.openInvite">
          <span style="font-size:18px;line-height:1;font-weight:400">+</span><span>${t('hhAdmin.addSomeone')}</span>
        </button>
      </div>

      ${former.length ? `
        <div class="hh-section">
          <div class="hh-section-head"><div class="eyebrow">${t('hhAdmin.formerTitle')}</div><div style="font-size:12px" class="faint">${t('hhAdmin.historyKept')}</div></div>
          ${former.map((m) => `
            <button class="former-row" data-action="member.open" data-user-id="${m.id}">
              <div class="avatar-dashed">${initials(m.name)}</div>
              <div class="former-name">${escapeHtml(m.name)}</div>
              <div class="former-standing tabular">${escapeHtml(formerStandingShort(m))}</div>
              <div class="chevron-right"></div>
            </button>
          `).join('')}
          <div class="hh-footnote">${t('hhAdmin.formerFootnote')}</div>
        </div>
      ` : ''}
      <div style="height:20px"></div>
    </div>
  `;
}

function renderMemberSheet() {
  const selected = findMember(state.selectedMemberId);
  if (!selected) return '';

  if (selected.status === 'unclaimed') {
    return `
      <div class="sheet-overlay" data-action="sheet.close"></div>
      <div class="sheet-panel">
        <div class="sheet-handle"></div>
        <div class="member-sheet-head">
          <div class="avatar avatar-38 avatar--unclaimed">${initials(selected.name)}</div>
          <div style="flex:1;min-width:0">
            <div class="member-sheet-title">${escapeHtml(selected.name)}</div>
            <div class="member-sheet-email">${t('member.noAccountYet')}</div>
          </div>
          <button class="sheet-cancel" data-action="sheet.close">${t('common.done')}</button>
        </div>

        <div class="detail-row"><div class="detail-label">${t('memberSheet.standing')}</div><div class="detail-value tabular">${escapeHtml(standingLabel(selected.id))}</div></div>
        <div class="detail-row"><div class="detail-label">${t('memberSheet.access')}</div><div class="detail-value">${t('memberSheet.noAccountYet')}</div></div>

        <div class="access-actions">
          <button class="access-action-btn" data-action="member.copySignupLink">${t('memberSheet.copySignupLink')}</button>
        </div>
        <div class="detail-note" style="margin-top:12px">${t('memberSheet.unclaimedNote')}</div>
      </div>
    `;
  }

  const isActive = selected.status === 'approved';
  const isSelf = selected.id === state.me.id;
  const isLastAdmin = selected.role === 'admin' && selected.status === 'approved' && activeAdminCount() <= 1;

  const roleOptionsHtml = ['member', 'admin'].map((r) => {
    const on = selected.role === r;
    const locked = isLastAdmin && r === 'member';
    const cls = on ? 'role-option-btn role-option-btn--on' : locked ? 'role-option-btn role-option-btn--locked' : 'role-option-btn';
    return `<button class="${cls}" data-action="member.pickRole" data-role="${r}">${t(r === 'admin' ? 'common.admin' : 'common.member')}</button>`;
  }).join('');
  const roleNote = t(selected.role === 'admin' ? 'memberSheet.roleNoteAdmin' : 'memberSheet.roleNoteMember');

  const st = selected.status;
  const firstNm = firstName(selected.name);
  const rawFirst = st === 'approved'
    ? { label: t('memberSheet.markMovedOut'), kind: 'quiet', to: 'moved_out' }
    : { label: t('memberSheet.moveBackIn', { name: firstNm }), kind: 'quiet', to: 'approved' };
  const rawSecond = st === 'removed'
    ? { label: t('memberSheet.giveBackAccess'), kind: 'quiet', to: 'moved_out' }
    : { label: t('memberSheet.revoke'), kind: 'danger', to: 'removed' };
  const actionsHtml = [rawFirst, rawSecond].map((a) => {
    const blocked = (isSelf || isLastAdmin) && a.to !== 'approved';
    const label = blocked ? t(isSelf ? 'memberSheet.blockedSelf' : 'memberSheet.blockedLastAdmin') : a.label;
    const cls = blocked ? 'access-action-btn access-action-btn--locked' : a.kind === 'danger' ? 'access-action-btn access-action-btn--danger' : 'access-action-btn';
    return `<button class="${cls}" data-action="member.runAccess" data-to="${a.to}" data-blocked="${blocked ? '1' : ''}">${escapeHtml(label)}</button>`;
  }).join('');

  const accessLabel = t(st === 'approved' ? 'memberSheet.accessInFlat' : st === 'moved_out' ? 'home.movedOutEyebrow' : 'memberSheet.accessNoAccess');
  const accessNote = t(st === 'approved' ? 'memberSheet.accessNoteApproved' : st === 'moved_out' ? 'memberSheet.accessNoteMovedOut' : 'memberSheet.accessNoteRemoved');

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
        <button class="sheet-cancel" data-action="sheet.close">${t('common.done')}</button>
      </div>

      <div class="detail-row"><div class="detail-label">${t('memberSheet.standing')}</div><div class="detail-value tabular">${escapeHtml(standingLabel(selected.id))}</div></div>

      ${isActive ? `
        <div class="detail-row">
          <div class="detail-label">${t('memberSheet.role')}</div>
          <div class="role-options-row">${roleOptionsHtml}</div>
        </div>
        <div class="detail-note role-note">${escapeHtml(roleNote)}</div>
      ` : ''}

      <div class="detail-row"><div class="detail-label">${t('memberSheet.access')}</div><div class="detail-value">${escapeHtml(accessLabel)}</div></div>
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
        <div class="sheet-title">${t('inviteSheet.title')}</div>
        <button class="sheet-cancel" data-action="sheet.close">${t('common.cancel')}</button>
      </div>

      <input class="underline-input" style="margin-top:18px" data-field="invite.name" value="${escapeHtml(f.name)}" placeholder="${t('inviteSheet.namePlaceholder')}" />
      <input class="underline-input" style="margin-top:14px" type="text" inputmode="email" autocapitalize="off" spellcheck="false" dir="ltr" data-field="invite.email" value="${escapeHtml(f.email)}" placeholder="${t('inviteSheet.emailPlaceholder')}" />

      <div class="invite-role-row">
        <div class="detail-label">${t('hhAdmin.joinsAs')}</div>
        <div class="role-options-row">
          ${['member', 'admin'].map((r) => `<button class="role-option-btn ${f.role === r ? 'role-option-btn--on' : ''}" data-action="invite.pickRole" data-role="${r}">${t(r === 'admin' ? 'common.admin' : 'common.member')}</button>`).join('')}
        </div>
      </div>

      <button class="btn-primary" style="margin-top:22px" data-action="invite.send" ${state.inviteSaving ? 'disabled' : ''}>${state.inviteSaving ? t('inviteSheet.creating') : t('inviteSheet.copyLink')}</button>
      <div class="detail-note" style="margin-top:12px">${t('inviteSheet.footnote')}</div>
    </div>
  `;
}

function renderRenameSheet() {
  return `
    <div class="sheet-overlay" data-action="sheet.close"></div>
    <div class="sheet-panel">
      <div class="sheet-handle"></div>
      <div class="sheet-head">
        <div class="sheet-title">${t('renameSheet.title')}</div>
        <button class="sheet-cancel" data-action="sheet.close">${t('common.cancel')}</button>
      </div>
      <input class="underline-input underline-input--serif" style="margin-top:18px" data-field="rename.draft" value="${escapeHtml(state.renameDraft)}" />
      <button class="btn-primary" style="margin-top:22px" data-action="household.saveRename" ${state.renameSaving ? 'disabled' : ''}>${state.renameSaving ? t('common.saving') : t('renameSheet.save')}</button>
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
          : `<div class="tagline">${t('acceptInvite.loading')}</div>`}
        <div class="link-row"><button data-action="acceptInvite.toLogin">${t('common.backToSignIn')}</button></div>
      </div>
    `;
  }
  return `
    <div class="screen screen--auth">
      <div class="brand">Halves</div>
      <div class="tagline">${t('acceptInvite.setPassword', { name: escapeHtml(ai.name), household: escapeHtml(ai.householdName) })}</div>
      <div class="form-stack">
        <div class="field">
          <label>${t('login.labelPassword')}</label>
          <input type="password" data-field="acceptInvite.password" value="${escapeHtml(ai.password)}" placeholder="${t('signup.passwordHint')}" />
        </div>
      </div>
      ${ai.error ? `<div class="error-row"><div class="error-dot"></div><div class="error-text">${escapeHtml(ai.error)}</div></div>` : ''}
      <button class="btn-primary" style="margin-top:26px" data-action="acceptInvite.submit" ${ai.loading ? 'disabled' : ''}>${ai.loading ? t('acceptInvite.joining') : t('acceptInvite.join', { household: escapeHtml(ai.householdName) })}</button>
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
    case 'breakdown': html = renderBreakdown(); break;
    case 'household': html = renderHousehold(); break;
    case 'accept-invite': html = renderAcceptInvite(); break;
    case 'restored': html = renderRestoredScreen(); break;
    default: html = renderLoading();
  }
  if (state.sheet === 'add') html += renderAddSheet();
  if (state.sheet === 'editShares') html += renderEditSharesSheet();
  if (state.sheet === 'settle') html += renderSettleSheet();
  if (state.sheet === 'menu') html += renderMenuSheet();
  if (state.sheet === 'member') html += renderMemberSheet();
  if (state.sheet === 'invite') html += renderInviteSheet();
  if (state.sheet === 'rename') html += renderRenameSheet();
  if (state.sheet === 'lang') html += renderLangSheet();
  if (state.sheet === 'currency') html += renderCurrencySheet();
  if (state.sheet === 'backup') html += renderBackupSheet();
  if (state.sheet === 'categories') html += renderCategoriesSheet();
  if (state.toast) html += `<div class="toast">${escapeHtml(state.toast)}</div>`;
  return html;
}

let lastRenderedSheet = null;

function render() {
  document.documentElement.lang = state.lang;
  document.documentElement.dir = state.lang === 'fa' ? 'rtl' : 'ltr';

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
    case 'draft.date': state.draft.date = value || todayISO(); break;
    case 'breakdown.from':
      state.breakdownFrom = value || firstOfThisMonthISO();
      if (state.breakdownFrom > state.breakdownTo) state.breakdownTo = state.breakdownFrom;
      break;
    case 'breakdown.to':
      state.breakdownTo = value || todayISO();
      if (state.breakdownTo < state.breakdownFrom) state.breakdownFrom = state.breakdownTo;
      break;
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
    case 'login.pickLang':
      state.lang = el.dataset.lang;
      localStorage.setItem('halves_lang', state.lang);
      return render();
    case 'signup.open': return openSignup();
    case 'signup.toLogin': state.route = 'login'; return render();
    case 'signup.modeCreate': state.signupForm.mode = 'create'; return render();
    case 'signup.modeJoin': state.signupForm.mode = 'join'; return render();
    case 'signup.pickLang':
      state.lang = el.dataset.lang;
      localStorage.setItem('halves_lang', state.lang);
      return render();
    case 'signup.pickCurrency': state.signupForm.currency = el.dataset.currency; return render();
    case 'signup.submit': return doSignup();
    case 'logout': return doLogout();
    case 'menu.open': state.sheet = 'menu'; return render();
    case 'menu.openLang': state.sheet = 'lang'; return render();
    case 'lang.pick': return pickLanguage(el.dataset.lang);
    case 'household.openCurrency': return openCurrencySheet();
    case 'currency.pick': state.draftCurrency = el.dataset.currency; return render();
    case 'currency.save': return saveCurrency();
    case 'notifications.toggle': return toggleNotifications();
    case 'openAdd': return openAddSheet();
    case 'openSettle': return openSettleSheet();
    case 'sheet.close': state.sheet = null; return render();
    case 'toHistory': state.sheet = null; state.route = 'history'; return render();
    case 'toHome': state.sheet = null; state.route = 'home'; return render();
    case 'toBreakdown': {
      if (!state.breakdownFrom) {
        const r = breakdownPresetRange('thisMonth');
        state.breakdownFrom = r.from;
        state.breakdownTo = r.to;
      }
      state.breakdownPerson = null;
      state.route = 'breakdown';
      return render();
    }
    case 'breakdown.pickFrom':
    case 'breakdown.pickTo':
      return openDatePicker(el);
    case 'breakdown.preset': {
      const r = breakdownPresetRange(el.dataset.preset);
      state.breakdownFrom = r.from;
      state.breakdownTo = r.to;
      return render();
    }
    case 'breakdown.togglePerson':
      state.breakdownPerson = state.breakdownPerson === el.dataset.userId ? null : el.dataset.userId;
      return render();
    case 'toHousehold': return goHousehold();
    case 'household.toggleRole': return toggleRequestRole(el.dataset.userId);
    case 'household.approve': return approveRequest(el.dataset.userId);
    case 'household.decline': return declineRequest(el.dataset.userId);
    case 'household.openRename': return openRenameSheet();
    case 'household.saveRename': return saveRename();
    case 'household.openInvite': return openInviteSheet();
    case 'invite.pickRole': state.inviteForm.role = el.dataset.role; return render();
    case 'invite.send': return sendInvite();
    case 'member.open': return openMemberSheet(el.dataset.userId);
    case 'member.pickRole': return pickMemberRole(el.dataset.role);
    case 'member.runAccess': return runAccessAction(el.dataset.to, el.dataset.blocked === '1');
    case 'member.copySignupLink': return copySignupLink();
    case 'menu.openBackup': return openBackupSheet();
    case 'backup.export': return exportBackup();
    case 'backup.pickFile': return pickBackupFile();
    case 'draft.openCategories': return openCategoriesSheet();
    case 'categories.close': return closeCategoriesSheet();
    case 'categories.remove': return removeCategory(Number(el.dataset.catId));
    case 'categories.add': {
      const input = document.getElementById('catAddInput');
      const value = input ? input.value : '';
      if (input) input.value = '';
      return addCategory(value);
    }
    case 'acceptInvite.submit': return submitAcceptInvite();
    case 'acceptInvite.toLogin':
      history.replaceState(null, '', location.pathname);
      state.route = 'login';
      return render();
    case 'draft.pickCategory': state.draft.category = el.dataset.cat; return render();
    case 'draft.togglePerson': return toggleDraftPerson(el.dataset.userId);
    case 'draft.cyclePayer': return cyclePayer();
    case 'draft.pickDate': return openDatePicker(el);
    case 'draft.save': return saveExpense();
    case 'history.editShares': return openEditSharesSheet(Number(el.dataset.expenseId));
    case 'history.deleteSettlement': return deleteSettlement(Number(el.dataset.settlementId));
    case 'history.toggleTrash': state.trashOpen = !state.trashOpen; return render();
    case 'history.restoreExpense': return restoreExpense(Number(el.dataset.id));
    case 'history.restoreSettlement': return restoreSettlement(Number(el.dataset.id));
    case 'shares.togglePerson': return toggleShareParticipant(el.dataset.userId);
    case 'shares.inc': return bumpShare(el.dataset.userId, 1);
    case 'shares.dec': return bumpShare(el.dataset.userId, -1);
    case 'shares.save': return saveShares();
    case 'shares.deleteExpense': return deleteExpenseFromSheet();
    case 'settle.pick': return pickCounterparty(el.dataset.userId);
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
    // Date inputs are handled on 'change' only -- re-rendering mid-pick
    // (which replaces the input) fights the open native picker.
    if (el.matches('input[type="date"]')) return;
    handleFieldInput(el.dataset.field, el.value);
  });

  app.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const field = e.target.closest('[data-field]');
    if (field) {
      if (field.dataset.field === 'login.password') doLogin();
      if (field.dataset.field === 'signup.password') doSignup();
      if (field.dataset.field === 'acceptInvite.password') submitAcceptInvite();
      return;
    }
    // Categories sheet: name/add inputs are uncontrolled (not wired through
    // data-field/state) so typing doesn't fight the full-page re-render on
    // every keystroke -- Enter is what actually persists them.
    if (e.target.matches('.cat-name-input')) return e.target.blur(); // triggers the blur listener below
    if (e.target.matches('.cat-add-input')) {
      const value = e.target.value;
      e.target.value = '';
      return addCategory(value);
    }
  });

  app.addEventListener(
    'blur',
    (e) => {
      if (e.target.matches && e.target.matches('.cat-name-input')) {
        renameCategory(Number(e.target.dataset.catId), e.target.value);
      }
    },
    true,
  );

  app.addEventListener('change', (e) => {
    if (e.target.id === 'backupFileInput') return onBackupFileChosen(e.target.files[0]);
    // <input type="date"> doesn't reliably fire 'input' on selection across
    // browsers (Safari and some mobile pickers only emit 'change'), so the
    // 'input' handler above can miss it -- route it through here too.
    const field = e.target.closest('[data-field]');
    if (field && field.matches('input[type="date"]')) handleFieldInput(field.dataset.field, field.value);
  });

  // Hold-to-restore isn't a click, so it's outside the data-action
  // dispatcher -- delegated the same way, keyed on the button's id since
  // it's re-rendered on every state change.
  app.addEventListener('pointerdown', (e) => {
    if (e.target.closest('#backupHoldBtn')) startHold();
  });
  app.addEventListener('pointerup', (e) => {
    if (e.target.closest('#backupHoldBtn')) endHold();
  });
  app.addEventListener('pointerleave', (e) => {
    if (e.target.closest && e.target.closest('#backupHoldBtn')) endHold();
  }, true);
}

// ---------- boot ----------

initEvents();
render();
boot();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => { /* installability is best-effort */ });
  });
}
