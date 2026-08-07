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
  box.innerHTML = `
    <style>
      #nav-narrate {
        position: fixed; top: 16px; right: 16px; z-index: 2147483647;
        background: rgba(11, 18, 32, 0.94); color: #e8eef7;
        border-radius: 14px; border: 1px solid rgba(255,255,255,.14);
        padding: 10px 12px; font: 500 12px/1.35 system-ui, sans-serif;
        min-width: 220px; max-width: 260px;
        box-shadow: 0 10px 28px rgba(0,0,0,.38); user-select: none;
        transition: opacity .25s ease, transform .25s ease, min-width .2s ease;
      }
      #nav-narrate.compact {
        opacity: 0.42; min-width: 0; padding: 8px 10px; transform: scale(0.92);
        transform-origin: top right;
      }
      #nav-narrate.compact:hover, #nav-narrate.compact:focus-within {
        opacity: 1; transform: scale(1); min-width: 220px;
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
      #nav-narrate .nav-row { display: flex; gap: 6px; margin-top: 4px; }
      #nav-narrate button {
        flex: 1; border: 0; border-radius: 8px; padding: 7px 8px; font: 600 11px inherit;
        cursor: pointer; color: #0b1220; background: #6ee7b7;
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
    </style>
    <div class="nav-narrate-chip" id="nav-narrate-chip">
      <span id="nav-narrate-dot-chip"></span>
      <span id="nav-narrate-chip-label">Narrate</span>
    </div>
    <div class="nav-narrate-body">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <span id="nav-narrate-dot"></span>
        <strong style="font-size:13px">Narration</strong>
      </div>
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
  `;
  document.documentElement.appendChild(box);

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

  const fmt = (ms) => {
    const s = Math.floor(ms / 1000);
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  };

  const pushConfig = () => {
    try {
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
    syncDots("");
    setCompact(false);
    chipLabel.textContent = "Narrate";
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
    setCompact(true);
    chipLabel.textContent = "Recording";
    pushConfig();
    timer = setInterval(() => {
      timeEl.textContent = fmt(performance.now() - window.__navNarrateT0);
    }, 500);
    drawWave();
  };

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
})();
