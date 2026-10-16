    _startVideoDrawingLoop() {
        // navigator: frame-driven draw v2. Draw each decoded share frame once,
        // as soon as it is decoded (requestVideoFrameCallback), instead of a
        // rAF loop that redrew duplicates and added up to one vsync of delay.
        // Reports draw fps / late frames to the worker log every 10s.
        if (!this.videoElement) return;
        const v = this.videoElement;
        const stats = { n: 0, slow: 0, t0: performance.now(), last: 0, maxGap: 0 };
        const report = (now) => {
            if (now - stats.t0 < 10000) return;
            const msg = `[bot-video] draw_fps=${(stats.n * 1000 / (now - stats.t0)).toFixed(1)} max_gap_ms=${stats.maxGap | 0} slow_draws=${stats.slow} src=${v.videoWidth}x${v.videoHeight}`;
            console.log(msg);
            try { window.ws.sendJson({ type: 'BOT_VIDEO_STATS', logStatement: msg }); } catch (e) {}
            stats.n = 0; stats.slow = 0; stats.maxGap = 0; stats.t0 = now;
        };
        const draw = () => {
            const vw = v.videoWidth, vh = v.videoHeight;
            if (vw && vh && (this.canvas.width !== vw || this.canvas.height !== vh)) {
                this.canvas.width = vw;
                this.canvas.height = vh;
            }
            const t = performance.now();
            this.canvasCtx.drawImage(v, 0, 0, this.canvas.width, this.canvas.height);
            const done = performance.now();
            if (done - t > 15) stats.slow++;
            if (stats.last) stats.maxGap = Math.max(stats.maxGap, done - stats.last);
            stats.last = done; stats.n++;
            report(done);
        };
        const alive = () => this.videoElement === v && !v.paused && !v.ended;
        let lastDrawTime = 0;
        const startRaf = () => { this.videoRafId = requestAnimationFrame(drawFrame); };
        if (typeof v.requestVideoFrameCallback === 'function') {
            let vfcCount = 0, useVfc = true;
            const startedAt = performance.now();
            const onFrame = () => {
                if (!alive() || !useVfc) return;
                vfcCount++;
                draw();
                v.requestVideoFrameCallback(onFrame);
            };
            v.requestVideoFrameCallback(onFrame);
            this.videoRafId = -1;
            // captureStream needs periodic paints on a static screen; also
            // fall back to rAF if rVFC never fires (detached element).
            this._navKeepalive = setInterval(() => {
                if (!alive()) { clearInterval(this._navKeepalive); this._navKeepalive = null; return; }
                if (useVfc && vfcCount === 0 && performance.now() - startedAt > 2000) {
                    useVfc = false; clearInterval(this._navKeepalive); this._navKeepalive = null;
                    console.log('[bot-video] rVFC silent; using rAF draw loop');
                    startRaf(); return;
                }
                if (performance.now() - stats.last > 500) draw();
            }, 500);
            return;
        }
        const drawFrame = (timestamp) => {
            if (!alive()) { this.videoRafId = null; return; }
            if (timestamp - lastDrawTime >= 1000 / 30 - 4) { draw(); lastDrawTime = timestamp; }
            this.videoRafId = requestAnimationFrame(drawFrame);
        };
        startRaf();
    }

