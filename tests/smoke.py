"""Start SIXAMP in a pseudo-terminal that claims Sixel support, drive it a little and quit.

    python tests/smoke.py [command ...]      (default: python sixamp.py)

Checks that it draws Sixel images, survives keys and clicks, restores the terminal and
exits cleanly, and that it refuses to start without Sixel. POSIX only (uses pty).
"""
import os
import pty
import select
import struct
import sys
import tempfile
import time

import fcntl
import termios

HERE = os.path.dirname(os.path.abspath(__file__))
CMD = sys.argv[1:] or [sys.executable, os.path.join(HERE, "..", "sixamp.py")]


def run(reply, keys, seconds):
    home = tempfile.mkdtemp()
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(XDG_CONFIG_HOME=home, XDG_CACHE_HOME=home, TERM_PROGRAM="smoke")
        os.execvp(CMD[0], CMD + ["--no-audio"])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 60, 160, 1600, 1200))
    out, t0, answered, k = b"", time.time(), False, 0
    while time.time() - t0 < seconds + 5:
        if select.select([fd], [], [], 0.05)[0]:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            out += data
            if not answered and b"\x1b[c" in out:
                os.write(fd, reply)
                answered = True
        if answered and k < len(keys) and time.time() - t0 > 1 + 0.2 * k:
            os.write(fd, keys[k])
            k += 1
        if k == len(keys) and time.time() - t0 > seconds:
            os.write(fd, b"q")
            k += 1
    _, status = os.waitpid(pid, 0)
    return out, os.waitstatus_to_exitcode(status)


def main():
    keys = [b"w", b"w", b"w", b"e", b"e", b"p", b"p", b"?", b"\x1b", b"l", b"\x1b", b"n", b"f",
            b"\x1b[<0;40;20M", b"\x1b[<0;40;20m", b"\x1b[<64;40;20M"]
    out, code = run(b"\x1b[?62;4;22c", keys, 3)
    images = out.count(b"\x1bP0;1;0q")
    restored = out.rstrip().endswith(b"\x1b[?1049l")
    print(f"sixel terminal: exit {code}, {images} images, terminal restored {restored}")
    assert code == 0 and images > 0 and restored, out[-400:]
    out, code = run(b"\x1b[?62;22c", [], 1)
    print(f"no sixel: exit {code}")
    assert code == 1 and b"does not report Sixel support" in out, out[-400:]
    print("ok")


if __name__ == "__main__":
    main()
