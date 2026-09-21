/* Shortlist front end: vanilla JS talking to the FastAPI JSON API. No build step.
 * Flow (hash routes):  #/  home  ->  #/jobs  pick or add a job  ->  #/upload/:id  add resumes  ->  #/results/:id  ranking + chat
 */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = { auth: { enabled: false, open: false, user: null, signup: false }, jobs: [], jobId: null, job: null, cards: [], filter: "All", busy: false, asking: false, picked: new Set() };

// ---------------------------------------------------------------- helpers
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage blocked: fine */ } },
};
// Resumes often print names in ALL CAPS ("JEEVAN RAJ M"); show them consistently in normal case.
const nice = (n) => (n && n === n.toUpperCase() ? n.toLowerCase().replace(/(^|\s)\p{L}/gu, (m) => m.toUpperCase()) : n || "");
const recClass = (r) => `rec-${r}`;
const barClass = (s) => (s >= 70 ? "good" : s >= 45 ? "warn" : "bad");
const fmt1 = (n) => (n == null ? "–" : Number(n).toFixed(1));

async function api(path, opts = {}) {
  let res;
  try { res = await fetch(path, opts); }
  catch { throw new Error("Cannot reach the server. Is it still running? Start it again and reload this page."); }   // was: "Failed to fetch"
  if (res.status === 401 && state.auth.enabled && !path.startsWith("/api/auth/login")) {   // session ended or never started
    state.auth.user = null; updateChrome(); location.hash = "#/login";
    throw new Error("Please sign in.");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    throw new Error(msg);
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}

// ---------------------------------------------------------------- router
const STEPS = [["jobs", "Choose a job"], ["upload", "Upload resumes"], ["results", "Ranking & questions"]];

function stepper(active) {
  const idx = STEPS.findIndex(([k]) => k === active);
  return `<ol class="stepper" aria-label="Progress">${STEPS.map(([k, label], i) => {
    const cls = i < idx ? "done" : i === idx ? "on" : "";
    const href = k === "jobs" ? "#/jobs" : state.jobId ? `#/${k}/${state.jobId}` : "#/jobs";
    const dot = i < idx ? "✓" : i + 1;
    return `<li class="${cls}" ${i === idx ? 'aria-current="step"' : ""}>${i <= idx ? `<a href="${href}">` : ""}<span class="dot">${dot}</span><span class="lbl">${label}</span>${i <= idx ? "</a>" : ""}</li>`;
  }).join("")}</ol>`;
}

async function route() {
  closeDrawer(); closeModal();
  const [view = "home", arg] = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const id = Number(arg) || null;
  let name = ["home", "jobs", "upload", "results", "login", "users"].includes(view) ? view : "home";
  if (state.auth.enabled && !state.auth.user && name !== "login") { location.hash = "#/login"; return; }
  if (name === "login" && (!state.auth.enabled || state.auth.user)) { location.hash = "#/jobs"; return; }
  if (name === "users" && state.auth.user?.role !== "admin") { toast("Only admins can manage users"); location.hash = "#/jobs"; return; }

  if ((name === "upload" || name === "results") && !id) { location.hash = "#/jobs"; return; }
  if (id) {
    try { await selectJob(id); } catch { toast("That job no longer exists"); location.hash = "#/jobs"; return; }
  }
  $$("[data-view]").forEach((el) => { el.hidden = el.dataset.view !== name; });
  $$("[data-stepper]").forEach((el) => { el.innerHTML = stepper(name); });
  window.scrollTo(0, 0);
  updateChrome();

  if (name === "home") await renderHome();
  if (name === "jobs") await renderJobs();
  if (name === "upload") renderUploadPage();
  if (name === "results") await renderResults();
  if (name === "users") await renderUsers();
}
window.addEventListener("hashchange", route);

async function selectJob(id) {
  state.jobId = id; store.set("jobId", id);
  state.job = await api(`/api/jobs/${id}`);
}

function updateChrome() {
  const signedOut = state.auth.enabled && !state.auth.user;              // on the login screen: hide everything but the logo
  $$("header nav, #startBtn").forEach((el) => el.classList.toggle("!hidden", signedOut));
  renderUserMenu();
  const pill = $("#jobPill");
  if (state.job && state.jobId) { pill.hidden = false; pill.textContent = `#${state.jobId} · ${state.job.title || "Untitled job"}`; pill.href = `#/results/${state.jobId}`; }
  else pill.hidden = true;
  $("#navResults").href = state.jobId ? `#/results/${state.jobId}` : "#/jobs";
}

// ---------------------------------------------------------------- authentication (only active when the server has AUTH_ENABLED=1)
function renderUserMenu() {
  const box = $("#userMenu"), u = state.auth.user;
  box.hidden = !u;
  if (!u) return;
  box.innerHTML = `<span class="mono hidden text-[12px] sm:inline" style="color:var(--muted)">${esc(u.name || u.email)} · ${esc(u.role)}</span>
    ${u.role === "admin" ? `<a class="btn btn-ghost btn-sm" href="#/users">Users</a>` : ""}
    <button class="btn btn-ghost btn-sm" data-act="password" type="button">Password</button>
    <button class="btn btn-ghost btn-sm" data-act="logout" type="button">Sign out</button>`;
}

$("#userMenu").addEventListener("click", async (e) => {
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (act === "logout") {
    try { await api("/api/auth/logout", { method: "POST" }); } catch { /* already signed out */ }
    state.auth.user = null; state.jobId = null; state.job = null; updateChrome(); location.hash = "#/login";
  }
  if (act === "password") openPasswordModal();
});

// The login card doubles as the sign-up form when registration is open.
function setLoginMode(signup) {
  state.auth.signup = signup;
  $("#loginEyebrow").textContent = signup ? "Create account" : "Sign in";
  $("#loginTitle").innerHTML = signup ? "Join <b>Shortlist</b>" : "Welcome <b>back</b>";
  $("#loginSub").textContent = signup ? "Create a recruiter account to start screening resumes." : "Sign in to screen resumes and rank candidates.";
  $("#nameRow").hidden = !signup;
  $("#loginPassword").autocomplete = signup ? "new-password" : "current-password";
  $("#loginBtn").textContent = signup ? "Create account" : "Sign in";
  $("#modeRow").hidden = !state.auth.open;
  $("#modeText").textContent = signup ? "Already have an account?" : "New here?";
  $("#modeBtn").textContent = signup ? "Sign in" : "Create an account";
  $("#loginError").classList.add("hidden");
}
$("#modeBtn").addEventListener("click", () => setLoginMode(!state.auth.signup));

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#loginError"), btn = $("#loginBtn"), signup = state.auth.signup;
  err.classList.add("hidden");
  if (!$("#loginEmail").value.trim() || !$("#loginPassword").value) { err.textContent = "Enter your email and password."; err.classList.remove("hidden"); return; }
  btn.disabled = true;
  try {
    const r = await api(signup ? "/api/auth/register" : "/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("#loginEmail").value, password: $("#loginPassword").value, name: $("#loginName").value }) });
    if (signup) { toast("Account created. Welcome!"); setLoginMode(false); }
    state.auth.user = r.user; $("#loginPassword").value = ""; location.hash = "#/jobs"; route();
  } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
  finally { btn.disabled = false; }
});

function openPasswordModal() {
  const body = $("#modalBody");
  body.className = "card p-6";
  body.innerHTML = `<form id="pwForm" novalidate><div class="eyebrow">Account</div><h2 class="h-display mt-2 text-[22px]">Change password</h2>
    <input id="pwCur" class="field mt-5" type="password" placeholder="Current password" autocomplete="current-password" aria-label="Current password">
    <input id="pwNew" class="field mt-3" type="password" placeholder="New password (8+ characters)" autocomplete="new-password" aria-label="New password">
    <p id="pwErr" class="mt-3 hidden text-sm" style="color:var(--bad)" role="alert"></p>
    <div class="mt-5 flex justify-end gap-2"><button class="btn btn-ghost btn-sm" type="button" id="pwCancel">Cancel</button><button class="btn btn-primary btn-sm" type="submit">Save</button></div></form>`;
  $("#modal").classList.add("open");
  $("#pwCancel").onclick = closeModal;
  $("#pwForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/auth/password", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: $("#pwCur").value, new_password: $("#pwNew").value }) });
      closeModal(); toast("Password changed");
    } catch (ex) { $("#pwErr").textContent = ex.message; $("#pwErr").classList.remove("hidden"); }
  };
  $("#pwCur").focus();
}

async function renderUsers() {
  const rows = await api("/api/users");
  const me = state.auth.user.id;
  $("#userRows").innerHTML = rows.map((u) => `<tr style="cursor:default">
    <td><div class="font-semibold">${esc(u.name || "–")}${u.id === me ? ` <span class="mono text-[11px]" style="color:var(--violet)">you</span>` : ""}</div><div class="mono text-[12px]" style="color:var(--muted)">${esc(u.email)}</div></td>
    <td><select class="field" style="padding:6px 30px 6px 10px;width:auto" data-uid="${u.id}" data-f="role" aria-label="Role for ${esc(u.email)}" ${u.id === me ? "disabled" : ""}>
      ${["recruiter", "admin"].map((r) => `<option value="${r}" ${u.role === r ? "selected" : ""}>${r}</option>`).join("")}</select></td>
    <td><span class="chip" style="${u.is_active ? "background:rgba(95,208,138,.12);color:var(--good)" : "background:rgba(229,86,109,.12);color:var(--bad)"}">${u.is_active ? "Active" : "Disabled"}</span></td>
    <td class="mono whitespace-nowrap text-[12px]" style="color:var(--muted)">${esc((u.last_login_at || "never").slice(0, 16))}</td>
    <td class="whitespace-nowrap text-right">
      <button class="btn btn-ghost btn-sm" data-uid="${u.id}" data-do="reset" type="button">Reset password</button>
      ${u.id === me ? "" : `<button class="btn btn-ghost btn-sm" data-uid="${u.id}" data-do="toggle" data-active="${u.is_active}" type="button">${u.is_active ? "Disable" : "Enable"}</button>
      <button class="btn btn-ghost btn-sm" style="color:var(--bad)" data-uid="${u.id}" data-do="delete" type="button">Delete</button>`}</td></tr>`).join("");
}
const jsonOpts = (method, obj) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj) });

$("#newUserForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/users", jsonOpts("POST", { name: $("#nuName").value, email: $("#nuEmail").value, password: $("#nuPassword").value, role: $("#nuRole").value }));
    e.target.reset(); toast("User added"); renderUsers();
  } catch (ex) { toast(ex.message); }
});
$("#userRows").addEventListener("change", async (e) => {
  if (e.target.dataset.f !== "role") return;
  try { await api(`/api/users/${e.target.dataset.uid}`, jsonOpts("PATCH", { role: e.target.value })); toast("Role updated"); }
  catch (ex) { toast(ex.message); }
  renderUsers();
});
$("#userRows").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-do]");
  if (!b) return;
  const id = b.dataset.uid;
  try {
    if (b.dataset.do === "toggle") await api(`/api/users/${id}`, jsonOpts("PATCH", { is_active: b.dataset.active !== "true" }));
    if (b.dataset.do === "delete") { if (!confirm("Delete this user permanently?")) return; await api(`/api/users/${id}`, { method: "DELETE" }); }
    if (b.dataset.do === "reset") {
      const pw = prompt("New password for this user (8+ characters):");
      if (!pw) return;
      await api(`/api/users/${id}`, jsonOpts("PATCH", { password: pw })); toast("Password reset");
    }
  } catch (ex) { toast(ex.message); }
  renderUsers();
});

// ---------------------------------------------------------------- home
async function renderHome() {
  try { state.jobs = await api("/api/jobs"); } catch { return; }
  const latest = state.jobs.find((j) => j.scored > 0);
  const link = $("#homeLatest");
  link.hidden = !latest;
  if (latest) link.href = `#/results/${latest.id}`;
}

// ---------------------------------------------------------------- step 1: choose a job
async function renderJobs() {
  state.jobs = await api("/api/jobs");
  const grid = $("#jobGrid");
  if (!state.jobs.length) {
    grid.innerHTML = `<div class="card px-6 py-10 text-center" style="color:var(--muted)">No jobs yet. Add your first job on the right.</div>`;
    return;
  }
  grid.innerHTML = state.jobs.map((j) => {
    const date = (j.created_at || "").slice(0, 10);
    return `<div class="job-card ${j.id === state.jobId ? "now" : ""}">
      <div class="min-w-0 flex-1"><div class="truncate text-[16px] font-semibold">${esc(j.title || "Untitled job")}</div>
        <div class="mono mt-1 text-[11.5px]" style="color:var(--muted)">#${j.id} · ${j.scored} candidate${j.scored === 1 ? "" : "s"}${j.pending ? ` · ${j.pending} awaiting choice` : ""}${date ? ` · added ${esc(date)}` : ""}${j.owner ? ` · by ${esc(j.owner)}` : ""}</div></div>
      ${j.scored ? `<a class="btn btn-ghost btn-sm" href="#/results/${j.id}">View ranking</a>` : ""}
      <a class="btn btn-primary btn-sm" href="#/upload/${j.id}">Screen resumes →</a></div>`;
  }).join("");
}

$("#jdFile").addEventListener("change", (e) => { $("#jdFileName").textContent = e.target.files[0]?.name || ""; });

$("#createJobBtn").addEventListener("click", async (e) => {
  const text = $("#jdText").value.trim(), file = $("#jdFile").files[0];
  if (!text && !file) return toast("Paste a job description or choose a file");
  const btn = e.currentTarget; btn.disabled = true; btn.innerHTML = `<span class="spin"></span> Reading job…`;
  const fd = new FormData(); fd.append("text", text); if (file) fd.append("file", file);
  try {
    const job = await api("/api/jobs", { method: "POST", body: fd });
    $("#jdText").value = ""; $("#jdFile").value = ""; $("#jdFileName").textContent = "";
    toast(`Job added: ${job.title}`);
    location.hash = `#/upload/${job.id}`;
  } catch (err) { toast(err.message); }
  btn.disabled = false; btn.textContent = "Analyse job";
});

// ---------------------------------------------------------------- step 2: upload resumes
function chipList(list, tone, max = 14) {
  const shown = list.slice(0, max).map((s) => `<span class="sk ${tone}">${esc(s)}</span>`).join("");
  return shown + (list.length > max ? `<span class="sk">+${list.length - max} more</span>` : "");
}

function renderUploadPage() {
  const j = state.job;
  $("#uploadJob").innerHTML = `<div class="flex flex-wrap items-start justify-between gap-3"><div>
      <div class="mono text-[11px] uppercase tracking-[.16em]" style="color:var(--faint)">Screening for</div>
      <div class="mt-1 text-lg font-semibold">${esc(j.title || "Untitled job")}</div></div>
      <div class="mono text-[12px]" style="color:var(--muted)">${j.scored} already scored</div></div>
    <div class="mt-4 text-[13px]" style="color:var(--muted)">${j.required_skills.length} required skills</div>
    <div class="mt-2 flex flex-wrap gap-1.5">${chipList(j.required_skills, "sk-green")}</div>
    ${j.preferred_skills.length ? `<div class="mt-3 text-[13px]" style="color:var(--muted)">${j.preferred_skills.length} nice to have</div><div class="mt-2 flex flex-wrap gap-1.5">${chipList(j.preferred_skills, "sk-yellow", 8)}</div>` : ""}`;
  $("#uploadList").innerHTML = ""; $("#uploadDone").classList.add("hidden"); $("#uploadDone").classList.remove("flex");
  const skip = $("#skipToResults"); skip.hidden = !j.scored; skip.href = `#/results/${state.jobId}`;
}

const dz = $("#dropzone"), input = $("#resumeInput");
dz.addEventListener("click", () => input.click());
dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
dz.addEventListener("drop", (e) => uploadFiles([...e.dataTransfer.files]));
input.addEventListener("change", () => { uploadFiles([...input.files]); input.value = ""; });

const OUTCOME = {
  stored: (o) => ({ cls: `rec-${o.recommendation}`, label: `${o.recommendation} · ${fmt1(o.score)}` }),
  duplicate_ignored: () => ({ cls: "st-ok", label: "Already submitted" }),
  conflict_pending: () => ({ cls: "st-warn", label: "Different version: choose one" }),
  needs_review: () => ({ cls: "st-warn", label: "Needs a human look" }),
  rejected_file: () => ({ cls: "rec-Reject", label: "Rejected" }),
};

async function uploadFiles(files) {
  if (!state.jobId) return toast("Choose a job first");
  if (state.busy) return toast("Still processing the previous batch");
  if (!files.length) return;
  state.busy = true;
  const list = $("#uploadList");
  $("#uploadDone").classList.add("hidden"); $("#uploadDone").classList.remove("flex");
  const rows = files.map((f) => {
    const li = document.createElement("li");
    li.className = "rounded-2xl border px-4 py-3"; li.style.borderColor = "var(--line)";
    li.innerHTML = `<div class="flex items-center gap-3"><span class="mono min-w-0 flex-1 truncate text-[13px]">${esc(f.name)}</span><span class="st mono text-xs" style="color:var(--muted)">waiting</span></div><div class="sub mono mt-1 hidden text-[11.5px]" style="color:var(--muted)"></div>`;
    list.appendChild(li); return li;
  });
  let ok = 0;
  for (let i = 0; i < files.length; i++) {           // one request per file: real per-file progress
    const st = $(".st", rows[i]), sub = $(".sub", rows[i]);
    st.innerHTML = `<span class="spin"></span> reading, checking, scoring…`;
    const fd = new FormData(); fd.append("files", files[i]);
    try {
      const { outcomes } = await api(`/api/jobs/${state.jobId}/resumes`, { method: "POST", body: fd });
      const o = outcomes[0], view = (OUTCOME[o.status] || OUTCOME.needs_review)(o);
      st.outerHTML = `<span class="st chip ${view.cls}" title="${esc(o.message)}">${esc(view.label)}</span>`;
      const bits = o.name ? [nice(o.name), o.email, `${o.skills_found} skills extracted`].filter(Boolean) : [o.message];
      sub.textContent = bits.join(" · "); sub.classList.remove("hidden");
      if (o.status === "stored" || o.status === "conflict_pending" || o.status === "duplicate_ignored") ok++;
    } catch (err) { st.outerHTML = `<span class="chip rec-Reject">${esc(err.message).slice(0, 60)}</span>`; }
  }
  state.busy = false;
  if (ok) {
    $("#uploadDoneTitle").textContent = `${ok} of ${files.length} resume${files.length > 1 ? "s" : ""} analysed`;
    $("#toResults").href = `#/results/${state.jobId}`;
    const box = $("#uploadDone"); box.classList.remove("hidden"); box.classList.add("flex");
    setTimeout(() => { if (location.hash.startsWith("#/upload") && !state.busy) location.hash = `#/results/${state.jobId}`; }, 2200);
  } else toast("No resumes could be processed. Check the reasons above.");
}

// ---------------------------------------------------------------- step 3: results (ranking + chat)
async function renderResults() {
  const [job, cards] = await Promise.all([api(`/api/jobs/${state.jobId}`), api(`/api/jobs/${state.jobId}/candidates`)]);
  state.job = job; state.cards = cards; state.filter = "All"; state.picked.clear(); updateChrome();
  $("#resultsTitle").innerHTML = `${esc(job.title || "Untitled job")}`;
  $("#addMore").href = `#/upload/${state.jobId}`;
  $("#exportBtn").href = `/api/jobs/${state.jobId}/export.csv`;
  $("#exportXlsx").href = `/api/jobs/${state.jobId}/export.xlsx`;
  renderStats(); renderFilters(); renderRanking(); updateCompareBtn(); renderSuggestions(); renderAlerts(); loadChatHistory(); loadDb();
}

function renderStats() {
  const j = state.job;
  $("#statScored").textContent = j.scored;
  $("#statShort").textContent = j.by_recommendation.Shortlist;
  $("#statAvg").textContent = j.average_score != null ? fmt1(j.average_score) : "–";
  $("#statReq").textContent = j.required_skills.length;
}

function renderFilters() {
  const by = state.job?.by_recommendation || {};
  const opts = [["All", state.cards.length], ["Shortlist", by.Shortlist || 0], ["Consider", by.Consider || 0], ["Reject", by.Reject || 0]];
  $("#filters").innerHTML = opts.map(([name, n]) => `<button class="tab ${state.filter === name ? "on" : ""}" data-f="${name}">${name}<small>${n}</small></button>`).join("");
}
$("#filters").addEventListener("click", (e) => {
  const b = e.target.closest("[data-f]"); if (!b) return;
  state.filter = b.dataset.f; renderFilters(); renderRanking();
});

function renderRanking() {
  const rows = state.cards.filter((c) => state.filter === "All" || c.recommendation === state.filter);
  const empty = $("#rankEmpty");
  if (!rows.length) {
    empty.classList.remove("hidden");
    empty.innerHTML = state.cards.length ? "No candidates in this group." : `No candidates yet. <a class="underline" href="#/upload/${state.jobId}">Upload resumes</a> to see the ranking.`;
    $("#rankBody").innerHTML = ""; return;
  }
  empty.classList.add("hidden");
  $("#rankBody").innerHTML = rows.map((c) => {
    const flag = c.verification === "corrected" ? `<span class="chip st-warn" title="The second check corrected something in this resume's data">Corrected</span>`
      : c.verification === "partial" ? `<span class="chip st-warn" title="Second AI unavailable at the time">Partly checked</span>` : "";
    const pending = c.llm_status === "unavailable" ? `<span class="chip st-warn" title="AI analysis text missing; retry">Analysis pending</span>` : "";
    return `<tr class="row ${state.picked.has(c.application_id) ? "picked" : ""}" tabindex="0" data-app="${c.application_id}">
      <td class="pick-cell"><input type="checkbox" class="pick" data-pick="${c.application_id}" ${state.picked.has(c.application_id) ? "checked" : ""} aria-label="Select ${esc(nice(c.name))} to compare"></td>
      <td class="mono" style="color:var(--faint)">${String(c.rank).padStart(2, "0")}</td>
      <td><div class="font-medium">${esc(nice(c.name))}</div><div class="mono mt-0.5 text-[11.5px]" style="color:var(--faint)">${esc(c.file)}</div></td>
      <td><div class="flex items-center gap-3"><span class="mono w-11 text-[15px] font-medium">${fmt1(c.score)}</span>
        <div class="bar ${barClass(c.score)} flex-1"><i style="width:${Math.max(2, c.score)}%"></i></div></div>
        <div class="mt-2 flex flex-wrap gap-1.5"><span class="chip ${recClass(c.recommendation)}">${c.recommendation}</span>${flag}${pending}</div></td>
      <td class="hide-sm mono text-[13px]">${c.required_matched}<span style="color:var(--faint)"> / ${c.required_total}</span></td>
      <td class="hide-sm text-[13px]">${c.experience_years ? c.experience_years + " yr" : "Fresher"}${c.internships.length ? `<div class="mono text-[11px]" style="color:var(--faint)">${c.internships.length} internship${c.internships.length > 1 ? "s" : ""}</div>` : ""}</td></tr>`;
  }).join("");
}
$("#rankBody").addEventListener("click", (e) => {
  const pick = e.target.closest("[data-pick]");
  if (pick) { togglePick(Number(pick.dataset.pick), pick.checked, pick); return; }
  if (e.target.closest(".pick-cell")) return;
  const r = e.target.closest("tr[data-app]"); if (r) openDrawer(Number(r.dataset.app));
});
$("#rankBody").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.closest("tr[data-app]")?.click(); });

// ---------------------------------------------------------------- compare 2-3 candidates side by side
function togglePick(id, on, box) {
  if (on && state.picked.size >= 3) { box.checked = false; toast("Compare up to 3 candidates at a time"); return; }
  on ? state.picked.add(id) : state.picked.delete(id);
  box.closest("tr").classList.toggle("picked", on);
  updateCompareBtn();
}
function updateCompareBtn() {
  const n = state.picked.size, b = $("#compareBtn");
  b.textContent = `Compare (${n})`; b.disabled = n < 2;
  b.title = n < 2 ? "Tick 2 or 3 candidates in the table" : "Compare the selected candidates side by side";
}
$("#compareBtn").addEventListener("click", () => openCompare([...state.picked]));

function openCompare(ids) {
  const picks = ids.map((id) => state.cards.find((c) => c.application_id === id)).filter(Boolean);
  if (picks.length < 2) return;
  const cols = `grid-template-columns:repeat(${picks.length}, minmax(0, 1fr))`;
  const best = Math.max(...picks.map((c) => c.score));
  const skillNames = picks[0].skills.map((s) => [s.skill, s.importance]);
  const status = (c, name) => c.skills.find((s) => s.skill === name);
  const dot = { green: "var(--good)", yellow: "var(--warn)", orange: "var(--orange)", red: "var(--bad)" };
  const list = (items) => items.length ? `<ul class="mt-2 space-y-1.5 text-[13px]" style="color:#cfcbdb">${items.slice(0, 4).map((i) => `<li class="flex gap-2"><span style="color:var(--faint)">–</span><span>${esc(i)}</span></li>`).join("")}</ul>` : `<p class="mt-2 text-[13px]" style="color:var(--faint)">None</p>`;
  const rows = skillNames.map(([name, imp]) => {
    const cells = picks.map((c) => status(c, name) || { status: "missing", colour: "red" });
    const differs = new Set(cells.map((x) => x.status)).size > 1;
    return `<tr class="${differs ? "diff" : ""}"><td class="first">${esc(name)}${imp === "preferred" ? ' <span class="mono" style="color:var(--faint);font-size:10px">BONUS</span>' : ""}</td>${cells.map((x) =>
      `<td><span class="inline-flex items-center gap-2"><i style="width:8px;height:8px;border-radius:50%;background:${dot[x.colour]};display:inline-block"></i>${x.status === "solid" ? "solid" : x.status}</span></td>`).join("")}</tr>`;
  }).join("");

  $("#modalBody").className = "card p-6 modal-wide";
  $("#modalBody").innerHTML = `<div class="flex items-start justify-between gap-4"><div><div class="eyebrow">Compare</div>
      <h3 class="h-display mt-3 text-[26px]">${picks.length} candidates, <b>side by side.</b></h3>
      <p class="mt-2 text-sm" style="color:var(--muted)">Highlighted skill rows are where the candidates differ.</p></div>
      <button class="btn btn-ghost btn-sm" id="closeModal">Close</button></div>

    <div class="cmp-grid mt-6" style="${cols}">${picks.map((c) => `<div class="cmp-col">
        <div class="mono text-[11px]" style="color:var(--faint)">RANK ${String(c.rank).padStart(2, "0")}</div>
        <div class="mt-1 text-[17px] font-semibold leading-tight">${esc(nice(c.name))}</div>
        <div class="mt-3 flex flex-wrap items-end gap-x-3 gap-y-2"><span class="text-[34px] font-semibold leading-none">${fmt1(c.score)}</span>
          <span class="chip ${recClass(c.recommendation)}">${c.recommendation}</span>${c.score === best ? '<span class="mono text-[10px]" style="color:var(--good)">HIGHEST</span>' : ""}</div>
        <div class="mono mt-2 text-[12px]" style="color:var(--muted)">${c.required_matched} of ${c.required_total} required skills</div>
        <div class="mt-4 space-y-2.5">${COMPONENTS.map(([k, label]) => { const v = c.components[k] ?? 0;
          return `<div><div class="flex justify-between text-[12px]"><span>${label}</span><span class="mono">${fmt1(v)}</span></div><div class="bar mt-1"><i style="width:${Math.max(2, v)}%"></i></div></div>`; }).join("")}</div>
        <div class="mono mt-4 text-[12px]" style="color:var(--muted)">${c.experience_years ? c.experience_years + " yr paid" : "Fresher"} · ${c.internships.length} internship${c.internships.length === 1 ? "" : "s"}</div>
      </div>`).join("")}</div>

    <div class="mono mt-7 text-[10.5px] uppercase tracking-[.18em]" style="color:var(--faint)">Skills against this job</div>
    <div class="mt-3 overflow-x-auto"><table class="cmp-table"><thead><tr><th>Skill</th>${picks.map((c) => `<th>${esc(nice(c.name).split(" ")[0])}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>

    <div class="cmp-grid mt-7" style="${cols}">${picks.map((c) => `<div>
        <div class="mono text-[10.5px] uppercase tracking-[.16em]" style="color:var(--faint)">Strengths</div>${list(c.strengths)}
        <div class="mono mt-4 text-[10.5px] uppercase tracking-[.16em]" style="color:var(--faint)">Gaps</div>${list(c.weaknesses)}</div>`).join("")}</div>`;
  $("#closeModal").onclick = closeModal;
  $("#modal").classList.add("open");
}

// ---------------------------------------------------------------- alerts (pending choices, review queue, failed analyses)
async function renderAlerts() {
  const box = $("#alerts"); box.innerHTML = "";
  const [conflicts, review] = await Promise.all([api(`/api/jobs/${state.jobId}/conflicts`), api(`/api/jobs/${state.jobId}/review`)]);
  const alert = (tone, title, body, action) => `<div class="card flex flex-wrap items-center gap-4 px-5 py-4" style="border-color:${tone}33;background:${tone}0d">
    <div class="min-w-0 flex-1"><div class="font-medium">${title}</div><div class="mt-0.5 text-[13.5px]" style="color:var(--muted)">${body}</div></div>${action || ""}</div>`;
  if (conflicts.length) {
    const who = [...new Set(conflicts.map((c) => nice(c.name)))];
    box.insertAdjacentHTML("beforeend", alert("#e9b44c", `${conflicts.length} resume${conflicts.length > 1 ? "s" : ""} waiting for a decision`,
      `${esc(who.join(", "))} submitted more than one different resume for this job. The applicant chooses which one counts.`,
      `<button class="btn btn-ghost btn-sm" id="reviewConflicts">Choose resume</button>`));
    $("#reviewConflicts").onclick = openConflicts;
  }
  if (state.job?.failed_analyses) {
    box.insertAdjacentHTML("beforeend", alert("#c471ed", `${state.job.failed_analyses} analysis${state.job.failed_analyses > 1 ? "es" : ""} incomplete`,
      "The AI service was busy, so the written analysis is missing. Scores use the skills data only until you retry.",
      `<button class="btn btn-ghost btn-sm" id="retryBtn">Retry now</button>`));
    $("#retryBtn").onclick = async (e) => {
      e.target.disabled = true; e.target.innerHTML = `<span class="spin"></span> Retrying`;
      try { const r = await api(`/api/jobs/${state.jobId}/rescore`, { method: "POST" }); toast(`Re-analysed ${r.results.filter((x) => x.llm_status === "ok").length} candidate(s)`); }
      catch (err) { toast(err.message); }
      renderResults();
    };
  }
  if (review.length) {
    box.insertAdjacentHTML("beforeend", alert("#e5566d", `${review.length} resume${review.length > 1 ? "s" : ""} need a human look`,
      review.map((r) => `${esc(r.name || r.resume_filename)} (${r.extraction_status !== "ok" ? "missing contact details" : "details could not be verified"})`).join(" · ")));
  }
}

async function openConflicts() {
  const body = $("#modalBody");
  body.className = "card p-6";
  body.innerHTML = `<div class="eyebrow">Choose a resume</div><p class="mt-6"><span class="spin"></span> Scoring each version against this job…</p>`;
  $("#modal").classList.add("open");
  let items;
  try { items = await api(`/api/jobs/${state.jobId}/conflicts?preview=true`); } catch (e) { toast(e.message); closeModal(); return; }
  const groups = {};
  items.forEach((i) => { (groups[i.email || i.name] ||= { name: i.name, active_id: i.active_id, current_file: i.current_file, options: [] }).options.push(i); });
  const current = (g) => state.cards.find((c) => c.application_id === g.active_id);
  body.innerHTML = `<div class="flex items-start justify-between gap-4"><div><div class="eyebrow">Choose a resume</div>
      <h3 class="h-display mt-3 text-[26px]">One person, <b>several versions.</b></h3>
      <p class="mt-2 text-sm" style="color:var(--muted)">Picking one replaces the others for this job. The previews show what each version would score.</p></div>
      <button class="btn btn-ghost btn-sm" id="closeModal">Close</button></div>
    ${Object.values(groups).map((g) => {
      const cur = current(g);
      const opts = [{ id: g.active_id, file: g.current_file, score: cur?.score, rec: cur?.recommendation, matched: cur ? `${cur.required_matched}/${cur.required_total}` : "–", now: true },
        ...g.options.map((o) => ({ id: o.pending_id, file: o.new_file, score: o.preview?.score, rec: o.preview?.recommendation, matched: o.preview?.required_matched || "–" }))]
        .sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
      return `<div class="mt-6"><div class="font-medium">${esc(nice(g.name))}</div>
        <div class="mt-3 space-y-2">${opts.map((o) => `<div class="flex flex-wrap items-center gap-3 rounded-2xl border p-3.5" style="border-color:var(--line)">
          <div class="min-w-0 flex-1"><div class="mono truncate text-[13px]">${esc(o.file)}</div>
            <div class="mt-1 text-xs" style="color:var(--muted)">${o.now ? "Currently active · " : ""}required skills ${o.matched}</div></div>
          <div class="text-right"><div class="mono text-lg font-medium">${o.score != null ? fmt1(o.score) : "–"}</div>${o.rec ? `<span class="chip ${recClass(o.rec)}">${o.rec}</span>` : ""}</div>
          <button class="btn ${o.now ? "btn-ghost" : "btn-primary"} btn-sm" data-keep="${o.id}">Keep this one</button></div>`).join("")}</div></div>`;
    }).join("")}`;
  $("#closeModal").onclick = closeModal;
  $$("[data-keep]", body).forEach((b) => b.onclick = async () => {
    b.disabled = true; b.innerHTML = `<span class="spin"></span>`;
    try { await api("/api/conflicts/resolve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep_application_id: Number(b.dataset.keep) }) }); toast("Resume choice saved"); }
    catch (e) { toast(e.message); }
    closeModal(); renderResults();
  });
}
const closeModal = () => $("#modal").classList.remove("open");
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

// ---------------------------------------------------------------- candidate drawer
const COMPONENTS = [["skills", "Skills match", "50%"], ["experience", "Experience", "20%"], ["projects_education", "Projects & education", "15%"], ["fit", "Overall fit", "15%"]];
const STATUS_LABEL = { solid: "", working: "working", inferred: "inferred", semantic: "by meaning", basic: "basic", partial: "partial", missing: "" };

function openDrawer(appId) {
  const c = state.cards.find((x) => x.application_id === appId); if (!c) return;
  const skillGroup = (colour, title) => {
    const list = c.skills.filter((s) => s.colour === colour);
    if (!list.length) return "";
    return `<div class="mt-4"><div class="mono text-[10.5px] uppercase tracking-[.16em]" style="color:var(--faint)">${title} · ${list.length}</div>
      <div class="mt-2 flex flex-wrap gap-1.5">${list.map((s) => `<span class="sk sk-${colour}" title="${esc(s.evidence ? `${s.importance} · resume says: “${s.evidence}”` : s.importance)}">${esc(s.skill)}${STATUS_LABEL[s.status] ? ` <small>${STATUS_LABEL[s.status]}</small>` : ""}${s.importance === "preferred" ? ` <small>bonus</small>` : ""}</span>`).join("")}</div></div>`;
  };
  const list = (items) => items.length ? `<ul class="mt-2 space-y-1.5 text-[14px]" style="color:#cfcbdb">${items.map((i) => `<li class="flex gap-2"><span style="color:var(--faint)">–</span><span>${esc(i)}</span></li>`).join("")}</ul>` : `<p class="mt-2 text-sm" style="color:var(--faint)">None found</p>`;
  const section = (title, inner) => `<div class="hairline mt-7 pt-6"><div class="mono text-[10.5px] uppercase tracking-[.18em]" style="color:var(--faint)">${title}</div>${inner}</div>`;
  const kv = (k, v) => `<div class="flex gap-4 py-1.5 text-[14px]"><div class="mono w-28 shrink-0 text-[11.5px] uppercase tracking-[.1em]" style="color:var(--faint)">${k}</div><div class="min-w-0 flex-1" style="color:#d7d3e2">${v}</div></div>`;
  const logLine = (l) => {
    const src = (l.evidence_quote.match(/^\[(\w+)\]/) || [])[1] === "llm" ? "second AI" : "automatic check";
    const why = l.evidence_quote.replace(/^\[\w+\]\s*/, "");
    const what = l.old_value && l.new_value ? `Replaced “${esc(l.old_value)}” with “${esc(l.new_value)}”` : l.old_value ? `Removed “${esc(l.old_value)}”` : `Added “${esc(l.new_value)}”`;
    return `<li class="rounded-xl border p-3 text-[13px]" style="border-color:var(--line)"><div>${what} <span class="mono text-[11px]" style="color:var(--faint)">· ${src}</span></div><div class="mt-1" style="color:var(--muted)">${esc(why).slice(0, 140)}</div></li>`;
  };
  const edu = c.education.map((e) => [e.degree, e.institution, e.year].filter(Boolean).join(" · ")).filter(Boolean);
  const interns = c.internships.map((i) => `${i.title || "Intern"} at ${i.company || "?"}${i.duration ? ` (${i.duration})` : ""}`);
  const extracted = `<div class="mt-3">
      ${kv("Name", esc(nice(c.name)))}${kv("Email", esc(c.email || "not found"))}${kv("Phone", esc(c.phone || "not found"))}
      ${kv("Experience", c.experience_years ? `${c.experience_years} years paid employment` : "No paid employment (fresher)")}
      ${kv("Education", edu.length ? edu.map(esc).join("<br>") : "not found")}
      ${kv("Internships", interns.length ? interns.map(esc).join("<br>") : "none")}
      ${kv("Certifications", c.certifications.length ? c.certifications.slice(0, 6).map(esc).join("<br>") : "none")}
    </div>
    <div class="mono mt-4 text-[10.5px] uppercase tracking-[.16em]" style="color:var(--faint)">Projects · ${c.projects.length}</div>${list(c.projects.slice(0, 8))}
    <div class="mono mt-5 text-[10.5px] uppercase tracking-[.16em]" style="color:var(--faint)">All skills found · ${c.all_skills.length}</div>
    <div class="mt-2 flex flex-wrap gap-1.5">${c.all_skills.map((s) => `<span class="sk" ${s.raw && s.raw !== s.skill ? `title="written on the resume as: ${esc(s.raw)}"` : ""}>${esc(s.skill)}${s.level ? ` <small>${esc(s.level)}</small>` : ""}</span>`).join("")}</div>
    ${c.soft_skills.length ? `<div class="mono mt-5 text-[10.5px] uppercase tracking-[.16em]" style="color:var(--faint)">Soft skills · not part of the technical score</div><div class="mt-2 flex flex-wrap gap-1.5">${c.soft_skills.map((s) => `<span class="sk">${esc(s)}</span>`).join("")}</div>` : ""}`;

  $("#drawer").innerHTML = `<div class="p-6 md:p-8">
    <div class="flex items-start justify-between gap-4"><div class="min-w-0">
      <div class="eyebrow">Rank ${String(c.rank).padStart(2, "0")}</div>
      <h3 class="h-display mt-3 text-[30px]">${esc(nice(c.name))}</h3>
      <div class="mono mt-2 text-[12px]" style="color:var(--muted)">${esc(c.email || "no email")} · ${esc(c.phone || "no phone")}</div></div>
      <button class="btn btn-ghost btn-sm" id="closeDrawer" aria-label="Close details">Close</button></div>

    <div class="mt-7 flex items-end gap-5"><div class="text-[56px] font-semibold leading-none tracking-tight">${fmt1(c.score)}</div>
      <div class="pb-1.5"><span class="chip ${recClass(c.recommendation)}">${c.recommendation}</span>
      <div class="mono mt-2 text-xs" style="color:var(--muted)">${c.required_matched} of ${c.required_total} required skills</div></div></div>
    ${c.summary ? `<p class="mt-5 text-[15px] leading-relaxed" style="color:#d3cfde">${esc(c.summary)}</p>` : `<p class="mt-5 text-sm" style="color:var(--warn)">Written analysis unavailable (AI was busy). Use “Retry now” on the ranking page.</p>`}

    ${section("How the score was built", `<div class="mt-4 space-y-3.5">${COMPONENTS.map(([k, label, w]) => {
      const v = c.components[k] ?? 0;
      return `<div><div class="flex justify-between text-[13px]"><span>${label} <span class="mono" style="color:var(--faint)">· weight ${w}</span></span><span class="mono">${fmt1(v)}</span></div><div class="bar mt-1.5"><i style="width:${Math.max(2, v)}%"></i></div></div>`;
    }).join("")}</div>`)}

    ${section("Skills against this job", `${skillGroup("green", "Solid")}${skillGroup("yellow", "Working, inferred or matched by meaning")}${skillGroup("orange", "Basic")}${skillGroup("red", "Missing")}
      <p class="mono mt-4 text-[11px]" style="color:var(--faint)">Inferred = implied by related tools on the resume (for example TensorFlow implies Machine Learning), not stated.</p>`)}

    ${section("Strengths", list(c.strengths))}${section("Gaps", list(c.weaknesses))}
    ${section("Suggested interview questions", list(c.interview_questions))}
    ${section("Extracted from the resume", extracted)}
    ${section("Double-check log", c.verification_log.length
      ? `<p class="mt-2 text-[13px]" style="color:var(--muted)">Corrections made after the first extraction, with the reason for each.</p><ul class="mt-3 space-y-2">${c.verification_log.map(logLine).join("")}</ul>`
      : `<p class="mt-2 text-sm" style="color:var(--muted)">No corrections needed. The second check agreed with the extraction.</p>`)}
    <p class="mono mt-8 text-[11px]" style="color:var(--faint)">Source file: ${esc(c.file)}</p></div>`;
  $("#drawer").scrollTop = 0;
  $("#drawer").classList.add("open"); $("#scrim").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false");
  $("#closeDrawer").focus(); $("#closeDrawer").onclick = closeDrawer;
}
function closeDrawer() { $("#drawer").classList.remove("open"); $("#scrim").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); }
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDrawer(); closeModal(); } });

// ---------------------------------------------------------------- chat
function md(text) {
  // Minimal, safe markdown: escape first, then bold, bullet lists and simple tables.
  const lines = esc(text).split("\n"); const out = []; let inList = false, table = [];
  const flushTable = () => { if (!table.length) return; const rows = table.filter((r) => !/^\|?\s*:?-{2,}/.test(r)).map((r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
    out.push(`<table>${rows.map((r, i) => `<tr>${r.map((c) => `<${i ? "td" : "th"}>${c}</${i ? "td" : "th"}>`).join("")}</tr>`).join("")}</table>`); table = []; };
  for (const raw of lines) {
    const line = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    if (/^\s*\|/.test(line)) { table.push(line); continue; } flushTable();
    if (/^\s*[*-]\s+/.test(line)) { if (!inList) { out.push("<ul>"); inList = true; } out.push(`<li>${line.replace(/^\s*[*-]\s+/, "")}</li>`); continue; }
    if (inList) { out.push("</ul>"); inList = false; }
    if (line.trim()) out.push(`<p>${line}</p>`);
  }
  flushTable(); if (inList) out.push("</ul>"); return out.join("");
}

function addMsg(role, html, calls) {
  const log = $("#chatLog"), div = document.createElement("div");
  div.className = `msg ${role === "user" ? "user" : "bot"}`;
  div.innerHTML = html + (calls?.length ? `<div class="tools" title="The safe database queries the assistant ran">${calls.map((c) => `<span>${esc(c.tool)}(${esc(Object.entries(c.args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", "))})</span>`).join("")}</div>` : "");
  log.appendChild(div); log.scrollTop = log.scrollHeight; return div;
}

async function loadChatHistory() {
  const log = $("#chatLog"); log.innerHTML = "";
  const hist = await api(`/api/jobs/${state.jobId}/chat`);
  if (!hist.length) addMsg("bot", `<p>Ask me about the candidates for <b>${esc(state.job?.title || "this job")}</b>. Pick a suggestion below or type your own question.</p>`);
  hist.forEach((m) => addMsg(m.role, m.role === "user" ? esc(m.content) : md(m.content)));
}

function renderSuggestions() {
  const a = nice(state.cards[0]?.name).split(" ")[0], b = nice(state.cards[1]?.name).split(" ")[0];
  const qs = ["Show me the top 5 candidates.", "Who is the best candidate for this role?", "Which candidates know Python?",
    "Which candidates have Machine Learning experience?", "Which candidates are missing Docker?",
    a && b ? `Compare ${a} and ${b}.` : null, a && b ? `Why is ${a} ranked higher than ${b}?` : null,
    "Show candidates with more than 2 years of experience.", "Which candidates have FastAPI experience?",
    "Recommend the best candidate for interview.", "Who has done an internship?", "How many candidates were shortlisted?"].filter(Boolean);
  $("#suggestions").innerHTML = qs.map((q) => `<button type="button" class="tab" data-q="${esc(q)}">${esc(q)}</button>`).join("");
}
$("#suggestions").addEventListener("click", (e) => { const b = e.target.closest("[data-q]"); if (b) ask(b.dataset.q); });

async function ask(q) {
  if (!state.jobId) return toast("Choose a job first");
  if (state.asking) return; state.asking = true;
  $("#chatInput").value = ""; $("#chatSend").disabled = true;
  addMsg("user", esc(q));
  const wait = addMsg("bot", `<span class="dots"><i></i><i></i><i></i></span>`);
  try {
    const r = await api(`/api/jobs/${state.jobId}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) });
    wait.remove(); addMsg("bot", md(r.text), r.calls);
  } catch (err) { wait.remove(); addMsg("bot", `<p>${esc(err.message)}</p>`); }
  state.asking = false; $("#chatSend").disabled = false; $("#chatInput").focus();
}
$("#chatForm").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#chatInput").value.trim(); if (q) ask(q); });
$("#clearChat").addEventListener("click", async () => { if (!state.jobId) return; await api(`/api/jobs/${state.jobId}/chat`, { method: "DELETE" }); loadChatHistory(); });

// ---------------------------------------------------------------- data viewer
let dbTable = "applications";
async function loadDb() {
  let counts; try { counts = await api("/api/db"); } catch { return; }
  $("#dbTabs").innerHTML = Object.entries(counts).map(([t, n]) => `<button class="tab ${t === dbTable ? "on" : ""}" data-t="${t}">${t}<small>${n}</small></button>`).join("");
  const d = await api(`/api/db/${dbTable}?limit=60`);
  const cell = (v) => v == null ? `<span style="color:var(--faint)">null</span>` : esc(v);
  $("#dbView").innerHTML = `<div class="data-wrap"><table class="data-table"><thead><tr>${d.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${
    d.rows.length ? d.rows.map((r) => `<tr>${r.map((v) => `<td title="${esc(v)}">${cell(v)}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${d.columns.length}">No rows yet</td></tr>`}</tbody></table></div>
    <p class="mono mt-2 text-[11px]" style="color:var(--faint)">Showing ${d.rows.length} of ${d.total} rows in <b>${esc(d.table)}</b>, newest first.</p>`;
}
$("#dbTabs").addEventListener("click", (e) => { const b = e.target.closest("[data-t]"); if (b) { dbTable = b.dataset.t; loadDb(); } });

// ---------------------------------------------------------------- scroll reveal (home only) + boot
const io = "IntersectionObserver" in window ? new IntersectionObserver((ents) => ents.forEach((en) => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } }), { threshold: 0.08 }) : null;
$$(".reveal").forEach((el) => (io ? io.observe(el) : el.classList.add("in")));

// Footer tells the truth about where the AI runs (cloud APIs or a self-hosted model).
api("/api/health").then((h) => { if (h.llm_mode === "local") $("#footMode").textContent = "Shortlist · FastAPI · SQLite · self-hosted local model (nothing leaves this machine)"; }).catch(() => {});

// Boot: ask the server whether login is required, restore an existing session, then start routing.
(async () => {
  try {
    const st = await api("/api/auth/status");
    state.auth.enabled = st.auth_enabled; state.auth.open = st.registration_open; setLoginMode(false);
    if (state.auth.enabled) { try { state.auth.user = (await api("/api/auth/me")).user; } catch { /* not signed in: route() sends us to the login screen */ } }
  } catch (e) { toast("Could not reach the server: " + e.message); }
  route().catch((e) => toast("Could not reach the server: " + e.message));
})();
