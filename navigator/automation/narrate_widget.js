(() => {
  if (document.getElementById("nav-narrate")) return;

  const LANGS = [
    ["auto", "Auto-detect"],
    ["en", "English"],
    ["hi", "Hindi"],
    ["es", "Spanish"],
    ["fr", "French"],
    ["de", "German"],
    ["pt", "Portuguese"],
    ["ja", "Japanese"],
    ["ar", "Arabic"],
  ];

  const box = document.createElement("div");
  box.id = "nav-narrate";
  box.setAttribute("data-navigator-chrome", "record-studio");
  box.innerHTML = `
    <style>
      #nav-narrate {
        position: fixed; top: 16px; right: 16px; z-index: 2147483647;
        background: rgba(11, 18, 32, 0.94); color: #e8eef7;
        border-radius: 14px; border: 1px solid rgba(255,255,255,.14);
        padding: 10px 12px; font: 500 12px/1.35 system-ui, sans-serif;
        min-width: 240px; max-width: 280px;
        box-shadow: 0 10px 28px rgba(0,0,0,.38); user-select: none;
        transition: opacity .25s ease, transform .25s ease, min-width .2s ease;
      }
      #nav-narrate.compact {
        opacity: 0.22; min-width: 0; padding: 8px 10px; transform: scale(0.92);
        transform-origin: top right;
      }
      #nav-narrate.compact:hover, #nav-narrate.compact:focus-within {
        opacity: 1; transform: scale(1); min-width: 240px;
      }
      #nav-narrate.compact .nav-narrate-body { display: none; }
      #nav-narrate.compact:hover .nav-narrate-body,
      #nav-narrate.compact:focus-within .nav-narrate-body { display: block; }
      #nav-narrate .nav-narrate-chip {
        display: none; align-items: center; gap: 8px; cursor: default;
      }
      #nav-narrate.compact .nav-narrate-chip { display: flex; }
      #nav-narrate.compact:hover .nav-narrate-chip,
      #nav-narrate.compact:focus-within .nav-narrate-chip { display: none; }
      #nav-narrate label { display: block; font-size: 10px; opacity: .72; margin: 0 0 3px; }
      #nav-narrate select {
        width: 100%; margin-bottom: 8px; border-radius: 8px; border: 1px solid rgba(255,255,255,.12);
        background: rgba(255,255,255,.06); color: #e8eef7; padding: 5px 8px; font: inherit;
      }
      #nav-narrate .nav-row { display: flex; gap: 6px; margin-top: 4px; flex-wrap: wrap; }
      #nav-narrate .nav-sec {
        margin: 0 0 10px; padding-bottom: 10px;
        border-bottom: 1px solid rgba(255,255,255,.1);
      }
      #nav-narrate .nav-sec:last-child { margin-bottom: 0; padding-bottom: 0; border-bottom: 0; }
      #nav-narrate .nav-status {
        font-size: 11px; opacity: .85; margin: 6px 0 0; line-height: 1.35;
        max-height: 4.2em; overflow: hidden;
      }
      #nav-narrate .nav-ask {
        margin-top: 8px; padding: 8px; border-radius: 8px;
        background: rgba(251, 191, 36, .12); border: 1px solid rgba(251,191,36,.35);
        display: none;
      }
      #nav-narrate .nav-ask.show { display: block; }
      #nav-narrate textarea {
        width: 100%; box-sizing: border-box; margin-top: 2px; border-radius: 8px;
        border: 1px solid rgba(255,255,255,.14); background: rgba(0,0,0,.25);
        color: #e8eef7; padding: 6px 8px; font: inherit; resize: vertical;
      }
      #nav-narrate.ask-open { opacity: 1 !important; transform: scale(1) !important; min-width: 260px; }
      #nav-narrate.ask-open .nav-narrate-body { display: block !important; }
      #nav-narrate.ask-open .nav-narrate-chip { display: none !important; }
      #nav-narrate button {
        flex: 1; border: 0; border-radius: 8px; padding: 7px 8px; font: 600 11px inherit;
        cursor: pointer; color: #0b1220; background: #6ee7b7; min-width: 72px;
      }
      #nav-narrate button.secondary { background: rgba(255,255,255,.12); color: #e8eef7; }
      #nav-narrate button.warn { background: #fbbf24; color: #0b1220; }
      #nav-narrate button:disabled { opacity: .45; cursor: not-allowed; }
      #nav-narrate button.recording { background: #f87171; color: #fff; }
      #nav-narrate button.paused { background: #fbbf24; color: #0b1220; }
      #nav-narrate #nav-narrate-time { margin-top: 6px; font-weight: 600; opacity: .8; font-variant-numeric: tabular-nums; }
      #nav-narrate canvas { margin-top: 6px; display: block; width: 100%; height: 22px; opacity: .9; }
      #nav-narrate-dot { width: 9px; height: 9px; border-radius: 50%; background: #64748b; flex-shrink: 0; }
      #nav-narrate-dot.live { background: #f87171; box-shadow: 0 0 0 3px rgba(248,113,113,.35); }
      #nav-narrate-dot.paused { background: #fbbf24; }
      #nav-narrate.mic-off #nav-mic-sec { display: none; }
      #nav-narrate .nav-field {
        margin-top: 8px; padding: 8px; border-radius: 8px;
        background: rgba(110, 231, 183, .1); border: 1px solid rgba(110,231,183,.35);
        display: none;
      }
      #nav-narrate .nav-field.show { display: block; }
      #nav-narrate .nav-var-row { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
      #nav-narrate .nav-var-chip {
        font-size: 10px; padding: 3px 7px; border-radius: 999px;
        background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.18);
        cursor: grab; user-select: none;
      }
      #nav-narrate input[type="text"] {
        width: 100%; box-sizing: border-box; margin-top: 2px; border-radius: 8px;
        border: 1px solid rgba(255,255,255,.14); background: rgba(0,0,0,.25);
        color: #e8eef7; padding: 6px 8px; font: inherit;
      }
      #nav-narrate.field-open { opacity: 1 !important; transform: scale(1) !important; min-width: 280px; }
      #nav-narrate.field-open .nav-narrate-body { display: block !important; }
      #nav-narrate.field-open .nav-narrate-chip { display: none !important; }
    </style>
    <div class="nav-narrate-chip" id="nav-narrate-chip">
      <span id="nav-narrate-dot-chip"></span>
      <span id="nav-narrate-chip-label">Studio</span>
    </div>
    <div class="nav-narrate-body">
      <div class="nav-sec" id="nav-studio-sec">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <span id="nav-narrate-dot"></span>
          <strong style="font-size:13px">Record studio</strong>
        </div>
        <div class="nav-row">
          <button type="button" id="nav-studio-capture" class="secondary">Start capturing</button>
          <button type="button" id="nav-studio-stop" class="secondary">Stop</button>
        </div>
        <div class="nav-row">
          <button type="button" id="nav-studio-hands">Start hands</button>
          <button type="button" id="nav-studio-pause" class="secondary" disabled>Pause</button>
        </div>
        <div class="nav-row">
          <button type="button" id="nav-studio-resume" class="secondary" disabled>Resume</button>
          <button type="button" id="nav-studio-barge" class="warn" disabled>Take over</button>
        </div>
        <div class="nav-status" id="nav-studio-status">Setup — log in, then Start capturing.</div>
        <div class="nav-field" id="nav-studio-field">
          <div id="nav-studio-field-label" style="margin-bottom:6px;font-weight:600"></div>
          <label for="nav-studio-var">Variable name</label>
          <input type="text" id="nav-studio-var" placeholder="work_email" autocomplete="off" />
          <label for="nav-studio-field-q" style="margin-top:6px">Ask visitor</label>
          <textarea id="nav-studio-field-q" rows="2" placeholder="Could you share your work email?"></textarea>
          <div class="nav-row" style="margin-top:6px">
            <button type="button" id="nav-studio-field-ask">Ask visitor</button>
          </div>
          <div class="nav-row">
            <button type="button" id="nav-studio-field-keep" class="secondary">Keep as agent fill</button>
            <button type="button" id="nav-studio-field-dismiss" class="secondary">Dismiss</button>
          </div>
          <div id="nav-studio-vars-wrap" style="margin-top:8px;display:none">
            <label>Drag a prior variable onto this field</label>
            <div class="nav-var-row" id="nav-studio-vars"></div>
          </div>
        </div>
        <div class="nav-ask" id="nav-studio-ask">
          <div id="nav-studio-ask-text" style="margin-bottom:6px"></div>
          <label for="nav-studio-prompt">What should we ask the visitor here?</label>
          <textarea id="nav-studio-prompt" rows="3" placeholder="e.g. Ask for their work email before continuing"></textarea>
          <div class="nav-row" style="margin-top:6px">
            <button type="button" id="nav-studio-ask-visitor">Ask visitor this</button>
          </div>
          <div class="nav-row">
            <button type="button" id="nav-studio-click-me" class="warn">I'll click</button>
            <button type="button" id="nav-studio-skip" class="secondary">Skip</button>
          </div>
        </div>
        <div class="nav-sec" id="nav-next-sec" style="margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,.1)">
          <label for="nav-studio-next">What to do next</label>
          <textarea id="nav-studio-next" rows="2" placeholder="e.g. send campaign using this number"></textarea>
          <div class="nav-row" style="margin-top:6px">
            <button type="button" id="nav-studio-next-go" class="secondary">Plan next steps</button>
          </div>
        </div>
      </div>
      <div class="nav-sec" id="nav-mic-sec">
        <strong style="font-size:12px;display:block;margin-bottom:6px">Narration</strong>
        <label for="nav-narrate-lang">Speak in</label>
        <select id="nav-narrate-lang"></select>
        <label for="nav-narrate-translate">Final script language</label>
        <select id="nav-narrate-translate"></select>
        <div class="nav-row">
          <button type="button" id="nav-narrate-rec">Record</button>
          <button type="button" id="nav-narrate-pause" class="secondary" disabled>Pause</button>
          <button type="button" id="nav-narrate-play" class="secondary" disabled>Play</button>
        </div>
        <div id="nav-narrate-time">00:00</div>
        <canvas id="nav-narrate-wave" width="220" height="22"></canvas>
      </div>
    </div>
  `;
  document.documentElement.appendChild(box);

  const hasMic =
    typeof window.navigatorNarrate === "function" ||
    typeof window.navigatorNarrateConfig === "function";
  if (!hasMic) box.classList.add("mic-off");

  const langSel = box.querySelector("#nav-narrate-lang");
  const transSel = box.querySelector("#nav-narrate-translate");
  const recBtn = box.querySelector("#nav-narrate-rec");
  const pauseBtn = box.querySelector("#nav-narrate-pause");
  const playBtn = box.querySelector("#nav-narrate-play");
  const dot = box.querySelector("#nav-narrate-dot");
  const dotChip = box.querySelector("#nav-narrate-dot-chip");
  const chipLabel = box.querySelector("#nav-narrate-chip-label");
  const timeEl = box.querySelector("#nav-narrate-time");
  const canvas = box.querySelector("#nav-narrate-wave");
  const ctx = canvas.getContext("2d");
  const statusEl = box.querySelector("#nav-studio-status");
  const askBox = box.querySelector("#nav-studio-ask");
  const askText = box.querySelector("#nav-studio-ask-text");
  const askPrompt = box.querySelector("#nav-studio-prompt");
  const btnAskVisitor = box.querySelector("#nav-studio-ask-visitor");
  const btnClickMe = box.querySelector("#nav-studio-click-me");
  const btnCapture = box.querySelector("#nav-studio-capture");
  const btnStop = box.querySelector("#nav-studio-stop");
  const btnHands = box.querySelector("#nav-studio-hands");
  const btnPause = box.querySelector("#nav-studio-pause");
  const btnResume = box.querySelector("#nav-studio-resume");
  const btnBarge = box.querySelector("#nav-studio-barge");
  const btnSkip = box.querySelector("#nav-studio-skip");
  const fieldBox = box.querySelector("#nav-studio-field");
  const fieldLabel = box.querySelector("#nav-studio-field-label");
  const fieldVar = box.querySelector("#nav-studio-var");
  const fieldQ = box.querySelector("#nav-studio-field-q");
  const btnFieldAsk = box.querySelector("#nav-studio-field-ask");
  const btnFieldKeep = box.querySelector("#nav-studio-field-keep");
  const btnFieldDismiss = box.querySelector("#nav-studio-field-dismiss");
  const varsWrap = box.querySelector("#nav-studio-vars-wrap");
  const varsRow = box.querySelector("#nav-studio-vars");
  const nextPrompt = box.querySelector("#nav-studio-next");
  const btnNextGo = box.querySelector("#nav-studio-next-go");

  for (const [val, label] of LANGS) {
    langSel.appendChild(new Option(label, val));
  }
  transSel.appendChild(new Option("Same as spoken", "same"));
  for (const [val, label] of LANGS.filter(([v]) => v !== "auto")) {
    transSel.appendChild(new Option(label, val));
  }

  let rec = null;
  let analyser = null;
  let stream = null;
  let timer = null;
  let audioCtx = null;
  let recordParts = [];
  let playback = null;
  let playbackUrl = null;
  let recording = false;
  let paused = false;
  let capturing = false;
  let handsActive = false;
  let pendingQid = null;
  let lastField = null;
  let demoVariables = [];

  const fmt = (ms) => {
    const s = Math.floor(ms / 1000);
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  };

  const studioCmd = async (action, extra) => {
    try {
      if (typeof window.navigatorStudioCmd !== "function") return null;
      return await window.navigatorStudioCmd(Object.assign({ action }, extra || {}));
    } catch (e) {
      console.warn("[navigator-studio] cmd failed", e);
      return null;
    }
  };

  const pushConfig = () => {
    try {
      if (typeof window.navigatorNarrateConfig !== "function") return;
      window.navigatorNarrateConfig({
        language: langSel.value || "auto",
        translate_to: transSel.value || "same",
      });
    } catch (e) {
      /* binding not ready yet */
    }
  };

  const setCompact = (on) => {
    box.classList.toggle("compact", !!on);
  };

  const syncCompact = () => {
    setCompact(capturing || recording);
    if (capturing && !recording) chipLabel.textContent = "Capturing";
    else if (recording) chipLabel.textContent = "Recording";
    else chipLabel.textContent = "Studio";
  };

  const syncDots = (state) => {
    dot.className = "";
    dotChip.className = "";
    if (state === "live") {
      dot.classList.add("live");
      dotChip.classList.add("live");
    } else if (state === "paused") {
      dot.classList.add("paused");
      dotChip.classList.add("paused");
    }
  };

  const renderVars = () => {
    if (!varsRow || !varsWrap) return;
    varsRow.innerHTML = "";
    if (!demoVariables.length) {
      varsWrap.style.display = "none";
      return;
    }
    varsWrap.style.display = "block";
    for (const v of demoVariables) {
      const chip = document.createElement("span");
      chip.className = "nav-var-chip";
      chip.textContent = "$" + (v.alias || "var");
      chip.draggable = true;
      chip.title = v.live_question || v.label || v.alias;
      chip.addEventListener("dragstart", (ev) => {
        ev.dataTransfer.setData("text/plain", v.alias || "");
      });
      chip.addEventListener(
        "click",
        (ev) => {
          ev.stopPropagation();
          if (!lastField) {
            statusEl.textContent = "Focus a field first, then bind $" + v.alias;
            return;
          }
          void studioCmd("bind_value_ref", {
            value_ref: v.alias,
            step_index: lastField.step_index,
          }).then(() => pollStudio());
        },
        true,
      );
      varsRow.appendChild(chip);
    }
  };

  const showFieldPanel = (lf) => {
    lastField = lf && typeof lf === "object" ? lf : null;
    if (!lastField || !capturing || handsActive) {
      fieldBox.classList.remove("show");
      box.classList.remove("field-open");
      return;
    }
    const alias = String(lastField.alias || "field");
    fieldLabel.textContent =
      (lastField.source === "user" ? "Ask visitor · " : "Field · ") +
      (lastField.label || alias);
    if (fieldVar && !fieldVar.value) fieldVar.value = alias;
    if (fieldQ && !fieldQ.value) {
      fieldQ.value =
        lastField.live_question ||
        "Could you share your " + alias.replace(/_/g, " ") + "?";
    }
    fieldBox.classList.add("show");
    box.classList.add("field-open");
    box.classList.remove("compact");
    renderVars();
  };

  const applyStudioStatus = (st) => {
    if (!st || typeof st !== "object") return;
    const phase = String(st.phase || "setup");
    capturing = phase === "capturing";
    btnCapture.disabled = capturing;
    btnCapture.textContent = capturing ? "Capturing…" : "Start capturing";
    const hands = st.hands || {};
    handsActive = !!hands.active;
    btnHands.disabled = !capturing || handsActive;
    btnPause.disabled = !handsActive || !!hands.client_paused || hands.phase === "paused";
    btnResume.disabled =
      !handsActive ||
      (!hands.client_paused && !hands.barged && hands.phase !== "paused" && hands.phase !== "barged");
    btnBarge.disabled = !handsActive || !!hands.barged;
    demoVariables = Array.isArray(st.demo_variables) ? st.demo_variables : [];
    let line = capturing ? "Capturing — clicks enter the flow." : "Setup — log in, then Start capturing.";
    if (handsActive) {
      line =
        hands.phase === "awaiting_input"
          ? "Hands paused — needs your answer."
          : hands.phase === "barged"
            ? "You took over — Resume when ready."
            : hands.phase === "paused"
              ? "Hands paused."
              : `Hands — ${hands.progress?.steps_done || 0}/${hands.progress?.steps_total || 0}`;
      if (hands.current_step) line += ` · ${hands.current_step}`;
    }
    statusEl.textContent = line;
    const q = hands.question;
    if (handsActive && q && q.qid) {
      pendingQid = q.qid;
      askText.textContent = q.prompt || "Need your help on this step.";
      askBox.classList.add("show");
      box.classList.add("ask-open");
      box.classList.remove("compact");
      fieldBox.classList.remove("show");
      box.classList.remove("field-open");
    } else {
      pendingQid = null;
      askBox.classList.remove("show");
      box.classList.remove("ask-open");
      if (askPrompt) askPrompt.value = "";
      showFieldPanel(st.last_field);
    }
    if (hands.phase === "awaiting_input" || hands.phase === "paused" || hands.phase === "barged") {
      syncDots("paused");
    } else if (handsActive || capturing) {
      syncDots("live");
    } else {
      syncDots("");
    }
    syncCompact();
  };

  const pollStudio = async () => {
    try {
      if (typeof window.navigatorStudioStatus !== "function") return;
      const st = await window.navigatorStudioStatus();
      applyStudioStatus(st);
    } catch (e) {
      /* ignore */
    }
  };

  const drawWave = () => {
    if (!analyser) return;
    const buf = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteTimeDomainData(buf);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = paused ? "#fbbf24" : "#6ee7b7";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let i = 0; i < canvas.width; i++) {
      const v = buf[Math.floor((i * buf.length) / canvas.width)] / 128.0;
      const y = (v * canvas.height) / 2;
      i ? ctx.lineTo(i, y) : ctx.moveTo(i, y);
    }
    ctx.stroke();
    requestAnimationFrame(drawWave);
  };

  const revokePlayback = () => {
    if (playback) {
      playback.pause();
      playback = null;
    }
    if (playbackUrl) {
      URL.revokeObjectURL(playbackUrl);
      playbackUrl = null;
    }
  };

  const rebuildPlayback = () => {
    revokePlayback();
    if (!recordParts.length) {
      playBtn.disabled = true;
      return;
    }
    const mime = (rec && rec.mimeType) || "audio/webm";
    playbackUrl = URL.createObjectURL(new Blob(recordParts, { type: mime }));
    playback = new Audio(playbackUrl);
    playback.onended = () => {
      playBtn.textContent = "Play";
    };
    playBtn.disabled = false;
  };

  const sendChunk = async (blob) => {
    if (!blob || !blob.size) return;
    recordParts.push(blob);
    const buf = await blob.arrayBuffer();
    let bin = "";
    const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    try {
      window.navigatorNarrate({
        mime: (rec && rec.mimeType) || "audio/webm",
        b64: btoa(bin),
        language: langSel.value || "auto",
        translate_to: transSel.value || "same",
      });
    } catch (e) {
      console.warn("[navigator-narrate] send failed", e);
    }
  };

  const stopTracks = () => {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    if (audioCtx) {
      audioCtx.close().catch(() => {});
      audioCtx = null;
    }
    analyser = null;
    if (timer) clearInterval(timer);
    timer = null;
  };

  const fullStop = () => {
    if (rec && rec.state !== "inactive") {
      try {
        rec.stop();
      } catch (e) {
        /* already stopped */
      }
    }
    rec = null;
    recording = false;
    paused = false;
    stopTracks();
    recBtn.textContent = "Record";
    recBtn.classList.remove("recording", "paused");
    pauseBtn.disabled = true;
    pauseBtn.textContent = "Pause";
    syncCompact();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    rebuildPlayback();
  };

  const start = async () => {
    revokePlayback();
    recordParts = [];
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    audioCtx.createMediaStreamSource(stream).connect(analyser);
    window.__navNarrateT0 = performance.now();
    rec = new MediaRecorder(stream);
    rec.ondataavailable = (ev) => {
      void sendChunk(ev.data);
    };
    rec.onstop = () => {
      rebuildPlayback();
    };
    rec.start(3000);
    recording = true;
    paused = false;
    recBtn.textContent = "Stop";
    recBtn.classList.add("recording");
    pauseBtn.disabled = false;
    playBtn.disabled = true;
    syncDots("live");
    syncCompact();
    pushConfig();
    timer = setInterval(() => {
      timeEl.textContent = fmt(performance.now() - window.__navNarrateT0);
    }, 500);
    drawWave();
  };

  btnCapture.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      void studioCmd("begin_capture").then(() => pollStudio());
    },
    true,
  );
  btnStop.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      void studioCmd("stop_record");
    },
    true,
  );
  btnHands.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      void studioCmd("hands_start").then(() => pollStudio());
    },
    true,
  );
  btnPause.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      void studioCmd("hands_pause").then(() => pollStudio());
    },
    true,
  );
  btnResume.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      void studioCmd("hands_resume").then(() => pollStudio());
    },
    true,
  );
  btnBarge.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      void studioCmd("hands_barge").then(() => pollStudio());
    },
    true,
  );
  btnAskVisitor.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      if (!pendingQid) return;
      const prompt = (askPrompt && askPrompt.value) || "";
      if (!String(prompt).trim()) {
        statusEl.textContent = "Type what to ask the visitor first.";
        return;
      }
      statusEl.textContent = "Capturing screen + drafting ask…";
      void studioCmd("hands_mark_ask", { qid: pendingQid, prompt }).then(() =>
        pollStudio(),
      );
    },
    true,
  );
  btnClickMe.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      void studioCmd("hands_click_myself").then(() => pollStudio());
    },
    true,
  );
  btnSkip.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      if (!pendingQid) return;
      void studioCmd("hands_answer", { qid: pendingQid, skip: true }).then(() =>
        pollStudio(),
      );
    },
    true,
  );

  btnFieldAsk.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      if (!lastField) {
        statusEl.textContent = "Click a field first.";
        return;
      }
      const varAlias = (fieldVar && fieldVar.value) || lastField.alias || "";
      const q = (fieldQ && fieldQ.value) || "";
      if (!String(q).trim()) {
        statusEl.textContent = "Type the visitor question first.";
        return;
      }
      statusEl.textContent = "Marking Ask visitor…";
      void studioCmd("mark_field_ask", {
        var_alias: varAlias,
        live_question: q,
        step_index: lastField.step_index,
      }).then((res) => {
        if (res && res.error) statusEl.textContent = String(res.error);
        else statusEl.textContent = "Saved — visitor will be asked live.";
        return pollStudio();
      });
    },
    true,
  );
  btnFieldKeep.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      void studioCmd("keep_agent_fill").then(() => pollStudio());
    },
    true,
  );
  btnFieldDismiss.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      void studioCmd("dismiss_field").then(() => pollStudio());
    },
    true,
  );
  fieldBox.addEventListener("dragover", (ev) => {
    ev.preventDefault();
  });
  fieldBox.addEventListener("drop", (ev) => {
    ev.preventDefault();
    const ref = ev.dataTransfer.getData("text/plain");
    if (!ref) return;
    void studioCmd("bind_value_ref", {
      value_ref: ref,
      step_index: lastField && lastField.step_index,
    }).then(() => pollStudio());
  });
  btnNextGo.addEventListener(
    "click",
    (ev) => {
      ev.stopPropagation();
      const prompt = (nextPrompt && nextPrompt.value) || "";
      if (!String(prompt).trim()) {
        statusEl.textContent = "Describe what to do next.";
        return;
      }
      statusEl.textContent = "Planning next steps (one screenshot)…";
      void studioCmd("next_prompt", { prompt }).then((res) => {
        if (res && res.error) statusEl.textContent = String(res.error);
        else statusEl.textContent = "Added " + (res && res.added ? res.added : 0) + " steps.";
        return pollStudio();
      });
    },
    true,
  );

  recBtn.addEventListener(
    "click",
    async (ev) => {
      ev.stopPropagation();
      if (recording) {
        fullStop();
        return;
      }
      try {
        await start();
      } catch (e) {
        chipLabel.textContent = "Mic blocked";
        console.warn(e);
      }
    },
    true,
  );

  pauseBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    if (!rec || rec.state === "inactive") return;
    if (!paused && rec.state === "recording") {
      rec.pause();
      paused = true;
      recBtn.classList.add("paused");
      syncDots("paused");
      chipLabel.textContent = "Paused";
      pauseBtn.textContent = "Resume";
    } else if (paused && rec.state === "paused") {
      rec.resume();
      paused = false;
      recBtn.classList.remove("paused");
      syncDots("live");
      chipLabel.textContent = "Recording";
      pauseBtn.textContent = "Pause";
      drawWave();
    }
  });

  playBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    if (!playback) {
      rebuildPlayback();
    }
    if (!playback) return;
    if (!playback.paused) {
      playback.pause();
      playBtn.textContent = "Play";
      return;
    }
    playback.play().catch(() => {});
    playBtn.textContent = "Pause";
  });

  langSel.addEventListener("change", pushConfig);
  transSel.addEventListener("change", pushConfig);
  pushConfig();
  void pollStudio();
  setInterval(() => {
    void pollStudio();
  }, 1000);
})();
