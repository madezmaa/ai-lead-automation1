"use strict";

// Backend location. Resolution order (see demo/config.js):
//   1. window.LEAD_DEMO_CONFIG.apiBaseUrl (non-empty) -> that origin
//   2. <meta name="api-base-url"> (non-empty)         -> that origin
//   3. window.LEAD_DEMO_CONFIG.apiBaseUrl === ""      -> same origin (relative)
//   4. auto: the API served this page itself (:8000) -> same origin;
//      a separate local static server / file://        -> http://localhost:8000;
//      any other host (production)                     -> same origin.
const API_BASE_URL = (() => {
  const cfg = window.LEAD_DEMO_CONFIG || {};
  const metaEl = document.querySelector('meta[name="api-base-url"]');
  const meta = metaEl && typeof metaEl.content === "string" ? metaEl.content : "";
  for (const candidate of [cfg.apiBaseUrl, meta]) {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim().replace(/\/+$/, "");
    }
  }
  if (cfg.apiBaseUrl === "") return "";
  const host = window.location.hostname;
  const isLocal =
    host === "localhost" || host === "127.0.0.1" || host === "" ||
    window.location.protocol === "file:";
  // When the API is the one serving this page (port 8000) use the same origin,
  // so opening the demo as localhost *or* 127.0.0.1 both work. A separate
  // static server (e.g. :5500) or a local file still targets the API on :8000.
  if (isLocal && window.location.port !== "8000") return "http://localhost:8000";
  return "";
})();

const SCENARIOS = {
  hot: {
    name: "John Smith",
    company: "Apex Growth",
    email: "john@apexgrowth.example",
    need: "We need an automated system to qualify our inbound leads and route qualified prospects to our sales team.",
    budget: "$3,000-$5,000",
    timeline: "Within 2 weeks",
  },
  warm: {
    name: "Sarah Miller",
    company: "Northstar Media",
    email: "sarah@northstar.example",
    need: "We are interested in automating lead qualification and CRM updates, but we are still evaluating options.",
    budget: "$1,000-$2,000",
    timeline: "1-2 months",
  },
  cold: {
    name: "David Brown",
    company: "Small Business",
    email: "david@example.com",
    need: "Looking for information about automation.",
    budget: "Unknown",
    timeline: "Unknown",
  },
};

const CLASSIFICATION_BY_DECISION = {
  qualified: { label: "HOT", tone: "hot" },
  nurture: { label: "WARM", tone: "warm" },
  disqualified: { label: "COLD", tone: "cold" },
};

const ACTION_COPY = {
  sales_follow_up: {
    title: "Sales follow-up",
    desc: "The lead cleared the qualified threshold. Assign an owner and reach out now.",
  },
  add_to_nurture: {
    title: "Add to nurture sequence",
    desc: "Not ready to buy yet. Keep the lead warm with automated drip content and re-score later.",
  },
  disqualify: {
    title: "Disqualify",
    desc: "Below the nurture threshold. Stop outreach and archive the lead.",
  },
  manual_review: {
    title: "Manual review",
    desc: "Ambiguous case — a human should confirm the verdict before routing.",
  },
};

const STEPS = 7;
let running = false;

const $ = (selector) => document.querySelector(selector);

function setStep(index, state) {
  const item = document.querySelector(`#stepper li[data-step="${index}"]`);
  if (!item) return;
  item.classList.remove("is-active", "is-done", "is-skipped", "is-failed");
  if (state) item.classList.add(`is-${state}`);
}

function resetSteps() {
  for (let i = 1; i <= STEPS; i += 1) setStep(i, null);
}

function setProgress(text) {
  $("#progress-note").textContent = text;
}

function showBanner(type, html) {
  const banner = $("#banner");
  banner.hidden = false;
  banner.className = `banner banner-${type}`;
  banner.innerHTML = html;
}

function hideBanner() {
  $("#banner").hidden = true;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

function formatTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? String(iso) : date.toLocaleString();
}

// Show a real status transition only when the API reports a genuine change.
// A net-zero move (e.g. re-qualifying a lead that stays "qualified") must not be
// rendered as a fabricated "qualified -> qualified" arrow.
function describeStateTransition(stateFrom, stateTo) {
  const to = typeof stateTo === "string" && stateTo.trim() ? stateTo.trim() : "—";
  const from = typeof stateFrom === "string" ? stateFrom.trim() : "";
  if (!from || from === to) return to;
  return `${from} → ${to}`;
}

// Normalize the deterministic scoring factors returned by the API. Returns an
// empty list (never invented data) when factors are missing or malformed.
function normalizeFactors(rulesOutput) {
  const raw = rulesOutput && Array.isArray(rulesOutput.factors) ? rulesOutput.factors : [];
  return raw
    .filter((factor) => factor && typeof factor === "object")
    .map((factor) => ({
      name:
        typeof factor.name === "string" && factor.name.trim()
          ? factor.name.trim()
          : "Unnamed factor",
      points: Number.isFinite(Number(factor.points)) ? Number(factor.points) : 0,
      detail: typeof factor.detail === "string" ? factor.detail.trim() : "",
    }));
}

async function api(path, options = {}) {
  const { headers, ...rest } = options;
  let response;
  try {
    response = await fetch(API_BASE_URL + path, {
      ...rest,
      headers: { "Content-Type": "application/json", ...(headers || {}) },
    });
  } catch (err) {
    const error = new Error(
      "We couldn't reach the qualification service just now. " +
        "Please check your connection and try again in a moment."
    );
    error.kind = "network";
    throw error;
  }
  let data = null;
  try { data = await response.json(); } catch (err) { data = null; }
  if (!response.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : null;
    const error = new Error(describeHttpError(response.status, data, detail));
    error.kind = "api";
    error.status = response.status;
    error.code = data ? data.code : null;
    error.data = data;
    throw error;
  }
  return data;
}

function describeHttpError(status, data, detail) {
  if (status === 409) {
    if (data && data.code === "duplicate_lead") return "A lead with this email already exists (it will be reused).";
    if (data && data.code === "not_qualified") return "This lead has not been qualified yet, so there is no CRM record to show.";
    if (data && data.code === "invalid_transition") return `Lead state does not allow this operation: ${detail || ""}`;
    return detail || "Conflict (409).";
  }
  if (status === 422) {
    const parts = Array.isArray(data && data.detail)
      ? data.detail.map((item) => `${(item.loc || []).join(".")}: ${item.msg}`)
      : [];
    return parts.length ? `Invalid lead data — ${parts.join("; ")}` : "Invalid lead data (422).";
  }
  if (status === 401 || status === 403) return "The backend rejected this request (authentication).";
  if (status === 404) return detail || "Not found (404).";
  if (status >= 500) return detail || `Backend error (${status}).`;
  return detail || `Request failed (${status}).`;
}

function isUnknown(text) {
  return !text || text.trim().toLowerCase() === "unknown";
}

function parseBudget(text) {
  const matches = (text || "").match(/\d[\d,]*/g);
  if (!matches) return null;
  const numbers = matches.map((raw) => Number(raw.replace(/,/g, ""))).filter((n) => Number.isFinite(n) && n > 0);
  if (!numbers.length) return null;
  return Math.max(...numbers);
}

function composeMessage(need, budget, timeline) {
  const parts = [need.trim()];
  if (!isUnknown(budget)) parts.push(`Budget: ${budget.trim()}`);
  if (!isUnknown(timeline)) parts.push(`Timeline: ${timeline.trim()}`);
  return parts.join("\n\n").replace(/\n\nTimeline:/, "\nTimeline:");
}

function buildLeadPayload() {
  const name = $("#f-name").value.trim();
  const company = $("#f-company").value.trim();
  const email = $("#f-email").value.trim();
  const need = $("#f-need").value.trim();
  const budgetText = $("#f-budget").value.trim();
  const timeline = $("#f-timeline").value.trim();

  const nameParts = name.split(/\s+/).filter(Boolean);
  const payload = {
    email,
    first_name: nameParts[0] || null,
    last_name: nameParts.length > 1 ? nameParts.slice(1).join(" ") : null,
    company: company || null,
    message: composeMessage(need, budgetText, timeline),
    source: "website",
  };
  const budget = parseBudget(budgetText);
  if (budget !== null) payload.budget = budget;
  return payload;
}

function validateForm() {
  let ok = true;
  const checks = [
    ["#f-name", (v) => v.trim().length > 0],
    ["#f-email", (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim())],
    ["#f-need", (v) => v.trim().length > 0],
  ];
  checks.forEach(([selector, test]) => {
    const input = $(selector);
    const valid = test(input.value);
    input.closest(".field").classList.toggle("is-invalid", !valid);
    if (!valid) ok = false;
  });
  return ok;
}

function hideResults() {
  ["#results", "#card-qualify", "#card-action", "#card-crm", "#card-notify", "#card-email"]
    .forEach((selector) => { $(selector).hidden = true; });
}

async function createOrReuseLead(payload) {
  try {
    const created = await api("/api/v1/leads", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return { id: created.id, reused: false };
  } catch (err) {
    if (err.status === 409 && err.code === "duplicate_lead") {
      const list = await api(`/api/v1/leads?q=${encodeURIComponent(payload.email)}&limit=20`);
      const match = (list.items || []).find(
        (item) => item.email.toLowerCase() === payload.email.toLowerCase()
      );
      if (match) return { id: match.id, reused: true };
    }
    throw err;
  }
}

function renderQualification(result) {
  const classification = CLASSIFICATION_BY_DECISION[result.decision] || { label: "—", tone: "cold" };
  const rules = result.rules_output || {};
  const ai = result.ai_output;

  const classBadge = $("#q-classification");
  classBadge.hidden = false;
  classBadge.textContent = classification.label;
  classBadge.className = `badge badge-${classification.tone}`;

  const decisionBadge = $("#q-decision");
  decisionBadge.hidden = false;
  decisionBadge.textContent = result.decision.charAt(0).toUpperCase() + result.decision.slice(1);

  $("#q-score").textContent = result.score;
  const scoreLabel = $("#q-score").parentElement;
  scoreLabel.className = `score-number is-${classification.tone}`;
  const fill = $("#q-score-fill");
  fill.style.width = "0%";
  fill.className = "score-fill";
  requestAnimationFrame(() => {
    fill.style.width = `${result.score}%`;
    fill.classList.add(`is-${classification.tone}`);
  });

  $("#q-reason").textContent = result.reason || "";

  const signals = (ai && ai.signals) || [];
  $("#q-signals").innerHTML = signals
    .map((signal) => `<span class="chip">${escapeHtml(signal)}</span>`)
    .join("");

  $("#q-rules-score").textContent = rules.score ?? "—";
  $("#q-ai-score").textContent = ai ? ai.score : (result.ai_used ? "—" : "not used");

  let engine = "Deterministic rules only";
  if (result.fallback_used) engine = "Rules (AI unavailable — fallback)";
  else if (result.ai_used) engine = result.rules_overrode_ai ? "AI blended · rules overrode AI" : "Rules + AI blended";
  $("#q-engine").textContent = engine;
  $("#q-state").textContent = describeStateTransition(result.state_from, result.state_to);
  $("#q-time").textContent = formatTime(result.created_at);

  const factors = normalizeFactors(rules);
  const factorList = $("#q-factor-list");
  if (factors.length) {
    factorList.innerHTML = factors
      .map((factor) => {
        const { points } = factor;
        const tone = points > 0 ? "pos" : points < 0 ? "neg" : "";
        const sign = points > 0 ? `+${points}` : String(points);
        const detail = factor.detail
          ? `<span class="f-detail">${escapeHtml(factor.detail)}</span>`
          : "";
        return `<li>
          <span class="f-points ${tone}">${escapeHtml(sign)}</span>
          <span class="f-name">${escapeHtml(factor.name)}</span>
          ${detail}
        </li>`;
      })
      .join("");
    $("#q-factor-note").textContent = `Deterministic factors behind the rules score of ${
      rules.score ?? "—"
    }/100. The final score above blends these rules with the AI assessment.`;
    $("#q-factor-note").hidden = false;
  } else {
    factorList.innerHTML =
      `<li><span class="f-detail">Scoring factors are not available for this result.</span></li>`;
    $("#q-factor-note").hidden = true;
  }

  $("#card-qualify").hidden = false;
}

function renderAction(result) {
  const copy = ACTION_COPY[result.recommended_action] || {
    title: result.recommended_action || "—",
    desc: "",
  };
  const classification = CLASSIFICATION_BY_DECISION[result.decision] || { tone: "cold" };
  const box = $("#a-box");
  box.className = `action-box is-${classification.tone}`;
  $("#a-title").textContent = copy.title;
  $("#a-desc").textContent = copy.desc;
  $("#card-action").hidden = false;
}

function renderCrm(payload) {
  const record = payload.record || {};
  const contact = record.contact || {};
  const company = record.company || {};
  const qualification = record.qualification || {};
  const statusClass = `pill-${record.lead_status || "new"}`;

  const row = (label, value) =>
    (value === null || value === undefined || value === ""
      ? ""
      : `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`);

  $("#crm-content").innerHTML = `
    <div class="crm-status">
      <span class="pill ${statusClass}">${escapeHtml(record.lead_status || "—")}</span>
      <span class="mono muted">${escapeHtml(record.external_id || "")}</span>
    </div>
    <div class="crm-grid">
      <section>
        <h4>Contact</h4>
        <dl>
          ${row("Name", [contact.first_name, contact.last_name].filter(Boolean).join(" "))}
          ${row("Email", contact.email)}
          ${row("Phone", contact.phone)}
          ${row("Title", contact.job_title)}
        </dl>
      </section>
      <section>
        <h4>Company</h4>
        <dl>
          ${row("Name", company.name)}
          ${row("Website", company.website)}
          ${row("Industry", company.industry)}
          ${row("Country", company.country_code || company.country)}
          ${row("Employees", company.employee_count)}
          ${row("Budget", company.declared_budget ? `$${Number(company.declared_budget).toLocaleString()}` : null)}
        </dl>
      </section>
      <section>
        <h4>Qualification</h4>
        <dl>
          ${row("Decision", qualification.decision)}
          ${row("Score", qualification.score)}
          ${row("Action", qualification.recommended_action)}
          ${row("Engine", qualification.ai_used ? (qualification.model || "AI") : "rules only")}
          ${row("Qualified", formatTime(qualification.qualified_at))}
        </dl>
      </section>
    </div>
    ${record.notes ? `<p class="crm-notes">${escapeHtml(record.notes)}</p>` : ""}`;
  $("#card-crm").hidden = false;
}

function renderCrmSkipped(message) {
  $("#crm-content").innerHTML = `
    <div class="state-box">
      <strong>Skipped — no CRM record for this lead</strong>
      ${escapeHtml(message)}
      The record is built from the lead&rsquo;s latest qualification, so it only
      exists once the lead has been qualified. Run the qualification step first.
    </div>`;
  $("#card-crm").hidden = false;
}

function renderNotifications(list) {
  const items = list.items || [];
  if (!items.length) {
    $("#notify-content").innerHTML = `
      <div class="state-box">
        <strong>No notifications yet</strong>
        A webhook entry is written on every qualification; run the demo again.
      </div>`;
    $("#card-notify").hidden = false;
    return;
  }
  const rows = items.map((item) => {
    const statusClass = `status-${item.status}`;
    const target = item.target
      ? `<code>${escapeHtml(item.target)}</code>`
      : `<span class="muted">not configured</span>`;
    const error = item.error ? `<div class="muted">${escapeHtml(item.error)}</div>` : "";
    return `<tr>
      <td><code>${escapeHtml(item.event)}</code></td>
      <td>${escapeHtml(item.channel)}</td>
      <td>${target}</td>
      <td><span class="status-pill ${statusClass}">${escapeHtml(item.status)}</span>${error}</td>
      <td>${escapeHtml(formatTime(item.created_at))}</td>
    </tr>`;
  }).join("");

  const skipped = items.every((item) => item.status === "skipped");
  const note = skipped
    ? `<div class="state-box" style="margin-bottom:14px">
         <strong>Sales alert logged</strong>
         Every qualification writes a sales alert entry. This one was recorded
         without delivery because no webhook endpoint is configured yet &mdash;
         connect one and the entry will show <code>delivered</code> or
         <code>failed</code>.
       </div>`
    : "";

  $("#notify-content").innerHTML = `
    ${note}
    <div class="table-scroll">
      <table class="note-table">
        <thead>
          <tr><th>Event</th><th>Channel</th><th>Target</th><th>Status</th><th>Time</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  $("#card-notify").hidden = false;
}

function renderEmailResponse(list) {
  const items = (list && list.items) || [];
  const row = items.find((item) => item.channel === "email");
  const content = $("#email-content");

  if (!row) {
    content.innerHTML = `
      <div class="state-box">
        <strong>No reply sent</strong>
        The speed-to-lead email only goes out for qualified (HOT) leads and is
        never sent to opted-out leads. This lead did not qualify, so no email
        was attempted.
      </div>`;
    $("#card-email").hidden = false;
    return { status: "none", note: "No reply needed for this lead.", banner: "" };
  }

  const statusClass = `status-${row.status}`;
  const detail = row.error ? `<div class="muted">${escapeHtml(row.error)}</div>` : "";
  content.innerHTML = `
    <dl class="meta-grid">
      <div><dt>Recipient</dt><dd><code>${escapeHtml(row.target || "-")}</code></dd></div>
      <div><dt>Event</dt><dd><code>${escapeHtml(row.event)}</code></dd></div>
      <div><dt>Logged</dt><dd>${escapeHtml(formatTime(row.created_at))}</dd></div>
      <div><dt>Status</dt><dd><span class="status-pill ${statusClass}">${escapeHtml(row.status)}</span></dd></div>
    </dl>
    ${detail}`;
  $("#card-email").hidden = false;

  const labels = {
    delivered: { banner: "delivered", note: `Reply delivered to ${row.target}.` },
    skipped: { banner: "prepared (dry run)", note: `Reply prepared for ${row.target} — dry run, so nothing was sent.` },
    failed: { banner: "failed", note: `Reply failed for ${row.target}.` },
  };
  const copy = labels[row.status] || { banner: row.status, note: `Reply ${row.status}.` };
  return { status: row.status, note: copy.note, banner: `Speed-to-lead reply <strong>${escapeHtml(copy.banner)}</strong>.` };
}

function markStepDone(index, note) {
  setStep(index, "done");
  if (note) setProgress(note);
}

async function runDemo() {
  if (running) return;
  hideBanner();

  if (!validateForm()) {
    showBanner("error", "Please fill in <strong>name</strong>, a valid <strong>email</strong> and the <strong>need</strong> before running.");
    return;
  }

  running = true;
  const button = $("#btn-run");
  button.disabled = true;
  button.classList.add("is-loading");
  resetSteps();
  hideResults();
  $("#results").hidden = false;

  try {
    const payload = buildLeadPayload();

    setStep(1, "active");
    setProgress("Creating the lead …");
    const lead = await createOrReuseLead(payload);
    markStepDone(1, lead.reused ? "Existing lead reused for this email." : "Lead created.");

    setStep(2, "active");
    setProgress("Running AI qualification (the first run can take up to a minute) …");
    const qualification = await api(`/api/v1/leads/${lead.id}/qualify`, {
      method: "POST",
      body: JSON.stringify({ use_ai: true }),
    });
    markStepDone(2, "Qualification complete.");

    setStep(3, "active");
    renderQualification(qualification);
    markStepDone(3);

    setStep(4, "active");
    renderAction(qualification);
    markStepDone(4, "Recommended action ready.");

    setStep(5, "active");
    setProgress("Building the CRM record …");
    try {
      const crm = await api(`/api/v1/leads/${lead.id}/crm-record`);
      renderCrm(crm);
      markStepDone(5, "CRM record built.");
    } catch (err) {
      if (err.status === 409 && err.code === "not_qualified") {
        setStep(5, "skipped");
        renderCrmSkipped(err.message);
        setProgress("CRM record skipped — this lead has not been qualified yet.");
      } else {
        throw err;
      }
    }

    setStep(6, "active");
    setProgress("Checking the sales alert log …");
    const notifications = await api(`/api/v1/leads/${lead.id}/notifications`);
    renderNotifications(notifications);
    markStepDone(6, "Sales alert recorded.");

    setStep(7, "active");
    setProgress("Checking the speed-to-lead reply …");
    const reply = renderEmailResponse(notifications);
    markStepDone(7, "Done — all stages completed.");

    const tone = CLASSIFICATION_BY_DECISION[qualification.decision] || { label: "?" };
    showBanner(
      "success",
      `Qualified with score <strong>${qualification.score}/100</strong> → classified <strong>${tone.label}</strong> → action <strong>${escapeHtml((ACTION_COPY[qualification.recommended_action] || {}).title || qualification.recommended_action)}</strong>. ${reply.banner}`
    );
  } catch (err) {
    const active = document.querySelector("#stepper li.is-active");
    if (active) setStep(Number(active.dataset.step), "failed");
    showBanner("error", err.html ? err.message : escapeHtml(err.message || String(err)));
    setProgress("Run failed — see the message above.");
  } finally {
    running = false;
    button.disabled = false;
    button.classList.remove("is-loading");
  }
}

function loadScenario(key) {
  const scenario = SCENARIOS[key];
  if (!scenario) return;
  $("#f-name").value = scenario.name;
  $("#f-company").value = scenario.company;
  $("#f-email").value = scenario.email;
  $("#f-need").value = scenario.need;
  $("#f-budget").value = scenario.budget;
  $("#f-timeline").value = scenario.timeline;
  document.querySelectorAll(".scenario").forEach((button) => {
    button.classList.toggle("is-selected", button.dataset.scenario === key);
  });
  document.querySelectorAll(".field").forEach((field) => field.classList.remove("is-invalid"));
  hideBanner();
  setProgress(`Sample loaded: ${scenario.company}. Press “Run AI Qualification”.`);
}

function resetDemo() {
  $("#lead-form").reset();
  document.querySelectorAll(".scenario").forEach((button) => button.classList.remove("is-selected"));
  document.querySelectorAll(".field").forEach((field) => field.classList.remove("is-invalid"));
  hideResults();
  hideBanner();
  resetSteps();
  setProgress(
    "Runs the complete flow in a few seconds. Emails stay in dry-run mode — nothing is sent to a real customer."
  );
}

function init() {
  document.querySelectorAll(".scenario").forEach((button) => {
    button.addEventListener("click", () => loadScenario(button.dataset.scenario));
  });
  $("#btn-run").addEventListener("click", runDemo);
  $("#btn-reset").addEventListener("click", resetDemo);
  $("#lead-form").addEventListener("submit", (event) => {
    event.preventDefault();
    runDemo();
  });
}

document.addEventListener("DOMContentLoaded", init);
