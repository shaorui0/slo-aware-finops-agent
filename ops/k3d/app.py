"""Demo workload: burns a fixed amount of CPU per request and reports latency and CFS throttling.

GET /          -> do ~WORK_MS of CPU work, reply ok
GET /healthz   -> ok
GET /metrics   -> Prometheus text: http_request_duration_p99_seconds (last 20s window),
                  cpu_throttle_ratio (throttled/periods over the last 15s), raw cfs counters.
Throttling comes from the container's cgroup cpu.stat, so it reacts to the CPU limit for real.
"""
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORK_MS = float(os.environ.get("WORK_MS", "12"))
LAT_WINDOW = 20
THR_WINDOW = 15
lat, lock = [], threading.Lock()
thr = []  # (t, nr_periods, nr_throttled)
total = [0]


def cpu_stat():
    for path in ("/sys/fs/cgroup/cpu.stat", "/sys/fs/cgroup/cpu/cpu.stat"):
        try:
            d = dict(line.split() for line in open(path))
            return int(d["nr_periods"]), int(d["nr_throttled"])
        except (OSError, KeyError, ValueError):
            continue
    return None


def sampler():
    while True:
        s = cpu_stat()
        if s:
            thr.append((time.time(), *s))
            while thr and thr[0][0] < time.time() - THR_WINDOW - 5:
                thr.pop(0)
        time.sleep(1)


def throttle_ratio():
    if len(thr) < 2:
        return 0.0
    cut = time.time() - THR_WINDOW
    base = next((x for x in thr if x[0] >= cut), thr[0])
    dp, dt = thr[-1][1] - base[1], thr[-1][2] - base[2]
    return dt / dp if dp > 0 else 0.0


def p99():
    cut = time.time() - LAT_WINDOW
    with lock:
        while lat and lat[0][0] < cut:
            lat.pop(0)
        xs = sorted(d for _, d in lat)
    return xs[min(len(xs) - 1, int(len(xs) * 0.99))] if xs else 0.0


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body, code=200):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply("ok")
        if self.path == "/metrics":
            s = cpu_stat() or (0, 0)
            return self.reply(
                f"http_request_duration_p99_seconds {p99():.4f}\n"
                f"cpu_throttle_ratio {throttle_ratio():.4f}\n"
                f"http_requests_total {total[0]}\n"
                f"container_cpu_cfs_periods_total {s[0]}\n"
                f"container_cpu_cfs_throttled_periods_total {s[1]}\n")
        t0 = time.time()
        c0 = time.thread_time()
        while (time.thread_time() - c0) * 1000 < WORK_MS:
            pass
        with lock:
            lat.append((time.time(), time.time() - t0))
            total[0] += 1
        self.reply("ok")


if __name__ == "__main__":
    threading.Thread(target=sampler, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
