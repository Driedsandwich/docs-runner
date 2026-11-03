"use strict";
const $ = id => document.getElementById(id);
const mobile = matchMedia("(max-width: 760px)");
const names = {passed: "Passed", failed: "Failed", needs_credential: "Needs credential", no_expectation: "No expectation", unsupported: "Unsupported", pending: "Pending", running: "Running"};
const symbols = {passed: "✓", failed: "×", needs_credential: "⚿", no_expectation: "?", unsupported: "╱", pending: "○", running: "◌"};
const eventNames = {blocked_network: "Blocked network", killed_by_limit: "Killed by limit", injection_detected: "Injection detected", read_only_root: "Root read-only"};
const fixtureURL = "https://example.org/quickstart";
let fixture = "stale", job = null, selected = null, selectionMade = false, stream = null, nextStream = null;
let busy = false, submitting = false, stopRequested = false, revision = -1, generation = 0;
let menuOpen = false, menuTrigger = null, eventIds = new Set(), evidenceKey = "", nextJobID = null, nextJob = null;
let outputText = "", outputWrapped = true, refreshing = false, refreshAgain = false;
function node(tag, text = "", className = "") { const e = document.createElement(tag); e.textContent = text; if (className) e.className = className; return e; }
function button(text, action, className = "") { const b = node("button", text, className); b.type = "button"; b.addEventListener("click", action); return b; }
function time(value) { return value ? new Date(value * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC") : "—"; }
function runLabel(value) { return value.presentation?.label || value.id; }
function statusElement(status) { const span = node("span", "", "status"); const icon = node("span", symbols[status] || "○", "status-icon");
  if (status === "needs_credential") {
    icon.textContent = "";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("class", "key-icon");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M14 3a5 5 0 1 1-3 9L3 20l-2-2 2-2-1-1 3-3 1 1 3-3a5 5 0 0 1 5-7Z");
    svg.append(path); icon.append(svg);
  } icon.setAttribute("aria-hidden", "true"); span.append(icon, node("span", names[status] || status)); return span; }
function tag(text, accent = false) { return node("span", text, `tag${accent ? " accent" : ""}`); }
function limitsText(p) { const l = p.limits; return l ? `${l.cpus} CPU / ${l.memory_mib} MiB / ${l.seconds} s / ${l.pids} PIDs` : "Limits not reported"; }
function notice(message, reconnect = false) { $("notice").hidden = !message; $("notice-text").textContent = message || ""; $("retry-connection").hidden = !reconnect; }
function announce(message) { if ($("live-status").textContent !== message) $("live-status").textContent = message; }
function updateRunButton() {
  $("run-label").textContent = busy ? (stopRequested ? "Stopping…" : "Stop") : (job ? "Re-run" : "Run audit");
  $("run").disabled = busy && stopRequested;
  $("url").disabled = busy;
  document.querySelectorAll("[data-demo]").forEach(b => { b.disabled = busy; b.setAttribute("aria-pressed", String(b.dataset.demo === fixture)); });
}
function setFixture(value) {
  fixture = value; $("url").value = fixtureURL;
  $("mode-note").textContent = "Fixture playback. No commands or external requests are executed.";
  updateRunButton();
}
document.querySelectorAll("[data-demo]").forEach(b => b.addEventListener("click", () => setFixture(b.dataset.demo)));
$("url").addEventListener("input", () => {
  fixture = null;
  $("mode-note").textContent = "Live audit. This URL uses the configured backend runner; its actual limits and evidence will be shown.";
  updateRunButton();
});
function counts(value) {
  const steps = value.steps || [], total = value.presentation?.plan?.length ?? steps.length;
  const passed = steps.filter(s => s.status === "passed").length;
  const failed = steps.filter(s => s.status === "failed").length;
  return {passed, failed, unverified: steps.length - passed - failed, verified: passed + failed, total, pending: total - steps.length};
}
function renderResults(value) {
  const c = counts(value), scored = c.verified > 0, score = scored ? Math.round(100 * c.passed / c.verified) : "—";
  $("summary-line").replaceChildren(node("span", `${c.passed} passed · ${c.failed} failed · ${c.unverified} unverified${c.pending ? ` · ${c.pending} pending` : ""}  |  `), node("strong", `Doc health ${score}${scored ? "/100" : " · Not scored"}`), node("span", ` · ${c.passed} of ${c.verified} verified · Coverage ${c.verified}/${c.total}`));
  $("result-counts").replaceChildren(...[["✓", c.passed, "passed"], ["×", c.failed, "failed"], ["⊖", c.unverified, "unverified"]].map(([symbol, n, label]) => node("span", `${symbol} ${n} ${label}`)));
  $("score").textContent = `Doc health: ${score}${scored ? "/100" : ""}`;
  $("verified").textContent = `${scored ? "" : "Not scored · "}${c.passed} of ${c.verified} verified`;
  $("coverage").textContent = `Coverage: ${c.verified}/${c.total}${c.pending ? ` · ${c.pending} pending` : ""}`;
  $("results-title").textContent = `${runLabel(value)} RESULTS (${value.phase === "finished" ? value.state : "in progress"})`;
}
function plannedSteps(value) { return value.presentation?.plan || value.steps.map(r => ({id: r.step.id, title: `Step ${String(r.step.id).padStart(2, "0")}`, command: r.step.command})); }
function renderSteps(value) {
  const plan = plannedSteps(value), active = Number(value.phase.split(":")[1]);
  $("step-count").textContent = `(${plan.length})`;
  $("empty-steps").hidden = !!plan.length;
  $("empty-steps").firstChild.textContent = busy ? "Extracting documented steps…" : "Your document’s steps will enter here.";
  const existing = new Map([...$("steps").children].map(li => [Number(li.dataset.step), li]));
  for (const item of plan) {
    let li = existing.get(item.id);
    if (!li) {
      li = node("li"); li.dataset.step = item.id;
      const b = button("", () => { selected = item.id; selectionMade = true; renderSteps(job); renderEvidence(job); }, "step-button");
      b.append(node("span", String(item.id).padStart(2, "0"), "step-number"), node("span"));
      li.append(b); $("steps").append(li);
    }
    const b = li.firstChild, result = value.steps.find(s => s.step.id === item.id);
    const status = result?.status || (active === item.id && busy ? "running" : "pending");
    const signature = `${item.title}|${status}`;
    if (b.dataset.signature !== signature) {
      const state = statusElement(status); state.className = "step-status";
      b.lastChild.replaceChildren(node("span", item.title, "step-name"), state);
      b.dataset.signature = signature;
    }
    b.setAttribute("aria-current", String(item.id === selected));
    b.setAttribute("aria-controls", "evidence");
    existing.delete(item.id);
  }
  existing.forEach(li => li.remove());
}
function addMetric(dl, label, value) { dl.append(node("dt", label), node("dd", String(value ?? "—"))); }
function renderEvidence(value) {
  const p = value.presentation || {}, item = plannedSteps(value).find(s => s.id === selected);
  const result = value.steps.find(s => s.step.id === selected);
  const detail = p.step_details?.[String(selected)] || {};
  const lifecycle = detail.lifecycle || p.lifecycle || (value.cleanup?.not_created ? "not_created" : value.phase === "cleaning_up" ? "destroying" : value.cleanup?.sandbox_removed ? "destroyed" : value.runtime ? "isolated" : "creating");
  const labels = {creating: "Creating", isolated: "Isolated", destroying: "Destroying", destroyed: "Destroyed", not_created: "Not created", unconfirmed: "Not confirmed"};
  $("sandbox").classList.toggle("empty", !item && ["not_created", "unconfirmed"].includes(lifecycle));
  $("sandbox-step").textContent = item ? `STEP ${String(item.id).padStart(2, "0")} — EXPANDED` : (value.phase === "finished" ? "NO ACTIVE STEP" : "PREPARING");
  $("sandbox-labels").replaceChildren(tag(detail.sandbox_id || value.runtime ? "Isolated" : labels[lifecycle] || lifecycle, true), ...(p.no_secrets ? [tag("No secrets")] : []), tag(limitsText(p)));
  $("lifecycle").textContent = labels[lifecycle] || lifecycle;
  $("lifecycle").classList.toggle("accent", lifecycle === "destroyed");
  $("lifecycle").parentElement.parentElement.classList.toggle("busy", ["creating", "destroying"].includes(lifecycle));
  $("destroyed-time").textContent = lifecycle === "destroyed" ? time(detail.destroyed_at || p.destroyed_at) : "";
  const signature = JSON.stringify([selected, item, result, detail, p.isolation, !result ? value.phase : null]);
  if (signature === evidenceKey) return;
  evidenceKey = signature;
  const panel = node("article", "", "evidence-panel");
  if (!item) {
    const empty = node("div", "", "empty-evidence");
    empty.append(node("span", value.phase === "finished" ? "◇" : "◌"), node("h3", value.phase === "finished" ? "No executed steps" : "Creating the boundary…"), node("p", value.phase === "finished" ? "No execution evidence was returned for this job." : "Waiting for the API to report extracted steps and sandbox evidence."));
    $("evidence").replaceChildren(empty); return;
  }
  const status = result?.status || (Number(value.phase.split(":")[1]) === item.id ? "running" : "pending");
  const header = node("header", "", "evidence-heading");
  header.append(node("h3", `Step ${String(item.id).padStart(2, "0")}: ${item.title}`), statusElement(status));
  const body = node("div", "", "evidence-body"), metrics = node("dl", "", "evidence-metrics");
  const evidence = result?.attempts?.[0]?.evidence;
  addMetric(metrics, "Command", evidence?.command || item.command);
  addMetric(metrics, "Exit", evidence ? evidence.exit_code ?? "—" : "Not executed");
  addMetric(metrics, "Elapsed", evidence ? `${evidence.duration_seconds} s` : "Not executed");
  addMetric(metrics, "Limit hit", evidence ? evidence.limits?.join(", ") || "none" : "—");
  body.append(metrics);
  const outputs = [];
  if (evidence) {
    if (evidence.stdout || !evidence.stderr) outputs.push(["Stdout tail", evidence.stdout || "(empty)"]);
    if (evidence.stderr) outputs.push(["Stderr tail", evidence.stderr]);
  } else outputs.push(["Output", result ? "Not executed" : "Waiting for execution evidence"]);
  for (const [label, text] of outputs) {
    body.append(node("p", label, "output-label"));
    const pre = node("pre", text, `output-tail${outputWrapped ? "" : " nowrap"}`); pre.tabIndex = 0; pre.setAttribute("aria-label", label); body.append(pre);
  }
  outputText = evidence ? `Command: ${evidence.command}\n\nstdout tail:\n${evidence.stdout}\n\nstderr tail:\n${evidence.stderr}` : "Not executed";
  if (evidence) {
    const tools = node("div", "", "output-tools");
    tools.append(button("Copy output", async e => {
      const control = e.currentTarget;
      try { await navigator.clipboard.writeText(outputText); control.textContent = "Copied"; announce("Output copied."); }
      catch { announce("Copy unavailable. Open captured output and select the text to copy."); }
    }), button(outputWrapped ? "Wrap: on" : "Wrap: off", e => {
      outputWrapped = !outputWrapped; e.currentTarget.textContent = outputWrapped ? "Wrap: on" : "Wrap: off";
      e.currentTarget.setAttribute("aria-pressed", String(outputWrapped));
      $("evidence").querySelectorAll(".output-tail").forEach(pre => pre.classList.toggle("nowrap", !outputWrapped));
    }), button("Open output ↗", () => { $("full-output").textContent = outputText; $("output-dialog").showModal(); }));
    tools.children[1].setAttribute("aria-pressed", String(outputWrapped)); body.append(tools);
  }
  if (result?.diagnosis?.suggested_command) {
    const suggestion = node("div", "", "suggestion");
    suggestion.append(node("h4", "Proposed fix"), node("code", `$ ${result.diagnosis.suggested_command}`), node("p", result.attempts.length > 1 ? "Original evidence preserved · see separate retry below" : "Unverified suggestion · Not applied", "suggestion-label"));
    body.append(suggestion);
  }
  const details = node("details", "", "evidence-details");
  details.append(node("summary", "Evidence provenance & all attempts"));
  const dl = node("dl");
  addMetric(dl, "Run ID", value.id); addMetric(dl, "Step ID", item.id);
  addMetric(dl, "Sandbox ID", detail.sandbox_id || (value.runtime ? `docsrunner-job-${value.id}` : "Not created"));
  addMetric(dl, "Source line", detail.source_line ?? item.source_line ?? "Not reported");
  addMetric(dl, "Isolation", p.isolation || "Not reported");
  addMetric(dl, "Preparation", detail.preparation || "Not reported");
  addMetric(dl, "Fixture", detail.fixture || "Not reported");
  addMetric(dl, "Stdout tail", evidence ? evidence.stdout || "(empty)" : "Not executed");
  addMetric(dl, "Stderr tail", evidence ? evidence.stderr || "(empty)" : "Not executed");
  details.append(dl, node("p", result?.reason || "No result yet."));
  if (result?.step.expectations.length) details.append(node("p", "Documented expectations"), node("pre", JSON.stringify(result.step.expectations, null, 2)));
  if (result?.diagnosis) details.append(node("p", result.diagnosis.explanation));
  for (const [i, attempt] of (result?.attempts || []).entries()) {
    const block = node("div", "", "attempt"); block.append(node("p", `${i ? "Separate retry" : "Original attempt"}: ${names[attempt.status]}`), node("pre", JSON.stringify(attempt.evidence, null, 2))); details.append(block);
  }
  body.append(details); panel.append(header, body);
  const previousDetails = $("evidence").querySelector(".evidence-details");
  if (previousDetails?.open && $("evidence").dataset.step === String(selected)) details.open = true;
  // Keep keyboard focus on the equivalent control if a selected step receives new evidence.
  const focused = $("evidence").contains(document.activeElement) ? document.activeElement.textContent : null;
  $("evidence").replaceChildren(panel); $("evidence").dataset.step = selected;
  if (focused) [...panel.querySelectorAll("button,summary")].find(el => el.textContent === focused)?.focus({preventScroll: true});
}
function renderEvents(value) {
  const p = value.presentation || {}, events = p.containment_events || [];
  $("events-heading").textContent = p.probe_label ? `Hostile probe ${p.probe_label}` : "Containment pathways";
  $("events-description").textContent = `Containment events · ${runLabel(value)}. Separate from the doc-health score.${p.probe_label ? " Injection is stopped at the extraction boundary, on the left." : ""}`;
  let added = false;
  for (const event of events) {
    if (eventIds.has(event.id)) continue;
    eventIds.add(event.id); added = true;
    const entry = node("article", "", "containment-event"); entry.dataset.event = event.kind;
    const title = node("div", "", "event-title");
    title.append(node("span", String(events.indexOf(event) + 1), "event-number"), node("h3", eventNames[event.kind] || event.kind));
    entry.append(title, node("p", event.detail, "event-description"), node("p", `${runLabel(value)} · ${time(event.observed_at).slice(11, 19)} UTC`, "event-run"));
    const details = node("details"); details.append(node("summary", "View boundary evidence")); const dl = node("dl");
    addMetric(dl, "Run ID", event.run_id); addMetric(dl, "Probe ID", event.probe_id); addMetric(dl, "Boundary", event.boundary === "extraction" ? "Extraction trust boundary (not container detection)" : "Runtime sandbox boundary");
    addMetric(dl, "Sandbox ID", event.sandbox_id || "Not created — extraction boundary"); addMetric(dl, "Observed", time(event.observed_at));
    for (const [key, val] of Object.entries(event.evidence || {})) addMetric(dl, key.replaceAll("_", " "), val);
    details.append(dl); entry.append(details);
    if (event.boundary === "extraction") {
      if (!$("extraction-events").children.length) $("extraction-events").append(node("h3", "Extraction trust boundary", "extraction-heading"));
      $("extraction-events").append(entry);
    } else $("runtime-events").append(entry);
  }
  // Existing real jobs expose only these observations; do not invent missing probe evidence.
  if (!value.fixture && value.injection?.injection_detected && !eventIds.has("legacy-injection")) {
    eventIds.add("legacy-injection");
    $("extraction-events").append(node("h3", "Injection detected · extraction boundary", "extraction-heading"), node("p", value.injection.reason, "event-description"));
  }
  $("empty-events").hidden = events.some(e => e.boundary === "runtime");
  if (!value.fixture && value.cleanup?.denied_destinations?.length) {
    $("empty-events").textContent = `Blocked destinations reported at cleanup: ${value.cleanup.denied_destinations.join(", ")}. Event timestamps were not reported.`;
  } else $("empty-events").textContent = busy ? "Waiting for observed runtime events…" : "No runtime containment events observed.";
  if (added) {
    $("sandbox").classList.remove("pulse"); void $("sandbox").offsetWidth; $("sandbox").classList.add("pulse");
    announce(`${events.length} containment event${events.length === 1 ? "" : "s"} observed. ${eventNames[events.at(-1).kind] || ""}.`);
  }
}
function showNext(value) {
  nextJob = value;
  const p = value.presentation || {}, target = $("next-job"); target.replaceChildren();
  target.append(node("h2", `Next job ${runLabel(value)}: ${value.state}.`));
  target.append(node("p", `Source: ${value.url}`), node("p", `Started: ${time(value.created_at)}`));
  const boundary = node("div", "", "next-sandbox");
  boundary.append(tag(p.lifecycle === "destroyed" ? "Destroyed" : p.lifecycle === "isolated" ? "Isolated" : "Creating", true), node("p", `Sandbox: ${p.sandbox_id || "Awaiting creation"}`), node("p", `Limits: ${limitsText(p)}`)); target.append(boundary);
  target.append(node("p", "Independent fixture job observed; not a general availability guarantee.", "muted"));
}
async function trackNext(id) {
  if (!id || id === nextJobID) return;
  nextJobID = id; const token = generation;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(id)}`); if (!response.ok) throw new Error("unavailable");
    const value = await response.json(); if (token !== generation) return; showNext(value);
    if (value.phase === "finished") return;
    let latest = value.revision ?? -1;
    nextStream?.close(); nextStream = new EventSource(`/api/jobs/${encodeURIComponent(id)}/events`);
    nextStream.addEventListener("progress", e => {
      if (token !== generation) return;
      const update = JSON.parse(e.data), snapshot = update.snapshot;
      if (snapshot && Number(e.lastEventId) > latest) { latest = Number(e.lastEventId); showNext(snapshot); }
      if (update.phase === "finished") nextStream?.close();
    });
    nextStream.onerror = () => { if (token === generation && nextJob?.phase !== "finished") announce("Next-job connection interrupted; reconnecting."); };
  } catch { if (token === generation) $("next-job").replaceChildren(node("h2", "Next job unavailable."), node("p", "Its current state could not be retrieved. No continuity claim is made.", "muted")); }
}
function render(value, force = false) {
  if (!force && value.revision !== undefined && value.revision < revision) return;
  revision = Math.max(revision, value.revision ?? 0); job = value;
  busy = value.phase !== "finished"; submitting = false; updateRunButton();
  const p = value.presentation || {}, plan = plannedSteps(value);
  if (!selectionMade) {
    const failed = value.steps.find(s => s.status === "failed");
    selected = failed?.step.id || (plan.find(s => s.id === Number(value.phase.split(":")[1]))?.id) || selected || plan[0]?.id;
  }
  $("headline").textContent = value.error ? (value.state === "cancelled" ? "Stopped. Evidence preserved." : "The audit needs attention.") : value.fixture && (p.containment_events?.length || value.phase === "finished") ? "The boundary holds." : "Tracing the boundary.";
  $("run-context").textContent = `${value.fixture ? "DEMO FIXTURE" : "LIVE AUDIT"} · ${runLabel(value)}`;
  $("observed-time").textContent = time(p.destroyed_at || value.created_at);
  $("source-line").textContent = `Source: ${value.url}  |  ${plan.length} extracted steps`;
  $("fixture-disclosure").textContent = value.fixture ? `DEMO FIXTURE · ${value.fixture} · Simulated evidence. No network access, LLM calls, or command execution.` : "LIVE AUDIT · Evidence and execution limits reported by the configured backend.";
  const l = p.limits;
  for (const [id, val] of Object.entries({"limit-cpu": l?.cpus, "limit-memory": l ? `${l.memory_mib} MiB` : null, "limit-time": l ? `${l.seconds} s` : null, "limit-pids": l?.pids})) $(id).textContent = val ?? "—";
  notice(value.error || ""); renderResults(value); renderSteps(value); renderEvidence(value); renderEvents(value);
  announce(`${runLabel(value)}: ${value.phase.replaceAll("_", " ")}. ${value.steps.length} steps with results.`);
  if (p.next_job_id) trackNext(p.next_job_id);
  else if (!busy) $("next-job").replaceChildren(node("h2", "No next job observed."), node("p", "Run another audit to observe a new, independent job.", "muted"));
  if (!busy) { stream?.close(); stopRequested = false; updateRunButton(); }
}
async function fetchJob(id, token) {
  const response = await fetch(`/api/jobs/${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error(response.status === 404 ? "Job not found or expired. Run a new audit." : "Could not retrieve the audit. Reconnect to retry.");
  const value = await response.json(); if (token === generation) render(value);
}
async function refresh(id, token) {
  if (refreshing) { refreshAgain = true; return; }
  refreshing = true;
  try { do { refreshAgain = false; await fetchJob(id, token); } while (refreshAgain && token === generation); }
  catch (error) { if (token === generation) notice(error.message, true); }
  finally { refreshing = false; }
}
function connect(id, token) {
  stream?.close(); stream = new EventSource(`/api/jobs/${encodeURIComponent(id)}/events`);
  stream.addEventListener("progress", e => {
    if (token !== generation) return;
    try {
      const event = JSON.parse(e.data);
      if (event.snapshot) { if (Number(e.lastEventId) > revision) render(event.snapshot); }
      else refresh(id, token);
    } catch { notice("An update could not be read. Reconnect to retrieve the current audit.", true); }
  });
  stream.onerror = () => { if (token === generation && busy) notice("Connection interrupted; reconnecting. The job may still be running.", true); };
  stream.onopen = () => { if (token === generation) { notice(""); refresh(id, token); } };
}
async function stop() {
  if (stopRequested) return;
  stopRequested = true; updateRunButton();
  if (submitting) { announce("Stop requested. Waiting for the job ID."); return; }
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(job.id)}/stop`, {method: "POST"});
    if (!response.ok) throw new Error("Could not stop the job. Try Stop again.");
    announce("Stop requested; waiting for cleanup confirmation.");
    await refresh(job.id, generation);
  } catch (e) { stopRequested = false; updateRunButton(); notice(e.message, true); }
}
async function start() {
  const previousJob = job;
  let rejected = false;
  const token = ++generation;
  stream?.close(); nextStream?.close(); revision = -1; busy = true; submitting = true; stopRequested = false;
  selectionMade = false; selected = null; evidenceKey = ""; eventIds = new Set(); nextJobID = null; nextJob = null;
  $("steps").replaceChildren(); $("runtime-events").replaceChildren(); $("extraction-events").replaceChildren();
  $("next-job").replaceChildren(node("h2", "One run. Its own boundary."), node("p", "Waiting for an independent follow-up job to be observed.", "muted"));
  const url = $("url").value;
  job = {id: "Submitting", url, fixture, state: "queued", phase: "submitting", steps: [], presentation: {simulated: !!fixture, plan: [], lifecycle: "creating"}};
  render(job, true); submitting = true; announce("Submitting audit."); if (menuOpen) closeMenu();
  try {
    const response = await fetch("/api/jobs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({url, ...(fixture ? {fixture} : {})})});
    const result = await response.json();
    if (!response.ok) {
      rejected = true;
      throw new Error(typeof result.detail === "string" ? result.detail : "The request was rejected. Check the URL and try again.");
    }
    if (token !== generation) return;
    job.id = result.id; submitting = false;
    const shouldStop = stopRequested; stopRequested = false;
    try { await fetchJob(result.id, token); }
    catch { notice("Audit accepted, but its state could not be retrieved. Reconnecting; the job may still be running.", true); }
    if (busy) connect(result.id, token);
    if (shouldStop && busy) await stop();
  } catch (error) {
    if (token !== generation) return;
    busy = false; submitting = false; stopRequested = false; updateRunButton();
    if (previousJob?.steps?.length) {
      revision = -1; eventIds = new Set(); evidenceKey = "";
      render(previousJob, true);
      notice(`New audit could not start: ${error.message} Previous evidence is preserved.`);
    } else {
      job.id = rejected ? "Not submitted" : "Submission unconfirmed";
      job.state = "failed"; job.phase = "finished"; job.error = error.message;
      job.presentation.lifecycle = rejected ? "not_created" : "unconfirmed";
      render(job, true);
      $("headline").textContent = "The audit could not start.";
    }
    announce(error.message);
  }
}
$("job-form").addEventListener("submit", e => { e.preventDefault(); if (busy) stop(); else start(); });
$("retry-connection").addEventListener("click", async () => { if (!job?.id || job.id === "Submitting") return; await refresh(job.id, generation); if (busy) connect(job.id, generation); });
window.addEventListener("offline", () => {
  stream?.close(); nextStream?.close();
  if (busy) notice("Connection interrupted; waiting for the network. The job may still be running.", true);
});
window.addEventListener("online", () => {
  if (busy && job?.id && job.id !== "Submitting") connect(job.id, generation);
  if (nextJobID && nextJob?.phase !== "finished") {
    const id = nextJobID; nextJobID = null; trackNext(id);
  }
});
$("close-output").addEventListener("click", () => $("output-dialog").close());
function placeControls() { (mobile.matches && !menuOpen ? $("mobile-controls") : $("form-slot")).append($("input-controls")); }
function menuFocusables() { return [...$("controls").querySelectorAll("a,button,input")].filter(el => !el.disabled && !el.hidden && el.getClientRects().length); }
function openMenu(trigger) {
  if (!mobile.matches) return;
  menuTrigger = trigger; menuOpen = true; document.body.classList.add("menu-open");
  $("controls").setAttribute("role", "dialog"); $("controls").setAttribute("aria-modal", "true");
  $("scrim").hidden = false; $("close-menu").hidden = false;
  $("main").inert = true; document.querySelector("footer").inert = true; document.querySelector(".rail").inert = true;
  document.querySelectorAll("[data-menu]").forEach(b => b.setAttribute("aria-expanded", "true"));
  placeControls(); $("close-menu").focus();
}
function closeMenu() {
  if (!menuOpen) return;
  menuOpen = false; document.body.classList.remove("menu-open");
  $("controls").removeAttribute("role"); $("controls").removeAttribute("aria-modal"); $("scrim").hidden = true; $("close-menu").hidden = true;
  $("main").inert = false; document.querySelector("footer").inert = false; document.querySelector(".rail").inert = false;
  document.querySelectorAll("[data-menu]").forEach(b => b.setAttribute("aria-expanded", "false")); placeControls(); menuTrigger?.focus();
}
document.querySelectorAll("[data-menu]").forEach(b => b.addEventListener("click", () => openMenu(b)));
$("close-menu").addEventListener("click", closeMenu); $("scrim").addEventListener("click", closeMenu);
$("controls").querySelectorAll(".drawer-nav a").forEach(a => a.addEventListener("click", () => { closeMenu(); const target = document.querySelector(a.getAttribute("href")); target.tabIndex = -1; target.focus(); }));
document.addEventListener("keydown", e => {
  if (!menuOpen) return;
  if (e.key === "Escape") { e.preventDefault(); closeMenu(); }
  if (e.key === "Tab") {
    const focusable = menuFocusables(), first = focusable[0], last = focusable.at(-1);
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});
document.addEventListener("focusin", e => { if (menuOpen && !$("controls").contains(e.target)) $("close-menu").focus(); });
mobile.addEventListener("change", () => { if (!mobile.matches) closeMenu(); placeControls(); });
placeControls(); updateRunButton();
