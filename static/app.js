// ---- Reminder stages: how long before the due time we alert you ----
const HOUR = 36e5;
const STAGES = [                       // listed from earliest to latest
  { key: "2d", ms: 48 * HOUR, label: "2 days" },
  { key: "1d", ms: 24 * HOUR, label: "1 day" },
  { key: "2h", ms: 2 * HOUR,  label: "2 hours" },
];
const FILTERS = {                      // what each tab shows
  all: () => true,
  upcoming: (a) => !a.done && a.left > 0,
  overdue: (a) => !a.done && a.left <= 0,
  done: (a) => a.done,
};
const PROMPTS = ["Summarize my files", "Quiz me on this", "Explain it simply", "What should I study first?"];
const $ = (id) => document.getElementById(id);
let assignments = [], filter = "all", imported = 0;

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// "2d 4h", "5h 10m", "12m" - or "Overdue"
function fmt(ms) {
  if (ms <= 0) return "Overdue";
  const d = Math.floor(ms / 864e5), h = Math.floor((ms % 864e5) / HOUR), m = Math.floor((ms % HOUR) / 6e4);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${Math.max(1, m)}m`;
}

// ---- Dashboard: hero, stat cards and assignment list ----
function renderAll() {
  const now = Date.now();
  const all = assignments.map((a) => ({ ...a, left: new Date(a.due) - now }));
  const open = all.filter((a) => !a.done);
  const next = open.find((a) => a.left > 0);
  const week = open.filter((a) => a.left > 0 && a.left < 7 * 24 * HOUR).length;

  $("sub").textContent = next
    ? `${week} due in the next 7 days. Next up: ${next.title} (${next.course}).`
    : "Nothing upcoming. Enjoy the breathing room.";
  $("nextTime").textContent = next ? fmt(next.left) : "–";
  $("nextTitle").textContent = next ? next.title : "No upcoming deadlines";

  const stats = [
    ["Due in 48 hours", open.filter((a) => a.left > 0 && a.left < 48 * HOUR).length, "soon"],
    ["Upcoming", open.filter((a) => a.left > 0).length, ""],
    ["Overdue", open.filter((a) => a.left <= 0).length, "late"],
    ["Turned in", all.filter((a) => a.done).length, "ok"],
  ];
  $("stats").innerHTML = stats.map(([l, n, c]) => `<div class="stat ${c}"><b>${n}</b><span>${l}</span></div>`).join("");

  const list = all.filter(FILTERS[filter]);
  $("assignments").innerHTML = list.length ? "" : '<p class="empty">Nothing here.</p>';
  for (const a of list) {
    const cls = a.done ? "done" : a.left <= 0 ? "late" : a.left < 24 * HOUR ? "urgent" : a.left < 48 * HOUR ? "soon" : "";
    const el = document.createElement("div");
    el.className = `card ${cls}`;
    // The three pills light up as each reminder stage is reached
    const pills = STAGES.map((s) => `<span class="dot ${a.left <= s.ms ? "on" : ""}" title="Reminder ${s.label} before">${s.key}</span>`).join("");
    el.innerHTML = `<div><b></b><small></small><div class="dots">${pills}</div></div>
                    <div class="side"><span class="chip"></span><a target="_blank" rel="noopener">Open in Classroom</a></div>`;
    el.querySelector("b").textContent = a.title;
    el.querySelector("small").textContent = `${a.course} · due ${new Date(a.due).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
    el.querySelector(".chip").textContent = a.done ? "Turned in" : fmt(a.left);
    a.link ? (el.querySelector("a").href = a.link) : el.querySelector("a").remove();
    $("assignments").append(el);
  }
}

// ---- Reminders (browser notification + on-screen toast) ----
function toast(text) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = text;
  $("toasts").append(t); setTimeout(() => t.remove(), 12000);
  if (Notification.permission === "granted") new Notification("StudyMate", { body: text });
}

function checkReminders() {
  const sent = JSON.parse(localStorage.getItem("sent") || "{}");
  for (const a of assignments) {
    const left = new Date(a.due) - Date.now();
    if (a.done || left <= 0) continue;
    const reached = STAGES.filter((s) => left <= s.ms);   // stages already reached
    if (!reached.length) continue;
    const stage = reached[reached.length - 1];            // the closest one
    if (!sent[`${a.id}:${stage.key}`]) toast(`"${a.title}" is due in about ${stage.label} (${a.course})`);
    reached.forEach((s) => (sent[`${a.id}:${s.key}`] = true)); // never repeat or double-fire
  }
  localStorage.setItem("sent", JSON.stringify(sent));
  renderAll();
}

// ---- Classroom files ----
function showImported() { $("imp").textContent = `${imported} file${imported === 1 ? "" : "s"} imported`; }

async function loadFiles(done) {
  const list = await api("/api/files");
  $("files").innerHTML = list.length ? "" : '<p class="empty">No files attached in Classroom yet.</p>';
  for (const f of list) {
    const has = done.has(f.id);
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML = `<div><b></b><small></small></div><button class="btn small"></button>`;
    el.querySelector("b").textContent = f.name;
    el.querySelector("small").textContent = f.course;
    const btn = el.querySelector("button");
    let on = has;                                   // is this file currently imported?
    const show = () => {                            // update the button to match
      btn.textContent = on ? "Remove" : "Import";
      btn.classList.toggle("ghost", on); el.classList.toggle("has", on);
      btn.disabled = false;
    };
    show();
    btn.onclick = async () => {
      btn.disabled = true; btn.textContent = on ? "Removing…" : "Importing…";
      try {
        await api(`/api/import/${f.id}`, { method: on ? "DELETE" : "POST" });
        on = !on; imported += on ? 1 : -1; showImported();
      } catch (e) { toast(e.message); }
      show();
    };
    $("files").append(el);
  }
}

// ---- Chat ----
function say(text, who) {
  const p = document.createElement("div");
  p.className = `msg ${who}`; p.textContent = text;
  $("log").append(p); $("log").scrollTop = $("log").scrollHeight;
  return p;
}
async function ask(message) {
  message = message.trim();
  if (!message) return;
  $("msg").value = ""; say(message, "me");
  const wait = say("Thinking…", "bot");
  try {
    const { reply } = await api("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    // Gemini writes in Markdown (**bold**, ### headings); turn it into clean, safe HTML
    if (window.marked && window.DOMPurify) wait.innerHTML = DOMPurify.sanitize(marked.parse(reply));
    else wait.textContent = reply;
  } catch (e) { wait.textContent = "Something went wrong: " + e.message; }
  $("log").scrollTop = $("log").scrollHeight;
}
$("send").onclick = () => ask($("msg").value);
$("msg").onkeydown = (e) => { if (e.key === "Enter") ask($("msg").value); };
PROMPTS.forEach((p) => {
  const b = document.createElement("button"); b.textContent = p; b.onclick = () => ask(p); $("chips").append(b);
});
$("tabs").onclick = (e) => {
  if (!e.target.dataset.f) return;
  filter = e.target.dataset.f;
  [...$("tabs").children].forEach((b) => b.classList.toggle("on", b === e.target));
  renderAll();
};

// ---- Start ----
(async function init() {
  const me = await api("/api/me");
  if (!me.signed_in) { $("welcome").hidden = false; return; }

  // Name and photo, top right
  const p = me.profile || {}, first = p.first || "there";
  $("uname").textContent = p.name || first;
  $("avatar").innerHTML = p.picture ? `<img alt="" referrerpolicy="no-referrer" src="${p.picture}">` : `<b>${first[0]}</b>`;
  $("avatar").firstChild.onerror = () => ($("avatar").innerHTML = `<b>${first[0]}</b>`);
  $("user").hidden = false;

  // Greeting
  const hr = new Date().getHours();
  $("greet").textContent = `Good ${hr < 12 ? "morning" : hr < 17 ? "afternoon" : "evening"}, ${first}`;
  $("date").textContent = new Date().toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
  $("app").hidden = false;

  // Welcome splash, once per browser session
  if (!sessionStorage.getItem("welcomed")) {
    sessionStorage.setItem("welcomed", "1");
    $("splashText").textContent = `Welcome in, ${first}.`;
    $("splash").hidden = false;
    setTimeout(() => $("splash").classList.add("out"), 1500);
    setTimeout(() => ($("splash").hidden = true), 2100);
  }

  if (Notification.permission === "default") Notification.requestPermission();
  imported = me.library.length; showImported();
  try {
    assignments = await api("/api/assignments");
    checkReminders();
    await loadFiles(new Set(me.library.map((l) => l.id)));
  } catch (e) { toast(e.message); }
  setInterval(checkReminders, 60 * 1000);                                                   // every minute
  setInterval(async () => { assignments = await api("/api/assignments"); }, 5 * 60 * 1000); // refresh every 5 min
})();