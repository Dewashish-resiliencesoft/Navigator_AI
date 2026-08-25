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
      #nav-narrate button {
        flex: 1; border: 0; border-radius: 8px; padding: 7px 8px; font: 600 11px inherit;
        cursor: pointer; color: #0b1220; background: #6ee7b7; min-width: 72px;
      }
      #nav-narrate button.secondary { background: rgba(255,255,255,.12); color: #e8eef7; }
      #nav-narrate button:disabled { opacity: .45; cursor: not-allowed; }
      #nav-narrate button.recording { background: #f87171; color: #fff; }
      #nav-narrate button.paused { background: #fbbf24; color: #0b1220; }
      #nav-narrate #nav-narrate-time { margin-top: 6px; font-weight: 600; opacity: .8; font-variant-numeric: tabular-nums; }
      #nav-narrate canvas { margin-top: 6px; display: block; width: 100%; height: 22px; opacity: .9; }
      #nav-narrate-dot { width: 9px; height: 9px; border-radius: 50%; background: #64748b; flex-shrink: 0; }
      #nav-narrate-dot.live { background: #f87171; box-shadow: 0 0 0 3px rgba(248,113,113,.35); }
      #nav-narrate-dot.paused { background: #fbbf24; }
      #nav-narrate.mic-off #nav-mic-sec { display: none; }
      #nav-narrate-dot-chip { width: 9px; height: 9px; border-radius: 50%; background: #64748b; }
      #nav-narrate-dot-chip.live { background: #f87171; }
      #nav-narrate-dot-chip.paused { background: #fbbf24; }
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
          <button type="button" id="nav-studio-capture">Start capturing this flow</button>
          <button type="button" id="nav-studio-stop" class="secondary">Stop</button>
        </div>
        <div class="nav-status" id="nav-studio-status">
          Setup — log in, then Start capturing. Sample vs ask vs variables: Demo Script in dashboard.
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
  const btnCapture = box.querySelector("#nav-studio-capture");
  const btnStop = box.querySelector("#nav-studio-stop");

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
  let t0 = 0;

  const fmt = (ms) => {
    const s = Math.floor(ms / 1000);
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  };

  const studioCmd = async (action, extra) => {
    const payload = Object.assign({ action }, extra || {});
    try {
      document.documentElement.setAttribute(
        "data-nav-studio-cmd",
        JSON.stringify(payload),
      );
    } catch (e) {
      /* ignore */
    }
    try {
      if (typeof window.navigatorStudioCmd === "function") {
        return await window.navigatorStudioCmd(payload);
      }
    } catch (e) {
      console.warn("[navigator-studio] cmd failed", e);
    }
    await new Promise((r) => setTimeout(r, 400));
    try {
      if (typeof window.navigatorStudioStatus === "function") {
        return await window.navigatorStudioStatus();
      }
      if (window.__navStudioStatus) return window.__navStudioStatus;
    } catch (e) {
      /* ignore */
    }
    return null;
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

  const syncDots = (mode) => {
    for (const el of [dot, dotChip]) {
      if (!el) continue;
      el.classList.remove("live", "paused");
      if (mode === "live") el.classList.add("live");
      else if (mode === "paused") el.classList.add("paused");
    }
  };

  const drawWave = () => {
    if (!analyser || !ctx) return;
    const buf = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteTimeDomainData(buf);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#6ee7b7";
    ctx.beginPath();
    const step = canvas.width / buf.length;
    for (let i = 0; i < buf.length; i++) {
      const y = (buf[i] / 255) * canvas.height;
      if (i === 0) ctx.moveTo(0, y);
      else ctx.lineTo(i * step, y);
    }
    ctx.stroke();
    if (recording && !paused) requestAnimationFrame(drawWave);
  };

  const stopMic = () => {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    if (rec && rec.state !== "inactive") {
      try {
        rec.stop();
      } catch (e) {
        /* ignore */
      }
    }
    rec = null;
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
    if (audioCtx) {
      audioCtx.close().catch(() => {});
      audioCtx = null;
    }
    analyser = null;
    recording = false;
    paused = false;
    recBtn.textContent = "Record";
    recBtn.classList.remove("recording", "paused");
    pauseBtn.disabled = true;
    pauseBtn.textContent = "Pause";
    syncDots("");
    syncCompact();
  };

  const startMic = async () => {
    if (!hasMic) return;
    pushConfig();
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      statusEl.textContent = "Mic permission denied.";
      return;
    }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx();
    const src = audioCtx.createMediaStreamSource(stream);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    src.connect(analyser);
    recordParts = [];
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";
    rec = new MediaRecorder(stream, { mimeType: mime });
    rec.ondataavailable = (ev) => {
      if (!ev.data || !ev.data.size) return;
      recordParts.push(ev.data);
      const reader = new FileReader();
      reader.onload = () => {
        const b64 = String(reader.result || "").split(",")[1] || "";
        try {
          if (typeof window.navigatorNarrate === "function") {
            window.navigatorNarrate({
              b64,
              mime,
              language: langSel.value || "auto",
              translate_to: transSel.value || "same",
            });
          }
        } catch (e) {
          /* ignore */
        }
      };
      reader.readAsDataURL(ev.data);
    };
    t0 = performance.now();
    if (!window.__navNarrateT0) window.__navNarrateT0 = t0;
    rec.start(3000);
    recording = true;
    paused = false;
    recBtn.textContent = "Stop mic";
    recBtn.classList.add("recording");
    pauseBtn.disabled = false;
    syncDots("live");
    syncCompact();
    timer = setInterval(() => {
      timeEl.textContent = fmt(performance.now() - t0);
    }, 250);
    drawWave();
  };

  recBtn.addEventListener("click", () => {
    if (recording) stopMic();
    else void startMic();
  });

  pauseBtn.addEventListener("click", () => {
    if (!rec) return;
    if (!paused) {
      try {
        rec.pause();
      } catch (e) {
        return;
      }
      paused = true;
      pauseBtn.textContent = "Resume";
      pauseBtn.classList.add("paused");
      syncDots("paused");
    } else {
      try {
        rec.resume();
      } catch (e) {
        return;
      }
      paused = false;
      pauseBtn.textContent = "Pause";
      pauseBtn.classList.remove("paused");
      syncDots("live");
      drawWave();
    }
  });

  playBtn.addEventListener("click", () => {
    if (!recordParts.length) return;
    if (playback) {
      playback.pause();
      playback = null;
      if (playbackUrl) URL.revokeObjectURL(playbackUrl);
      playbackUrl = null;
      playBtn.textContent = "Play";
      return;
    }
    const blob = new Blob(recordParts, { type: recordParts[0].type || "audio/webm" });
    playbackUrl = URL.createObjectURL(blob);
    playback = new Audio(playbackUrl);
    playback.onended = () => {
      playBtn.textContent = "Play";
      playback = null;
    };
    playback.play();
    playBtn.textContent = "Stop play";
  });

  langSel.addEventListener("change", pushConfig);
  transSel.addEventListener("change", pushConfig);

  btnCapture.addEventListener("click", async () => {
    const res = await studioCmd("begin_capture");
    capturing = !!(res && (res.phase === "capturing" || res.ok !== false));
    if (capturing) {
      statusEl.textContent =
        "Capturing — click/fill the product. Sample vs ask: Demo Script after Stop.";
      btnCapture.textContent = "Capturing…";
      btnCapture.disabled = true;
      syncCompact();
      syncDots("live");
    } else {
      statusEl.textContent = String((res && res.error) || "Could not start capture.");
    }
  });

  btnStop.addEventListener("click", async () => {
    stopMic();
    statusEl.textContent = "Stopping…";
    await studioCmd("stop_merge");
    capturing = false;
    btnCapture.disabled = false;
    btnCapture.textContent = "Start capturing this flow";
    statusEl.textContent =
      "Stopped. Open Demo Script to set Sample / Ask visitor / Use variable on fills.";
    syncCompact();
    syncDots("");
  });

  const pollStatus = async () => {
    try {
      let st = null;
      if (typeof window.navigatorStudioStatus === "function") {
        st = await window.navigatorStudioStatus();
      } else if (window.__navStudioStatus) {
        st = window.__navStudioStatus;
      }
      if (!st || typeof st !== "object") return;
      const phase = String(st.phase || "");
      if (phase === "capturing") {
        capturing = true;
        btnCapture.disabled = true;
        btnCapture.textContent = "Capturing…";
      } else if (phase === "setup") {
        capturing = false;
        btnCapture.disabled = false;
        btnCapture.textContent = "Start capturing this flow";
      }
      const n = Number(st.steps || 0);
      if (capturing && n >= 0) {
        statusEl.textContent =
          "Capturing — " +
          n +
          " step" +
          (n === 1 ? "" : "s") +
          ". Demo Script sets sample vs ask vs variables.";
      }
      syncCompact();
    } catch (e) {
      /* ignore */
    }
  };
  setInterval(pollStatus, 1200);
  pushConfig();
})();
