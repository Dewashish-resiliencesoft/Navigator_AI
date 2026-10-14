"""Loopback stage 1: real Attendee RealtimeAudioOutputManager, Gemini-like bursty input."""
import importlib.util, json, random, sys, time
import numpy as np
spec = importlib.util.spec_from_file_location("ram", sys.argv[1]); ram = importlib.util.module_from_spec(spec); spec.loader.exec_module(ram)
random.seed(7); SR = 48000
out = []; t0 = time.monotonic()
def cb(bytes, sample_rate):
    time.sleep(random.uniform(0.01, 0.06))  # chromedriver execute_script cost
    out.append((time.monotonic() - t0, len(bytes) // 2))
m = ram.RealtimeAudioOutputManager(cb, 0.9, SR)  # Zoom settings
total = 0; sent_log = []
for line in range(3):  # three scripted lines, 6s each, 1.5s apart
    n_audio = 0
    while n_audio < 6 * SR:
        n = int(SR * random.choice([0.02, 0.04, 0.04, 0.08]))
        m.add_chunk(b"\x01\x00" * n, SR); n_audio += n; total += n
        sent_log.append((time.monotonic() - t0, n))
        d = random.expovariate(1 / 0.018) * (n / (0.04 * SR))
        if random.random() < 0.04: d += random.uniform(0.18, 0.35)  # Gemini stall
        time.sleep(d)
    time.sleep(1.5)
time.sleep(1.0)
json.dump({"in_total": total, "deliveries": out, "inputs": sent_log}, open("/tmp/deliv.json", "w"))
print("in", total, "delivered", sum(n for _, n in out))
