/* Night Shift shell, driven by the engine. One session: capture → debrief → work map → teach → real life → results. */
(function () {
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const GATE = { voice_silent: true, hands_still: true, screen_stable: true };
  const HEAD = {
    1: ["Capture · live", "Afternoon wandering"],
    2: ["Map", "Debrief, then the work map"],
    3: ["Teach", "A case the expert never showed"],
    4: ["Real life", "The night shift, scored live"],
    5: ["Results", "This session"]
  };

  let sid = "";
  let tab = 1;
  let form = emptyForm();
  let captureSteps = [];
  let captureAt = 0;
  let openQ = null;
  let debriefQs = [];
  let debriefAt = 0;
  let debriefStarted = false;
  let teachback = null;
  let tbAt = 0;
  let realMoments = [];
  let realAt = 0;
  let realLog = [];
  let titles = {};
  let forms = null;
  let order = [];
  let current = null;
  let phase = "predict";
  let busy = false;
  let teachReady = false;
  let teachBoot = null;
  let teachLog = [];
  let caught = 0;
  let firstRight = 0;
  let seenPredict = {};
  let nightCaught = 0;
  let captureTitle = "";
  let answeredCapture = false;
  let mapReady = false;
  let mapBoot = null;
  let teachbackLoading = false;
  let confirming = false;
  let pendingNext = null;
  let answering = false;
  let realBoot = null;

  function emptyForm() {
    return {
      incident_type: "other", observation: "", interpretation: "none", checks: [],
      occurrences_today: 1, months_in_residence: 0, pattern: "", intervention: "no_action", escalate_to: "none"
    };
  }
  function newSid() { sid = "ns-" + Math.random().toString(36).slice(2, 10); }

  async function api(path, body) {
    const res = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: { "content-type": "application/json", "x-session-id": sid },
      body: body ? JSON.stringify(body) : undefined
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
    if (!res.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === "string" ? detail : res.status + " " + res.statusText);
    }
    return data;
  }

  function pill(text, ok) {
    let el = $("engine-pill");
    if (!el) {
      el = document.createElement("span");
      el.id = "engine-pill";
      el.className = "chip";
      const nav = document.querySelector(".nav-right");
      if (nav) nav.prepend(el);
    }
    el.textContent = text;
    el.dataset.state = ok ? "ok" : "bad";
  }

  function listenReason(reason) {
    const r = String(reason || "");
    if (r.includes("cooldown")) return "Listening. The next question waits a short moment.";
    if (r.includes("no uncertain")) return "Nothing to ask about this choice.";
    if (r.includes("already asked")) return "Already asked about this choice.";
    if (r.includes("fresh") || r.includes("stale")) return "That moment has passed. Keep documenting.";
    if (r.includes("budget")) return "Listening. Enough questions for now.";
    return "Listening";
  }

  let heard = "";
  let heardTimer = null;
  function voiceRole() { return (tab === 3 || tab === 4) ? "tutor" : "interviewer"; }
  function voiceOn() { return $("btn-voice") && $("btn-voice").getAttribute("aria-pressed") === "true"; }
  function speak(text, tag) {
    if (!voiceOn() || !text || !window.ApprenticeVoice) return;
    window.ApprenticeVoice.say(voiceRole(), tag || "SAY", text).catch(() => pill("Voice unavailable", false));
  }
  function takeSpoken(text) {
    const box = $("cap-composer") && !$("cap-composer").hidden ? $("ans")
      : ($("deb-composer") && !$("deb-composer").hidden ? $("dans") : null);
    if (!box || !text) return;
    heard = (heard + " " + text).trim();
    box.value = heard;
    clearTimeout(heardTimer);
    heardTimer = setTimeout(() => {
      const said = heard;
      heard = "";
      if (!said) return;
      if (box === $("ans")) sendAnswer(said).catch(err => addLine($("convo"), "sys", err.message));
      else sendDebrief(said).catch(err => addLine($("dconvo"), "sys", err.message));
    }, 2200);
  }

  function show(n) {
    tab = n;
    if (window.NightShell) window.NightShell.showTab(n);
    else {
      [1, 2, 3, 4, 5].forEach(k => { const el = $("tab" + k); if (el) el.hidden = k !== n; });
      document.querySelectorAll(".tab").forEach(b => b.setAttribute("aria-selected", String(+b.dataset.tab === n)));
      const h = HEAD[n];
      if (h) { $("topic-k").textContent = h[0]; $("topic-t").textContent = h[1]; }
    }
    if (n === 1 && captureTitle) { $("topic-t").textContent = captureTitle; $("topic-t").title = captureTitle; }
    const sub = $("subjects"); if (sub) sub.hidden = true;
    const job = n === 2 ? openMap() : n === 3 ? openTeach() : n === 4 ? openReal() : n === 5 ? openResults() : null;
    if (voiceOn() && window.ApprenticeVoice && window.ApprenticeVoice.role !== voiceRole()) {
      window.ApprenticeVoice.start(voiceRole()).catch(() => pill("Voice unavailable", false));
    }
    Promise.resolve(job).then(() => dock()).catch(() => dock());
  }

  function dock() {
    const btn = $("btn-next");
    const label = $("cta-t");
    const n = $("step-n");
    if (!btn || !label) return;
    const count = btn.querySelector(".cta-s");
    const captureDone = captureAt >= captureSteps.length && captureSteps.length > 0;
    const debriefDone = debriefStarted && debriefAt >= debriefQs.length;
    const onMap = $("map") && !$("map").hidden;
    const nightDone = realMoments.length > 0 && realAt >= realMoments.length;
    if (tab === 1) {
      label.textContent = openQ ? "Send the answer above" : (captureDone ? "Continue to the debrief" : "Answer the question above");
      n.textContent = "";
      btn.disabled = !captureDone;
    } else if (tab === 2) {
      n.textContent = "";
      if (mapReady || onMap) {
        label.textContent = "Continue to practice";
        btn.disabled = false;
      } else if (mapBoot && !debriefStarted) {
        label.textContent = "Opening the debrief";
        btn.disabled = true;
      } else if (!debriefStarted) {
        label.textContent = answeredCapture ? "Open the debrief" : "Back to the case";
        btn.disabled = false;
      } else if (teachbackLoading || confirming) {
        label.textContent = teachbackLoading ? "Writing the teach-back" : "Saving the confirmation";
        btn.disabled = true;
      } else if (teachback && tbAt < (teachback.steps || []).length) {
        label.textContent = "Confirm this step";
        btn.disabled = false;
      } else if (debriefDone) {
        label.textContent = "Hear the teach-back";
        btn.disabled = false;
      } else {
        label.textContent = "Answer above first";
        btn.disabled = true;
      }
    } else if (tab === 3) {
      n.textContent = "";
      if (teachBoot && !teachReady) {
        label.textContent = "Opening Teach";
        btn.disabled = true;
      } else if (!teachReady) {
        label.textContent = mapReady ? "Open practice" : (answeredCapture ? "Back to the work map" : "Back to the case");
        btn.disabled = false;
      } else if (pendingNext) {
        label.textContent = "Next case";
        btn.disabled = false;
      } else if (phase === "done") {
        label.textContent = "Continue to the night shift";
        btn.disabled = false;
      } else {
        label.textContent = "Choose an answer above";
        btn.disabled = true;
      }
    } else if (tab === 4) {
      label.textContent = nightDone ? "See how this session went" : "Choose a response above";
      n.textContent = realMoments.length ? (Math.min(realAt + 1, realMoments.length) + "/" + realMoments.length) : "";
      btn.disabled = !nightDone;
    } else {
      label.textContent = "This session is complete";
      n.textContent = "";
      btn.disabled = true;
    }
    if (count) count.hidden = !n.textContent;
  }

  function placeholder(ul, text) {
    if (!ul) return;
    ul.innerHTML = `<li class="none-yet">${esc(text)}</li>`;
  }
  function li(ul, html) {
    if (!ul) return;
    ul.querySelector(".none-yet")?.remove();
    const item = document.createElement("li");
    if (ul.id === "notes" || ul.classList.contains("qs")) item.className = "full";
    item.innerHTML = `<span>${html}</span>`;
    ul.appendChild(item);
    const aside = ul.closest("aside");
    if (aside) aside.scrollTop = aside.scrollHeight;
  }

  function buttons(items, attr) {
    return items.map(it => `<button type="button" class="opt" ${attr}="${esc(it.id)}">${esc(it.t)}</button>`).join("");
  }

  function evidenceWords(ex) {
    const live = ((ex && ex.expert_words) || []).filter(w => w.quote);
    if (live.length) return live;
    return ((ex && ex.dataset_summaries) || []).filter(d => d.summary).slice(0, 2).map(d => ({ quote: d.summary, summary: true }));
  }
  function quotes(words) {
    return (words || []).filter(w => w.quote).slice(0, 2).map(w =>
      `<p class="orig" style="font-family:var(--serif);margin:.2rem 0">“${esc(w.quote)}”</p>${w.summary ? '<p class="hint">From the notes, not a direct quotation.</p>' : ""}`
    ).join("");
  }
  function showPath(el, trace) {
    $(el).innerHTML = (trace || []).map(t => {
      const obs = Array.isArray(t.observed) ? (t.observed.join(", ") || "none") : (t.observed === "" || t.observed == null ? "none" : String(t.observed));
      const stop = !!t.met;
      return `<li class="${stop ? "stop" : "pass"}"><span>${esc(t.field)}</span><span class="a">${esc(obs)}${stop ? " ✕" : " ✓"}</span></li>`;
    }).join("");
  }

  /* ---------- capture ---------- */
  function capturePlan() {
    return [
      { prompt: "What kind of incident is this?", field: "incident_type", options: [
        { id: "wandering", t: "Wandering", value: "wandering" },
        { id: "refusal_of_care", t: "Refusal of care", value: "refusal_of_care" },
        { id: "exit_seeking", t: "Exit-seeking", value: "exit_seeking" }
      ]},
      { prompt: "What do you check?", field: "checks", options: [
        { id: "toileting", t: "Toileting", value: "toileting" },
        { id: "hearing_vision_aids", t: "Glasses and hearing", value: "hearing_vision_aids" },
        { id: "pain", t: "Pain", value: "pain" },
        { id: "signage_routine", t: "Signage and routine", value: "signage_routine" }
      ]},
      { prompt: "What do you do next?", field: "intervention", options: [
        { id: "prompted_toileting", t: "Offer the toilet", value: "prompted_toileting" },
        { id: "restore_signage", t: "Restore the door signage", value: "restore_signage" },
        { id: "request_antipsychotic", t: "Ask for an antipsychotic", value: "request_antipsychotic" },
        { id: "integration_plan_review", t: "Review the integration plan", value: "integration_plan_review" },
        { id: "swap_carer_or_call_psychologist", t: "Swap carer or call the psychologist", value: "swap_carer_or_call_psychologist" }
      ]},
      { prompt: "Who do you tell?", field: "escalate_to", options: [
        { id: "nurse", t: "Nurse", value: "nurse" },
        { id: "coordinating_physician", t: "Coordinating physician", value: "coordinating_physician" },
        { id: "team_meeting", t: "Team meeting", value: "team_meeting" },
        { id: "none", t: "No one", value: "none" }
      ]},
      { prompt: "Save this record?", field: "save", options: [
        { id: "save", t: "Save the record", value: true }
      ]}
    ];
  }

  function renderScenario(scenario) {
    const box = $("convo");
    box.innerHTML = "";
    const rec = scenario.record || {};
    const who = rec.resident || {};
    if (scenario.task) addLine(box, "psy", scenario.task, true);
    if (scenario.note) addLine(box, "ap", scenario.note, true);
    (rec.entries || []).forEach(e => addLine(box, "cg", (e.when ? e.when + " — " : "") + e.text, true));
    if (rec.facility_log) addLine(box, "sys", rec.facility_log, true);
    if (rec.routine) addLine(box, "sys", rec.routine, true);
    if (who.name) captureTitle = who.name + " · " + (scenario.title || "");
    else captureTitle = scenario.title || "";
    if (captureTitle) { $("topic-t").textContent = captureTitle; $("topic-t").title = captureTitle; }
    const hint = box.parentElement && box.parentElement.querySelector(".hint");
    if (hint) hint.textContent = "Read the case, then answer the question below.";
  }
  function addLine(box, who, text, quiet) {
    const highlight = !quiet && who !== "sys";
    if (highlight) box.querySelectorAll(".msg.live").forEach(m => m.classList.remove("live"));
    const names = { psy: "Psychologist", exa: "Psychologist", cg: "Care assistant", ap: "AI Apprentice" };
    const m = document.createElement("div");
    m.className = "msg " + (who === "sys" ? "sys" : who) + (highlight ? " live" : "");
    const name = names[who] || "";
    m.innerHTML = name
      ? `<div class="meta"><b class="who">${name}</b></div><p class="orig">${esc(text)}</p>`
      : `<p class="orig">${esc(text)}</p>`;
    box.appendChild(m);
    if (quiet) return;
    const pin = () => { box.scrollTop = box.scrollHeight; };
    pin();
    requestAnimationFrame(pin);
  }

  function showCaptureStep() {
    const host = $("cap-opts");
    const guide = document.querySelector("#tab1 > section > .hint");
    if (openQ) {
      host.innerHTML = `<p class="hint">Write the answer in your own words.</p>`;
      if (guide) guide.textContent = "The apprentice asked a question. Answer it below.";
      dock();
      return;
    }
    if (captureAt >= captureSteps.length) {
      host.innerHTML = `<p class="hint">That is the whole case. Continue to the debrief.</p>`;
      if (guide) guide.textContent = "The case is documented.";
      document.querySelector('.tab[data-tab="1"]')?.classList.add("done");
      dock();
      return;
    }
    const step = captureSteps[captureAt];
    if (guide) guide.textContent = "Read the case, then answer below. Question " + (captureAt + 1) + " of " + captureSteps.length + ".";
    host.innerHTML = `<p class="hint">${esc(step.prompt)}</p>` +
      buttons(step.options, "data-cap");
    dock();
  }

  async function onCapture(btn) {
    if (busy || openQ) return;
    const step = captureSteps[captureAt];
    const opt = step.options.find(o => o.id === btn.dataset.cap);
    if (!opt) return;
    busy = true;
    btn.disabled = true;
    if (step.field === "checks") {
      if (!form.checks.includes(opt.value)) form.checks = form.checks.concat([opt.value]);
    } else if (step.field === "save") {
      /* value is the flag; the form is the record */
    } else if (step.field !== "save") {
      form[step.field] = opt.value;
    }
    const body = { field: step.field, value: step.field === "checks" ? form.checks : opt.value, form: form };
    if (step.field === "checks") body.delta = { added: opt.value };
    const res = await api("/events", body);
    li($("notes"), esc(res.event.text));
    if (voiceOn()) speak(res.event.text, "CONTEXT");
    if (res.scope && res.scope.escalate) li($("flags1"), esc(res.scope.reason));
    addLine($("convo"), "cg", opt.t);
    const asked = await api("/question", { event_id: res.event.id, signals: GATE });
    if (asked.question) {
      openQ = asked.question;
      li($("qs"), `<b>${esc(asked.question.type)}</b> ${esc(asked.question.text)}`);
      $("ap1-sub").textContent = "Asking";
      $("cap-composer").hidden = false;
      $("ans").focus();
      speak(asked.question.text, "ASK");
      addLine($("convo"), "ap", asked.question.text);
    } else {
      $("ap1-sub").textContent = listenReason(asked.reason);
    }
    captureAt++;
    showCaptureStep();
    busy = false;
  }

  async function sendAnswer(text) {
    if (answering || !openQ || !String(text || "").trim()) return;
    answering = true;
    const q = openQ;
    const said = String(text).trim();
    try {
      const r = await api("/answer", { question_id: q.id, text: said });
      openQ = null;
      answeredCapture = true;
      $("cap-composer").hidden = true;
      $("ans").value = "";
      const learned = r.dont_know ? "left unresolved" : `${r.transition.before} → ${r.transition.after}`;
      li($("notes"), `<b>${esc(q.slot)}</b> ${esc(learned)}`);
      $("ap1-sub").textContent = "Following this case";
      addLine($("convo"), "psy", said);
      showCaptureStep();
    } catch (err) {
      addLine($("convo"), "sys", err.message);
    } finally {
      answering = false;
      dock();
    }
  }

  /* ---------- debrief and work map ---------- */
  async function openMap() {
    if (mapReady || debriefStarted) { dock(); return; }
    $("debrief").hidden = false;
    $("map").hidden = true;
    const hint = $("dconvo").parentElement && $("dconvo").parentElement.querySelector(".hint");
    if (hint) hint.textContent = "Explain what you would write. You will hear it back before it is kept.";
    if (!answeredCapture) {
      $("dconvo").innerHTML = "";
      addLine($("dconvo"), "sys", "Answer one question during Capture first. The debrief uses that explanation.");
      dock();
      return;
    }
    if (mapBoot) return mapBoot;
    mapBoot = (async () => {
      try {
        const d = await api("/debrief/start", {});
        debriefStarted = true;
        debriefQs = d.questions || [];
        debriefAt = 0;
        teachback = null;
        tbAt = 0;
        $("dconvo").innerHTML = "";
        addLine($("dconvo"), "ap", "A few points are still open from what you documented.");
        showDebriefQ();
      } catch (err) {
        mapBoot = null;
        const raw = err.message || "";
        const msg = /explained decision|capture at least/i.test(raw)
          ? "Answer one question during Capture first. The debrief uses that explanation."
          : raw;
        $("dconvo").innerHTML = "";
        addLine($("dconvo"), "sys", msg);
      } finally {
        dock();
      }
    })();
    return mapBoot;
  }
  function showDebriefQ() {
    const q = debriefQs[debriefAt];
    $("deb-composer").hidden = !q;
    const tray = $("deb-actions");
    if (tray) tray.hidden = !q;
    if (!q) {
      addLine($("dconvo"), "sys", teachback
        ? "Confirm this step, or correct it."
        : "Debrief answers are in. Hear the teach-back, then confirm each step.");
      $("confirm-block").hidden = !teachback;
      dock();
      return;
    }
    addLine($("dconvo"), "ap", q.text);
    speak(q.text, "ASK");
    li($("flags2"), `<b>${esc(q.rule_id)}</b> ${esc(q.slot)} · score ${esc(q.score)}`);
    const box = $("dans");
    if (box) box.focus();
    dock();
  }
  async function sendDebrief(text) {
    const q = debriefQs[debriefAt];
    if (answering || !q || !String(text || "").trim()) return;
    answering = true;
    const said = String(text).trim();
    try {
      const r = await api("/answer", { question_id: q.id, text: said });
      addLine($("dconvo"), "psy", said);
      addLine($("dconvo"), "sys", r.dont_know ? "Left unresolved." : (q.slot + ": " + r.transition.before + " → " + r.transition.after));
      $("dans").value = "";
      debriefAt++;
      showDebriefQ();
    } catch (err) {
      addLine($("dconvo"), "sys", err.message);
    } finally {
      answering = false;
      dock();
    }
  }
  async function loadTeachback() {
    if (teachback || teachbackLoading) return;
    teachbackLoading = true;
    dock();
    addLine($("dconvo"), "sys", "Writing the teach-back from what you said…");
    try {
      teachback = await api("/debrief/teachback");
      tbAt = 0;
      addLine($("dconvo"), "ap", teachback.text || "No teach-back text yet.");
      speak(teachback.text, "TEACHBACK");
      if (!(teachback.steps || []).length) {
        await showWorkMap();
        return;
      }
      $("confirm-block").hidden = false;
      $("confirm-block").scrollIntoView({ block: "nearest" });
      addLine($("dconvo"), "sys", "Confirm this step, or correct it. " + teachback.steps.length + " step" + (teachback.steps.length === 1 ? "" : "s") + ".");
    } catch (err) {
      teachback = null;
      addLine($("dconvo"), "sys", err.message);
    } finally {
      teachbackLoading = false;
      dock();
    }
  }
  async function confirmStep(ok) {
    if (confirming || teachbackLoading) return;
    if (!teachback) { await loadTeachback(); return; }
    const step = (teachback.steps || [])[tbAt];
    if (!step) { await showWorkMap(); return; }
    confirming = true;
    dock();
    let correction = null;
    if (!ok) correction = window.prompt("What should this step say instead?") || "Correction requested.";
    try {
      const r = await api("/debrief/confirm", { rule_id: step.rule_id, ok, correction });
      tbAt++;
      addLine($("dconvo"), "sys", step.rule_id + ": " + r.teach_back + " (" + tbAt + " of " + teachback.steps.length + ")");
      if (tbAt >= teachback.steps.length) await showWorkMap();
      else addLine($("dconvo"), "sys", "Confirm the next step, or correct it.");
    } catch (err) {
      addLine($("dconvo"), "sys", err.message);
    } finally {
      confirming = false;
      dock();
    }
  }
  async function showWorkMap() {
    const wm = await api("/workmap");
    $("debrief").hidden = true;
    $("map").hidden = false;
    $("map-ver").textContent = wm.map_version;
    $("map-banner").hidden = false;
    $("map-banner").textContent = wm.steps.length + " step" + (wm.steps.length === 1 ? "" : "s") + " from this capture. The other rules stay until someone works them.";
    const chart = $("chart");
    chart.innerHTML = (wm.steps.length ? wm.steps : []).map(s =>
      `<button type="button" class="opt" data-step="${esc(s.step_id)}" style="margin-bottom:.4rem"><b>${esc(s.n + ". " + s.title)}</b><br><span class="hint">${esc(s.decision)}</span></button>`
    ).join("") || `<p class="hint">No live step yet. Answer a capture question first.</p>`;
    chart.onclick = e => {
      const b = e.target.closest("[data-step]");
      if (!b) return;
      const s = wm.steps.find(x => x.step_id === b.dataset.step);
      if (s) renderStep(s);
    };
    const guards = [];
    wm.steps.forEach(s => (s.guardrails || []).forEach(g => guards.push(`<li><b>${esc(s.rule_id)}</b> ${esc(g.text)} <span class="ts">${esc(g.state)}</span></li>`)));
    (wm.seeded_rules || []).slice(0, 6).forEach(r => {
      const g = (r.guardrails || [])[0];
      if (g) guards.push(`<li><b>${esc(r.rule_id)}</b> ${esc(g.text)} <span class="ts">seeded · ${esc(g.state)}</span></li>`);
    });
    $("guards").innerHTML = guards.join("") || `<li>No guardrail text on this map yet.</li>`;
    $("flags3").innerHTML = (wm.unexplained_events || []).map(e => `<li>${esc(e.text)}</li>`).join("") || `<li class="none-yet">Nothing unexplained.</li>`;
    if (wm.steps[0]) renderStep(wm.steps[0]);
    else $("detail").innerHTML = `<h2 style="margin:0">Work map ${esc(wm.map_version)}</h2><p class="hint">${esc((wm.seeded_rules || []).length)} seeded rules are loaded. A live step appears after capture.</p>`;
    mapReady = true;
    document.querySelector('.tab[data-tab="2"]')?.classList.add("done");
    dock();
  }
  function renderStep(s) {
    const reasons = (s.reason || []).map(r => `<p class="orig" style="font-family:var(--serif)">“${esc(r.text)}”</p>`).join("") || "<p class='hint'>No live explanation yet.</p>";
    const prov = (s.provenance || []).slice(0, 3).map(p => {
      if (p.kind === "screen") return `<li>Screen moment ${esc(p.event_id || "")}</li>`;
      return `<li>${esc(p.kind)} ${esc(p.unit_id || "")} ${p.span ? "“" + esc(p.span) + "”" : ""}</li>`;
    }).join("");
    $("detail").innerHTML = `<h2 style="margin:0">${esc(s.title)}</h2>
      <div class="row"><span>Decision</span><span>${esc(s.decision)}</span></div>
      <div class="row"><span>You said</span><span>${reasons}</span></div>
      <div class="row"><span>Guardrail</span><span>${esc((s.guardrails[0] && s.guardrails[0].text) || "—")}</span></div>
      <div class="row"><span>Teach-back</span><span>${esc(s.teach_back)}</span></div>
      <div class="row"><span>Evidence</span><span><ul class="list">${prov}</ul></span></div>`;
  }

  /* ---------- teach (existing sealed cases) ---------- */
  function verdict(ok, tag, text, words) {
    $("j-tutor").innerHTML = `<div class="verdict ${ok ? "ok" : "stop"}"><span class="tag">${esc(tag)}</span><span>${esc(text)}</span>${quotes(words)}</div>`;
    speak(text, "SAY");
  }
  function masteryLine(rows) {
    const name = id => titles[id] || id;
    const mastered = (rows || []).filter(r => r.level === "mastered").map(r => name(r.rule_id));
    const practise = (rows || []).filter(r => r.level === "practice next").map(r => name(r.rule_id));
    $("j-mastered").textContent = mastered.join(", ") || "none yet";
    $("j-practise").textContent = practise.join(", ") || "a harder case";
  }
  async function openTeach() {
    if (teachReady) { dock(); return; }
    if (!mapReady) {
      verdict(false, "Finish the work map first", "Answer the debrief and confirm the teach-back. Teach keeps that same session.", []);
      dock();
      return;
    }
    if (teachBoot) return teachBoot;
    teachBoot = (async () => {
      await api("/session", { mode: "teach" });
      teachReady = true;
      const [caseList, ruleBook, formBook] = await Promise.all([
        api("/cases"), api("/rules"),
        fetch("teach_forms.json").then(r => { if (!r.ok) throw new Error("Record choices failed to load"); return r.json(); })
      ]);
      forms = formBook;
      (ruleBook.rules || []).forEach(r => { titles[r.id] = r.title; });
      order = (caseList.teach || []).map(c => c.id);
      teachLog = []; caught = 0; firstRight = 0; seenPredict = {};
      if (!order.length) throw new Error("No practice cases are loaded.");
      await showCase(order[0]);
    })().catch(err => {
      teachBoot = null; teachReady = false;
      verdict(false, "Could not open Teach", err.message, []);
      dock();
    });
    return teachBoot;
  }
  async function showCase(id) {
    pendingNext = null;
    phase = "predict";
    current = await api("/teach/open", { case_id: id });
    delete $("j-opts").dataset.recorded;
    $("j-done").hidden = true;
    $("j-clock").textContent = current.resident || id;
    $("j-situ").innerHTML = (current.facts || []).map(esc).join("<br>");
    $("j-q").textContent = (current.predict && current.predict.question) || "";
    const opts = (current.predict && current.predict.options) || {};
    $("j-opts").innerHTML = buttons(Object.keys(opts).sort().map(k => ({ id: k, t: opts[k] })), "data-choice");
    $("j-tutor").innerHTML = '<span class="none-yet">Pick the next documented step. You will see if the record can be saved.</span>';
    $("j-path").innerHTML = "";
    dock();
  }
  async function onPredict(letter, btn) {
    btn.classList.add("picked");
    const res = await api("/teach/predict", { case_id: current.id, option: letter });
    [...$("j-opts").children].forEach(el => { if (el.disabled !== undefined) el.disabled = true; });
    btn.classList.remove("picked");
    btn.classList.add(res.correct ? "right" : "wrong");
    if (!seenPredict[current.id]) {
      seenPredict[current.id] = true;
      if (res.correct) firstRight++;
      teachLog.push({ where: "Teach · " + current.id, first: btn.textContent, ok: !!res.correct, areaLabel: res.correct ? "" : ((res.explain && res.explain.title) || "prediction") });
    }
    if (res.correct) verdict(true, "Matches the work map", typeof res.explain === "string" ? res.explain : "That matches how the expert reasons.", []);
    else verdict(false, "Held · " + ((res.explain && res.explain.title) || "work map"), "That choice does not match this rule. Nothing is saved yet.", evidenceWords(res.explain));
    phase = "record";
    const spec = forms[current.id];
    $("j-q").textContent = "What do you write in the record?";
    $("j-opts").insertAdjacentHTML("beforeend",
      `<p class="hint" style="margin:.35rem 0">The guardrails check the record before it is saved.</p>` +
      buttons([spec.bad, spec.good], "data-choice"));
  }
  async function onRecord(which, btn) {
    const choice = forms[current.id][which];
    const firstRecord = !$("j-opts").dataset.recorded;
    $("j-opts").dataset.recorded = "1";
    btn.classList.add("picked");
    const res = await api("/teach/check-save", { case_id: current.id, form: choice.form });
    btn.classList.remove("picked");
    btn.disabled = true;
    btn.classList.add(res.saved ? "right" : "wrong");
    if (firstRecord) {
      if (!res.saved) caught++;
      teachLog.push({ where: "Teach · " + current.id + " · record", first: choice.t, ok: !!res.saved, areaLabel: res.saved ? "" : ((res.blocked[0] && res.blocked[0].title) || "guardrail") });
    }
    if (!res.saved) {
      const b = (res.blocked || []).find(x => evidenceWords(x.explain).length) || res.blocked[0] || {};
      const other = [...$("j-opts").querySelectorAll("[data-choice='bad'],[data-choice='good']")].some(el => !el.disabled);
      verdict(false, "Caught before saving · " + (b.guardrail_id || "guardrail"), (b.message || "Not saved.") + (other ? " Try the other record." : ""), evidenceWords(b.explain));
      showPath("j-path", (res.blocked || []).flatMap(x => x.trace || []));
      dock();
      return;
    }
    verdict(true, "Saved", (res.warnings || []).length ? "Saved. " + res.warnings[0].message : "Saved. No guardrail blocked this record.", []);
    $("j-path").innerHTML = `<li class="pass"><span>Record</span><span class="a">saved ✓</span></li>`;
    [...$("j-opts").querySelectorAll("[data-choice='bad'],[data-choice='good']")].forEach(el => { el.disabled = true; });
    if (res.mastery) masteryLine(res.mastery);
    if (!res.next_scenario) { pendingNext = null; finishTeach(res.mastery || []); return; }
    pendingNext = res.next_scenario;
    $("j-tutor").insertAdjacentHTML("beforeend", `<button type="button" class="btn btn-primary" id="j-next-case" style="margin-top:.6rem">Next case</button>`);
    $("j-next-case").onclick = () => {
      const id = pendingNext;
      if (!id) return;
      pendingNext = null;
      $("j-next-case").disabled = true;
      showCase(id).catch(err => verdict(false, "Could not open the next case", err.message, []));
    };
    dock();
  }
  function finishTeach(rows) {
    phase = "done";
    pendingNext = null;
    $("j-done").hidden = false;
    $("j-m1").textContent = String(caught);
    $("j-m2").textContent = firstRight + "/" + order.length;
    masteryLine(rows);
    document.querySelector('.tab[data-tab="3"]')?.classList.add("done");
    $("j-q").textContent = "The practice cases are done. Open the night shift.";
    dock();
  }
  async function onTeachClick(e) {
    const btn = e.target.closest("[data-choice]");
    if (!btn || btn.disabled || busy || !current || phase === "done") return;
    busy = true;
    try {
      if (phase === "predict" && btn.dataset.choice.length === 1) await onPredict(btn.dataset.choice, btn);
      else if (phase === "record" && (btn.dataset.choice === "bad" || btn.dataset.choice === "good")) await onRecord(btn.dataset.choice, btn);
    } catch (err) {
      verdict(false, "Could not score", err.message, []);
    } finally { busy = false; }
  }

  /* ---------- real life ---------- */
  async function openReal() {
    if (!realMoments.length) {
      if (!realBoot) {
        realBoot = fetch("real_life.json").then(r => {
          if (!r.ok) throw new Error("Night shift moments failed to load");
          return r.json();
        }).then(data => {
          realMoments = data.moments || [];
          realAt = 0;
          $("r-log").innerHTML = "";
          $("r-done").hidden = true;
        }).catch(err => {
          realBoot = null;
          pill(err.message || "Night shift failed to load", false);
          throw err;
        });
      }
      await realBoot;
    }
    showMoment();
  }
  function showMoment() {
    if (realAt >= realMoments.length) {
      $("r-done").hidden = false;
      $("r-draft").innerHTML = realLog.map(r => `<li>${esc(r.first)} <span class="ts">${r.ok ? "would save" : "caught"}</span></li>`).join("");
      document.querySelector('.tab[data-tab="4"]')?.classList.add("done");
      dock();
      return;
    }
    const m = realMoments[realAt];
    $("r-clock").textContent = m.clock;
    const hold = $("r-live");
    hold.innerHTML = `<p class="situ">${esc(m.situ)}</p>` + buttons(m.choices, "data-night");
    dock();
  }
  async function onNight(btn) {
    if (busy) return;
    const m = realMoments[realAt];
    const choice = m.choices.find(c => c.id === btn.dataset.night);
    if (!choice) return;
    busy = true;
    $("r-live").querySelectorAll("[data-night]").forEach(b => { b.disabled = true; });
    const res = await api("/guard/check", { form: choice.form });
    const row = document.createElement("div");
    row.className = "step " + (res.saved ? "ok" : "stop");
    const b = (res.blocked || [])[0];
    row.innerHTML = `<span><span class="ts">${esc(m.clock)}</span> <b>Care assistant:</b> ${esc(choice.t)}</span>` +
      `<span class="vt">${res.saved ? "Would save" : "Caught before saving · " + esc((b && b.guardrail_id) || "guardrail")}</span>` +
      `<span>${esc(res.saved ? ((res.warnings[0] && res.warnings[0].message) || "No blocking guardrail.") : (b && b.message) || "")}</span>`;
    $("r-log").appendChild(row);
    showPath("r-path", res.saved ? [] : (res.blocked || []).flatMap(x => x.trace || []));
    if (!res.saved) {
      nightCaught++;
      const words = evidenceWords(b && b.explain);
      if (words.length) row.insertAdjacentHTML("beforeend", quotes(words));
    }
    realLog.push({ where: "Real life · " + m.clock, first: choice.t, ok: !!res.saved, areaLabel: res.saved ? "" : ((b && b.title) || "guardrail") });
    const table = {};
    (res.blocked || []).forEach(x => { table[x.rule_id] = "tested"; });
    (res.warnings || []).forEach(x => { if (!table[x.rule_id]) table[x.rule_id] = "held"; });
    const prev = $("r-gtable").dataset.rules ? JSON.parse($("r-gtable").dataset.rules) : {};
    Object.assign(prev, table);
    $("r-gtable").dataset.rules = JSON.stringify(prev);
    $("r-gtable").innerHTML = Object.entries(prev).map(([id, st]) => `<tr><td><b>${esc(id)}</b></td><td class="${st === "held" ? "held" : ""}">${esc(st)}</td></tr>`).join("");
    speak(res.saved ? "That record would save." : (b && b.message) || "Caught before saving.", "SAY");
    realAt++;
    busy = false;
    showMoment();
  }

  /* ---------- results ---------- */
  async function openResults() {
    let mastery = [];
    let wm = null;
    try { mastery = (await api("/mastery")).rows || []; } catch (e) { /* teach not opened yet */ }
    try { wm = await api("/workmap"); } catch (e) { wm = null; }
    const rows = teachLog.concat(realLog);
    const caughtN = rows.filter(r => !r.ok).length;
    const mastered = mastery.filter(r => r.level === "mastered").length;
    const practise = mastery.filter(r => r.level === "practice next").map(r => titles[r.rule_id] || r.rule_id);
    const tiles = [
      [`${firstRight}/${order.length || 0}`, "Teach: right first time"],
      [String(caughtN), "Caught before saving"],
      [String(mastered), "Rules at mastered"],
      [String((wm && wm.steps || []).length), "Live work-map steps"]
    ];
    $("tiles").innerHTML = tiles.map(([v, l]) => `<div class="metric"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`).join("");
    $("rtable").innerHTML = `<tr><th>When</th><th>First decision</th><th>Result</th></tr>` +
      (rows.map(r => `<tr><td class="ts">${esc(r.where)}</td><td>${esc(r.first)}</td><td class="${r.ok ? "ok" : "no"}">${r.ok ? "✓ saved" : "✕ caught · " + esc(r.areaLabel || "")}</td></tr>`).join("") || `<tr><td colspan="3">No decisions yet.</td></tr>`);
    const areas = {};
    rows.filter(r => !r.ok && r.areaLabel).forEach(r => { areas[r.areaLabel] = (areas[r.areaLabel] || 0) + 1; });
    const max = Math.max(1, ...Object.values(areas));
    $("bars").innerHTML = Object.entries(areas).map(([k, n]) => `<div class="bar"><span>${esc(k)}</span><span class="track"><span class="fill" style="display:block;width:${n / max * 100}%"></span></span><span class="n">${n}</span></div>`).join("") || `<p class="hint">Nothing caught yet.</p>`;
    const loop = [];
    if (wm) (wm.steps || []).filter(s => s.teach_back === "none").forEach(s => loop.push(s.title + " has no teach-back yet."));
    practise.forEach(t => loop.push("Practise next: " + t));
    if (!loop.length) loop.push("No open loop from this session yet.");
    $("loop").innerHTML = loop.map(t => `<li><span>${esc(t)}</span></li>`).join("");
    document.querySelector('.tab[data-tab="5"]')?.classList.add("done");
    dock();
  }

  function wire() {
    const tray = document.createElement("div");
    tray.id = "cap-actions";
    tray.className = "choice-tray";
    const cap = document.createElement("div");
    cap.id = "cap-opts";
    cap.className = "opts";
    tray.appendChild(cap);
    const comp = document.createElement("div");
    comp.id = "cap-composer";
    comp.hidden = true;
    comp.innerHTML = `<textarea id="ans" rows="2" placeholder="Answer in your own words"></textarea>
      <div style="display:flex;gap:.4rem;margin-top:.4rem"><button type="button" class="btn btn-primary" id="ans-send">Send answer</button><button type="button" class="btn" id="ans-dk">I don't know</button></div>`;
    tray.appendChild(comp);
    $("convo").insertAdjacentElement("afterend", tray);
    const dtray = document.createElement("div");
    dtray.id = "deb-actions";
    dtray.className = "choice-tray";
    const dcomp = document.createElement("div");
    dcomp.id = "deb-composer";
    dcomp.hidden = true;
    dcomp.innerHTML = `<textarea id="dans" rows="2" placeholder="Answer the debrief question"></textarea>
      <div style="display:flex;gap:.4rem;margin-top:.4rem"><button type="button" class="btn btn-primary" id="dans-send">Send answer</button><button type="button" class="btn" id="dans-dk">I don't know</button></div>`;
    dtray.appendChild(dcomp);
    dtray.hidden = true;
    $("dconvo").insertAdjacentElement("afterend", dtray);
    const hold = document.createElement("div");
    hold.id = "r-live";
    hold.className = "opts";
    hold.style.marginTop = ".6rem";
    $("r-log").insertAdjacentElement("beforebegin", hold);

    cap.onclick = async e => {
      const b = e.target.closest("[data-cap]");
      if (!b || b.disabled || openQ || busy) return;
      try { await onCapture(b); }
      catch (err) { busy = false; b.disabled = false; addLine($("convo"), "sys", err.message); pill("Offline", false); }
    };
    $("ans-send").onclick = () => sendAnswer($("ans").value).catch(err => addLine($("convo"), "sys", err.message));
    $("ans-dk").onclick = () => sendAnswer("I don't know");
    $("dans-send").onclick = () => sendDebrief($("dans").value).catch(err => addLine($("dconvo"), "sys", err.message));
    $("dans-dk").onclick = () => sendDebrief("I don't know");
    $("btn-yes").onclick = () => confirmStep(true).catch(err => addLine($("dconvo"), "sys", err.message));
    $("btn-no").onclick = () => confirmStep(false).catch(err => addLine($("dconvo"), "sys", err.message));
    $("j-opts").addEventListener("click", onTeachClick);
    hold.onclick = async e => {
      const b = e.target.closest("[data-night]");
      if (!b || b.disabled) return;
      try { await onNight(b); }
      catch (err) { busy = false; pill(err.message || "Offline", false); showMoment(); }
    };
    document.querySelectorAll(".tab").forEach(b => { b.onclick = () => show(+b.dataset.tab); });
    $("btn-next").onclick = () => {
      if (openQ || answering || confirming || teachbackLoading) return;
      if (tab === 1 && captureAt >= captureSteps.length) show(2);
      else if (tab === 2 && mapReady) show(3);
      else if (tab === 2 && !debriefStarted) {
        if (!answeredCapture) show(1);
        else openMap();
      } else if (tab === 2 && teachback && tbAt < (teachback.steps || []).length) {
        confirmStep(true).catch(err => addLine($("dconvo"), "sys", err.message));
      } else if (tab === 2 && debriefStarted && debriefAt >= debriefQs.length) {
        loadTeachback().catch(err => addLine($("dconvo"), "sys", err.message));
      } else if (tab === 3 && !teachReady) show(mapReady ? 2 : (answeredCapture ? 2 : 1));
      else if (tab === 3 && pendingNext) {
        const id = pendingNext;
        pendingNext = null;
        const jump = $("j-next-case");
        if (jump) jump.disabled = true;
        showCase(id).catch(err => verdict(false, "Could not open the next case", err.message, []));
      } else if (tab === 3 && phase === "done") show(4);
      else if (tab === 4 && realAt >= realMoments.length && realMoments.length) show(5);
    };
    $("btn-restart").onclick = () => location.reload();
    const voiceBtn = $("btn-voice");
    if (voiceBtn) voiceBtn.onclick = async () => {
      if (!window.ApprenticeVoice) return;
      if (voiceOn()) {
        await window.ApprenticeVoice.stop();
        voiceBtn.setAttribute("aria-pressed", "false");
        voiceBtn.textContent = "Voice off";
        return;
      }
      voiceBtn.textContent = "Connecting";
      try {
        await window.ApprenticeVoice.start(voiceRole());
        voiceBtn.setAttribute("aria-pressed", "true");
        voiceBtn.textContent = "Voice on";
        pill("Voice on", true);
      } catch (err) {
        await window.ApprenticeVoice.stop();
        voiceBtn.setAttribute("aria-pressed", "false");
        voiceBtn.textContent = "Voice off";
        pill(err.message || "Voice unavailable", false);
      }
    };
    if (window.ApprenticeVoice) window.ApprenticeVoice.onUser = takeSpoken;
  }

  async function start() {
    wire();
    $("convo").innerHTML = `<p class="empty">Loading this case…</p>`;
    newSid();
    captureSteps = capturePlan();
    dock();
    try {
      const health = await api("/health");
      pill(health.ok ? "Ready" : "Degraded", !!health.ok);
      const opened = await api("/session", { mode: "capture" });
      const scenario = opened.capture_scenario;
      if (scenario && scenario.form_start) form = Object.assign(emptyForm(), scenario.form_start);
      if (scenario && scenario.record && scenario.record.resident) form.months_in_residence = scenario.record.resident.months_in_residence || form.months_in_residence;
      const obs = ((scenario.record && scenario.record.entries) || []).map(e => e.text).join(" ");
      form.observation = obs.slice(0, 900);
      renderScenario(scenario);
      placeholder($("notes"), "Nothing written down yet.");
      placeholder($("qs"), "No question yet.");
      placeholder($("flags1"), "Nothing to double-check yet.");
      showCaptureStep();
      show(1);
    } catch (err) {
      pill("Offline", false);
      $("convo").innerHTML = `<p class="empty">${esc(err.message || "The page could not reach the server. Start it and reload.")}</p>`;
    }
  }

  window.LiveTeach = {
    rows() { return teachLog.concat(realLog); },
    open() { return openTeach(); },
    onReset() { location.reload(); }
  };
  start();
})();
