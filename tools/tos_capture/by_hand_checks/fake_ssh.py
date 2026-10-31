"""Stands in for ssh in the by-hand checks: reports the bytes it got on stdin."""
import hashlib
import sys
import time

verb = sys.argv[-1]
if verb == "hang":
    time.sleep(600)
elif verb == "hang_no_read":
    time.sleep(600)
elif verb == "fail":
    sys.stderr.write("no route to host\n")
    raise SystemExit(255)
elif verb == "flood":
    # Both pipes well past their buffers, BEFORE touching stdin: deadlocks a naive
    # read-then-write.
    sys.stdout.write("o" * 500000)
    sys.stdout.flush()
    sys.stderr.write("e" * 500000)
    sys.stderr.flush()
data = sys.stdin.buffer.read()
print(f"{verb} len={len(data)} first={data[:4].hex()} last={data[-2:].hex()} md5={hashlib.md5(data).hexdigest()}")
