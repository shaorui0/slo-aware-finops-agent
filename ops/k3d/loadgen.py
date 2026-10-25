"""Tiny load generator: RPS requests per second against TARGET, spread over worker threads."""
import os
import threading
import time
import urllib.request

TARGET = os.environ.get("TARGET", "http://api-steady:8080/")
RPS = float(os.environ.get("RPS", "5"))
WORKERS = 16


def worker():
    interval = WORKERS / RPS
    nxt = time.time()
    while True:
        nxt += interval
        try:
            urllib.request.urlopen(TARGET, timeout=10).read()
        except Exception:
            pass
        time.sleep(max(0.0, nxt - time.time()))


for _ in range(WORKERS):
    threading.Thread(target=worker, daemon=True).start()
    time.sleep(1.0 / RPS)
print(f"loadgen: {RPS} rps -> {TARGET}", flush=True)
while True:
    time.sleep(3600)
