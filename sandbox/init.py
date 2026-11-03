import os
import signal
import sys
import time

# PID 1 reaps orphans; only read-only image files contain trusted code.
os.environ.clear()
signal.signal(signal.SIGCHLD, signal.SIG_IGN)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    time.sleep(1)

