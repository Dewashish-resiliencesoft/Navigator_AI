    _processAudioQueue() {
        // navigator: push scheduler v2. Every chunk is scheduled the moment it
        // arrives (driven by the websocket, not by setTimeout). The old
        // timer-driven loop starved whenever Chrome throttled timers in the
        // bot page: 122 underruns of ~460ms each in one Zoom demo while the
        // queue still held 300-400ms of audio.
        const ctx = this.audioContext;
        if (!ctx) { this.isPlayingAudioQueue = false; return; }
        if (this.turnOffMicTimeout) { clearTimeout(this.turnOffMicTimeout); this.turnOffMicTimeout = null; }
        const currentTime = ctx.currentTime;
        if (this.audioQueue.length === 0) {
            this.isPlayingAudioQueue = false;
            this._navArmMicOff();
            return;
        }
        if (!this.nextPlayTime || this.nextPlayTime < currentTime + 0.02) {
            // Fresh start (or starvation): wait for 300ms of audio, max 300ms.
            const queuedS = this.audioQueue.reduce((a, c) => a + c.duration, 0);
            this._navWaitSince = this._navWaitSince || performance.now();
            if (queuedS < 0.3 && performance.now() - this._navWaitSince < 300) {
                this.isPlayingAudioQueue = false; // next arrival re-evaluates
                if (!this._navTimer) {
                    this._navTimer = setTimeout(() => { this._navTimer = null; this._processAudioQueue(); }, 60);
                }
                return;
            }
            this._navWaitSince = 0;
            const gap = this.nextPlayTime ? currentTime - this.nextPlayTime : 0;
            if (this.nextPlayTime && gap > -0.02 && gap < 0.7) {
                this._navUnderruns = (this._navUnderruns || 0) + 1;
                const msg = `[bot-audio] underrun gap_ms=${(Math.max(0, gap) * 1000) | 0} n=${this._navUnderruns} queued_ms=${(queuedS * 1000) | 0}`;
                console.log(msg);
                try { window.ws.sendJson({ type: 'BOT_AUDIO_UNDERRUN', logStatement: msg }); } catch (e) {}
            }
            this.nextPlayTime = currentTime + 0.1;
            this._navFadeIn = true;
        }
        while (this.audioQueue.length) {
            const { data, duration } = this.audioQueue.shift();
            const frames = data.length / this.numChannels;
            const audioBuffer = ctx.createBuffer(this.numChannels, frames, this.sampleRate);
            for (let ch = 0; ch < this.numChannels; ch++) {
                const out = audioBuffer.getChannelData(ch);
                if (this.numChannels === 1) out.set(data);
                else for (let i = 0; i < frames; i++) out[i] = data[i * this.numChannels + ch];
                if (this._navFadeIn) {
                    // 4ms fade-in after silence: no click when speech restarts.
                    const n = Math.min(frames, Math.floor(this.sampleRate * 0.004));
                    for (let i = 0; i < n; i++) out[i] *= i / n;
                }
            }
            this._navFadeIn = false;
            const source = ctx.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(this.gainNode);
            source.start(this.nextPlayTime);
            this.nextPlayTime += duration;
        }
        this.isPlayingAudioQueue = false;
        this._navArmMicOff();
    }

    _navArmMicOff() {
        // Keep the Zoom mic unmuted between lines; mute only after 10 min idle.
        if (this.turnOffMicTimeout) clearTimeout(this.turnOffMicTimeout);
        this.turnOffMicTimeout = setTimeout(() => {
            if (this.audioQueue.length === 0) this.disableMic();
        }, 600000);
    }

