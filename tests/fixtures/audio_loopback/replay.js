// Loopback stage 2: the real bot-page player (extracted from payload.js) on a virtual clock.
const fs = require('fs');
const [payloadPath, delivPath, throttleMs] = [process.argv[2], process.argv[3], +process.argv[4]];
const src = fs.readFileSync(payloadPath, 'utf8');
function method(name) {
  let i = src.indexOf(`    async ${name}(`); if (i < 0) i = src.indexOf(`    ${name}(`); if (i < 0) return "";
  let j = src.indexOf('{', i), d = 0, k = j;
  for (; k < src.length; k++) { if (src[k] === '{') d++; else if (src[k] === '}' && --d === 0) break; }
  return src.slice(i, k + 1);
}
let now = 0; const timers = [];
global.performance = { now: () => now * 1000 };
global.setTimeout = (fn, ms) => { const due = now + Math.max(ms || 0, throttleMs) / 1000; const t = { due, fn }; timers.push(t); return t; };
global.clearTimeout = t => { const i = timers.indexOf(t); if (i >= 0) timers.splice(i, 1); };
global.window = { ws: { sendJson: () => {} } }; const logs = [];
console.log = (m) => logs.push(m);
const sched = [];
const ctx = { get currentTime() { return now; }, createBuffer: (c, f, r) => { const d = new Float32Array(f); return { getChannelData: () => d, f, r }; },
  createBufferSource: () => ({ connect() {}, start(t) { sched.push([t, this.buffer.f / this.buffer.r, this.buffer.getChannelData()[0], curLine]); } }) };
const body = ['playPCMAudio', '_processAudioQueue', '_navArmMicOff'].map(method).join('\n');
const P = eval(`(class { constructor(){ this.audioQueue=[]; this.isPlayingAudioQueue=false; this.nextPlayTime=0; this.sampleRate=48000; this.numChannels=1; this.turnOffMicTimeout=null; this.audioContext=ctx; this.gainNode={}; }
 _createSourceAudioTrack(){} ensureMicOn(){} disableMic(){}\n${body.replace(/async playPCMAudio/, 'playPCMAudio')} })`);
const p = new P();
const { deliveries } = JSON.parse(fs.readFileSync(delivPath));
let di = 0; let curLine = 0, lastD = -9;
while (di < deliveries.length || timers.length) {
  const nextD = di < deliveries.length ? deliveries[di][0] : Infinity;
  timers.sort((a, b) => a.due - b.due);
  const nextT = timers.length ? timers[0].due : Infinity;
  if (nextT > 1e5 && nextD === Infinity) break;
  if (nextD <= nextT) { now = nextD; if (now - lastD > 0.8) curLine++; lastD = now; p.playPCMAudio(new Int16Array(deliveries[di][1]).fill(1000), 48000); di++; }
  else { now = nextT; timers.shift().fn(); }
}
sched.sort((a, b) => a[0] - b[0]);
let gaps = [], late = 0, lateMs = 0;
for (let i = 1; i < sched.length; i++) {
  const g = sched[i][0] - (sched[i - 1][0] + sched[i - 1][1]);
  if (g > 0.002 && sched[i][3] === sched[i-1][3]) { gaps.push(Math.round(g * 1000)); if (process.env.DBG) console.error("gap at", sched[i][0].toFixed(2), Math.round(g*1000)); }
}
const firstSample = sched.filter(s => s[2] === 0).length;
console.error(JSON.stringify({ chunks: sched.length, played_s: +sched.reduce((a, s) => a + s[1], 0).toFixed(2), midspeech_gaps: gaps.length, gap_ms_total: gaps.reduce((a, b) => a + b, 0), gap_ms_max: Math.max(0, ...gaps), fade_ins: firstSample, reported_underruns: logs.filter(l => /underrun/.test(l)).length }));
