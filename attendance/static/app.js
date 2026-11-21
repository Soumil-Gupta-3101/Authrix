"use strict";
const $ = (id) => document.getElementById(id);
const mode = document.body.dataset.mode;
const csrf = document.querySelector('meta[name="csrf-token"]').content;
let current = null, polling = false, studentSignature = "", cameraUrl = null;
const text = (id, value) => { if ($(id).textContent !== String(value)) $(id).textContent = value; };
const tableVersions = new Map();
const pretty = (value) => String(value || "—").replaceAll("_", " ");
const timestamp = (value) => value ? new Date(value).toLocaleString("en-IN", { timeZone: current?.timezone || "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" }) : "—";
function showError(message) { text("error", message); $("error").hidden = false; }
async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify(payload) });
  if (response.status === 401) { location.href = "/login"; throw new Error("Sign in again"); }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed");
  return result;
}
async function action(path, payload) {
  try { $("error").hidden = true; await api(path, payload); await refresh(); return true; }
  catch (error) { showError(error.message); return false; }
}
function cell(value) { const node = document.createElement("td"); node.textContent = value; return node; }
function badge(value, good = false, bad = false) { const node = document.createElement("span"); node.className = `badge ${bad ? "bad" : good ? "" : "subtle"}`; node.textContent = value; return node; }
function table(id, rows, columns, render) {
  const version = JSON.stringify(rows) + (id === "students-body" ? `${!!current.active_session}:${current.job.status === "running"}` : "");
  if (tableVersions.get(id) === version) return;
  tableVersions.set(id, version);
  const body = $(id); body.replaceChildren();
  if (!rows.length) { const tr = document.createElement("tr"), td = cell("Nothing here yet."); td.colSpan = columns; td.className = "empty"; tr.append(td); body.append(tr); return; }
  rows.forEach((row) => { const tr = document.createElement("tr"); render(tr, row); body.append(tr); });
}
function fillSelect(id, students, empty) {
  const select = $(id), previous = select.value;
  select.replaceChildren(new Option(empty, ""));
  students.forEach(s => select.add(new Option(`${s.roll} · ${s.name}`, s.id)));
  select.add(new Option("Unknown / unregistered", "999999999"));
  if ([...select.options].some(o => o.value === previous)) select.value = previous;
}
function render(state) {
  current = state;
  const active = state.active_session, busy = state.job.status === "running";
  text("student-count", state.students.length);
  text("face-count", `${state.storage.available_faces} face enrollments available`);
  text("storage-database", state.storage.database);
  text("storage-faces", state.storage.faces_directory);
  text("storage-matcher", state.storage.face_backend === "sface" ? "SFace · Offline" : pretty(state.storage.face_backend));
  text("present-count", state.report.filter(r => r.status === "Present").length);
  text("session-count", active ? `of ${state.report.length} students in ${active.class_name}` : "No active session");
  $("active-session").hidden = !active; $("session-form").hidden = !!active;
  text("session-badge", active ? "Active" : "Inactive");
  if (active) { text("session-title", active.subject); text("session-detail", `${active.class_name} · Started ${timestamp(active.started_at)}`); }
  text("job-badge", pretty(state.job.status)); text("job-message", state.job.message);
  text("job-warning", state.job.warning || "");
  text("lcd-line1", state.feedback.lines[0]); text("lcd-line2", state.feedback.lines[1]);
  const lcdStatus = { pending: "LCD checked during the next operation", connected: "LCD connected", unavailable: "LCD unavailable · Dashboard active", disabled: "LCD disabled · Dashboard active", dashboard: "Dashboard feedback" };
  text("lcd-status", lcdStatus[state.feedback.lcd_status] || "Dashboard feedback");
  text("lcd-warning", state.feedback.warning || "");
  $("scan-once").disabled = !active || busy; $("scan-loop").disabled = !active || busy;
  $("stop-job").disabled = !busy; $("device-test").disabled = busy;
  $("student-form").querySelectorAll("input,button").forEach(e => e.disabled = !!active || busy);
  $("session-form").querySelector("button").disabled = busy || !state.students.length;
  text("camera-title", busy ? "Verification in progress" : "Ready to verify");
  const signature = JSON.stringify(state.students);
  if (signature !== studentSignature) {
    studentSignature = signature;
    fillSelect("sim-face", state.students, "No face / failed check");
    fillSelect("sim-finger", state.students, "No finger / failed check");
    const select = $("class-name"), old = select.value; select.replaceChildren();
    const classes = [...new Set(state.students.map(s => s.class_name))];
    if (!classes.length) select.add(new Option("Register a class first", ""));
    classes.forEach(c => select.add(new Option(c, c)));
    if (classes.includes(old)) select.value = old;
  }
  table("register-body", state.report, 5, (tr, r) => {
    tr.append(cell(r.name), cell(r.roll)); const status = cell(""); status.append(badge(r.status, r.status === "Present"));
    tr.append(status, cell(timestamp(r.recorded_at)), cell(r.method || "Waiting for both checks"));
  });
  table("students-body", state.students, 5, (tr, s) => {
    const name = cell(""), strong = document.createElement("span"), roll = document.createElement("span");
    strong.className = "student-name"; strong.textContent = s.name; roll.className = "roll-sub"; roll.textContent = s.roll; name.append(strong, roll);
    const face = cell(""), finger = cell("");
    const available = s.face_status === "ready" || s.face_status === "simulated";
    face.append(badge(available ? (s.face_status === "simulated" ? "Simulated" : "Saved") : s.face_status === "unavailable" ? "Unavailable" : "Needed", available, s.face_status === "unavailable"));
    if (s.face_samples) { const samples = document.createElement("div"); samples.className = "roll-sub"; samples.textContent = `${s.face_samples} samples`; face.append(samples); }
    if (s.face_error) { const detail = document.createElement("div"); detail.className = "small error"; detail.textContent = s.face_error; face.append(detail); }
    finger.append(badge(s.fingerprint_id !== null ? "Enrolled" : "Needed", s.fingerprint_id !== null));
    const actions = cell(""), wrapper = document.createElement("div"); wrapper.className = "row-actions";
    ["face", "fingerprint"].forEach(kind => { const button = document.createElement("button"); button.className = "secondary small-button";
      button.textContent = kind === "face" ? (s.face_ready ? "Recapture face" : "Enroll face") : "Enroll finger";
      button.disabled = busy || !!active || (kind === "fingerprint" && s.fingerprint_id !== null);
      button.addEventListener("click", () => action("/api/jobs", {kind, student_id:s.id})); wrapper.append(button); });
    actions.append(wrapper); tr.append(name, cell(s.class_name), face, finger, actions);
  });
  table("reports-body", state.sessions, 5, (tr, s) => {
    const status = cell(""); status.append(badge(s.active ? "Open" : "Closed", !!s.active));
    const downloads = cell(""), links = document.createElement("div"); links.className = "download-links";
    ["xlsx", "csv"].forEach(format => { const link = document.createElement("a"); link.href = `/api/reports/${s.id}.${format}`; link.textContent = format === "xlsx" ? "Excel ↗" : "CSV ↗"; links.append(link); });
    downloads.append(links); tr.append(cell(s.subject), cell(s.class_name), cell(timestamp(s.started_at)), status, downloads);
  });
  const identity = id => state.students.find(s => s.id === id)?.roll || (id ? `Unknown (${id})` : "—");
  table("activity-body", state.attempts, 5, (tr, r) => {
    const outcome = cell(""); outcome.append(badge(pretty(r.outcome), r.outcome === "accepted", r.outcome !== "accepted"));
    tr.append(cell(timestamp(r.created_at)), cell(r.session_id || "—"), cell(identity(r.face_id)), cell(identity(r.fingerprint_id)), outcome);
  });
}
async function refresh() {
  if (polling) return;
  polling = true;
  try {
    render(await api("/api/state"));
    if (mode !== "simulation" && current.job.status === "running") {
      const response = await fetch("/api/camera.jpg");
      if (response.status === 200) {
        const blob = await response.blob(); if (cameraUrl) URL.revokeObjectURL(cameraUrl);
        cameraUrl = URL.createObjectURL(blob); $("preview").src = cameraUrl; $("preview").hidden = false; $("camera-placeholder").hidden = true;
      }
    } else { $("preview").hidden = true; $("camera-placeholder").hidden = false; }
  } catch (error) { showError(error.message); } finally { polling = false; }
}
function scan(continuous) {
  const payload = { kind: "attendance", continuous };
  if (mode === "simulation") payload.sim_face = Number($("sim-face").value) || null;
  if (mode !== "raspberry_pi") payload.sim_fingerprint = Number($("sim-finger").value) || null;
  action("/api/jobs", payload);
}
$("session-form").addEventListener("submit", async event => { event.preventDefault(); await action("/api/sessions", Object.fromEntries(new FormData(event.target))); });
$("student-form").addEventListener("submit", async event => { event.preventDefault(); if (await action("/api/students", Object.fromEntries(new FormData(event.target)))) event.target.reset(); });
$("close-session").addEventListener("click", () => action("/api/sessions/close", {}));
$("scan-once").addEventListener("click", () => scan(false)); $("scan-loop").addEventListener("click", () => scan(true));
$("stop-job").addEventListener("click", () => action("/api/jobs/cancel", {}));
$("device-test").addEventListener("click", () => action("/api/jobs", {kind:"diagnostics"}));
$("sim-inputs").hidden = mode === "raspberry_pi"; $("sim-face-field").hidden = mode !== "simulation";
text("camera-label", mode === "simulation" ? "SIMULATED CAPTURE" : mode === "laptop" ? "LAPTOP WEBCAM" : "PI CAMERA MODULE");
text("mode-note", mode === "simulation" ? "SIMULATION · Both biometric results are simulated. Use the selectors to test success, mismatch, missing inputs and duplicates. These records are test data." : mode === "laptop" ? "LAPTOP TEST · Face recognition uses this laptop’s webcam. Fingerprints are simulated. These records are test data." : "RASPBERRY PI · Real face and fingerprint checks are required. Prompts and results appear here and on an available I²C LCD.");
if (mode === "simulation") text("enrollment-note", "In simulation mode, enrollment creates test identities. No camera or fingerprint hardware is used.");
if (mode === "laptop") text("enrollment-note", "Face enrollment uses the server laptop’s webcam. Fingerprint enrollment is simulated; enroll real fingerprints again on the Pi.");
refresh(); setInterval(refresh, 700);
