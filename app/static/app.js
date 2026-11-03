const $ = (id) => document.getElementById(id);
let stream;
function element(tag, text) { const node = document.createElement(tag); node.textContent = text; return node; }
function render(job) {
  $("state").textContent = `${job.state}: ${job.phase}${job.error ? " — " + job.error : ""}`;
  $("injection").textContent = job.injection ? `Injection detected: ${job.injection.injection_detected}. ${job.injection.reason}` : "";
  $("steps").replaceChildren();
  for (const step of job.steps) {
    const section = element("article", "");
    section.append(element("h2", `Step ${step.step.id}: ${step.status}`), element("p", step.reason));
    section.append(element("pre", step.step.command));
    section.append(element("pre", `Documented expectations\n${JSON.stringify(step.step.expectations, null, 2)}`));
    for (const [i, attempt] of step.attempts.entries()) {
      const detail = element("details", ""); detail.open = true;
      detail.append(element("summary", `${i ? "Repair attempt" : "Original attempt"}: ${attempt.status}`));
      detail.append(element("pre", JSON.stringify(attempt.evidence, null, 2))); section.append(detail);
    }
    if (step.diagnosis) section.append(element("pre", JSON.stringify(step.diagnosis, null, 2)));
    $("steps").append(section);
  }
  $("cleanup").textContent = job.cleanup ? `Cleanup and blocked destinations\n${JSON.stringify(job.cleanup, null, 2)}` : "";
  if (job.phase === "finished") { stream?.close(); $("run").disabled = false; }
}
async function start(url) {
  stream?.close(); $("run").disabled = true; $("state").textContent = "Submitting…";
  try {
    const response = await fetch("/api/jobs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({url})});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Request rejected");
    const refresh = async () => {
      const response = await fetch(`/api/jobs/${result.id}`);
      if (!response.ok) throw new Error("Job unavailable");
      render(await response.json());
    };
    stream = new EventSource(`/api/jobs/${result.id}/events`);
    stream.addEventListener("progress", () => refresh().catch((e) => { $("state").textContent = e.message; }));
    stream.onerror = () => { $("state").textContent = "Connection interrupted; reconnecting…"; $("run").disabled = false; };
    await refresh();
  } catch (error) { $("state").textContent = error.message; $("run").disabled = false; }
}
$("job-form").addEventListener("submit", (e) => { e.preventDefault(); start($("url").value); });
document.querySelectorAll("[data-demo]").forEach((button) => button.addEventListener("click", () => {
  $("url").value = `${location.origin}/demo-docs/${button.dataset.demo}`; start($("url").value);
}));
