#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 pez2001. See LICENSE and THIRD_PARTY_NOTICES.
# /// script
# requires-python = ">=3.9"
# dependencies = ["numpy", "miniaudio"]
# ///
"""SIXAMP - a 1998-style MP3 player for your terminal, drawn in Sixel graphics.

    python3 sixamp.py [FILE|FOLDER|PLAYLIST.m3u ...]

Needs a Sixel terminal (foot, WezTerm, Konsole, mlterm, iTerm2, Windows
Terminal 1.22+, Contour, or `xterm -ti vt340`) and:  pip install numpy miniaudio

Plays MP3, FLAC, WAV and Ogg Vorbis on Linux, macOS and Windows.
Press ? in the player for all the keys.
"""
__version__ = "1.0.0"
import argparse
import collections
import json
import mmap
import os
import random
import re
import select
import signal
import struct
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

WINDOWS = os.name == "nt"
if not WINDOWS:
    import fcntl
    import termios
    import tty

try:
    import miniaudio
    import numpy as np
except ImportError as exc:
    sys.exit(f"sixamp: missing dependency ({exc.name}). Run:  pip install numpy miniaudio")

SR = 44100
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".ogg"}
LIST_EXTS = {".m3u", ".m3u8"}
STATE_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sixamp" / "state.json"
LOG_FILE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "sixamp.log"

# ------------------------------------------------------------------- palette

PALETTE = []
_pal_idx = {}


def C(value):
    """Return the palette index for a colour ('#rrggbb' or an (r, g, b) tuple)."""
    if isinstance(value, str):
        value = tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    value = tuple(int(v) for v in value)
    if value not in _pal_idx:
        _pal_idx[value] = len(PALETTE)
        PALETTE.append(value)
    return _pal_idx[value]


def ramp(a, b, n):
    a = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return [C(tuple(a[k] + (b[k] - a[k]) * i / max(n - 1, 1) for k in range(3))) for i in range(n)]


DESK = C("#0b0b14")
BLACK = C("#000000")
WHITE = C("#ffffff")
BODY = ramp("#43436a", "#25253f", 12)
EDGE_LT = C("#7a7aa4")
EDGE_MD = C("#55557c")
EDGE_DK = C("#14142a")
INSET_LT = C("#6a6a92")
GOLD_LT = C("#e6c668")
GOLD = C("#a98a3c")
GOLD_DK = C("#5d4a1f")
TITLE_BG = C("#1c1c36")
TITLE_TX = C("#f0e6c4")
FACE_LT = C("#e4e4ee")
FACE = C("#b4b4c8")
FACE_MD = C("#8c8ca6")
FACE_DK = C("#55556e")
FACE_PR = C("#8a8aa2")
ICON = C("#20203a")
GREEN = C("#00f000")
GREEN_MD = C("#00b000")
GREEN_DIM = C("#0a300a")
GREEN_FAINT = C("#1a551a")
LED_OFF = C("#2e4a2e")
LED_ON = C("#3cff3c")
SEL_BG = C("#0000c6")
GREY_TX = C("#9c9cb4")
LABEL = C("#d0d0e0")
RED = C("#f03030")
# The spectrum colours, top (0) to bottom (15), same order as the old viscolor table.
VIS = ramp("#ff2a00", "#ffc000", 6) + ramp("#f0e000", "#18b818", 10)
VIS_PEAK = C("#e0e0e8")
VOLBAR = ramp("#10d010", "#e0e000", 14) + ramp("#e0e000", "#f02000", 14)

# --------------------------------------------------------------------- fonts

# 5x7 glyphs from the Adafruit GFX Library (BSD licence, see THIRD_PARTY_NOTICES); "y" retouched.
_F57 = bytes.fromhex(
    "0000000000 00005f0000 0007000700 147f147f14 242a7f2a12 2313086462 3649562050"
    "0008070300 001c224100 0041221c00 2a1c7f1c2a 08083e0808 0080703000 0808080808"
    "0000606000 2010080402 3e5149453e 00427f4000 7249494946 2141494d33 1814127f10"
    "2745454539 3c4a494931 4121110907 3649494936 464949291e 0000140000 0040340000"
    "0008142241 1414141414 0041221408 0201590906 3e415d594e 7c1211127c 7f49494936"
    "3e41414122 7f4141413e 7f49494941 7f09090901 3e41415173 7f0808087f 00417f4100"
    "2040413f01 7f08142241 7f40404040 7f021c027f 7f0408107f 3e4141413e 7f09090906"
    "3e4151215e 7f09192946 2649494932 03017f0103 3f4040403f 1f2040201f 3f4038403f"
    "6314081463 0304780403 6159494d43 007f414141 0204081020 004141417f 0402010204"
    "4040404040 0003070800 2054547840 7f28444438 3844444428 384444287f 3854545418"
    "00087e0902 18a4a49c78 7f08040478 00447d4000 2040403d00 7f10284400 00417f4000"
    "7c04780478 7c08040478 3844444438 fc18242418 18242418fc 7c08040408 4854545424"
    "04043f4424 3c4040207c 1c2040201c 3c4030403c 4428102844 5ca0a0a07c 4464544c44"
    "00083641 00 00007f0000 0041360800 0201020402"
)
F57 = {}
for _i in range(95):
    _col = _F57[_i * 5:_i * 5 + 5]
    F57[chr(32 + _i)] = np.array([[(_c >> _r) & 1 for _c in _col] for _r in range(8)], bool)

_F35_SRC = """
A 010101111101101 B 110101110101110 C 011100100100011 D 110101101101110 E 111100110100111
F 111100110100100 G 011100101101011 H 101101111101101 I 111010010010111 J 001001001101010
K 101101110101101 L 100100100100111 M 101111111101101 N 110101101101101 O 010101101101010
P 110101110100100 Q 010101101110011 R 110101110101101 S 011100010001110 T 111010010010010
U 101101101101111 V 101101101101010 W 101101111111101 X 101101010101101 Y 101101010010010
Z 111001010100111 0 111101101101111 1 010110010010111 2 110001010100111 3 110001010001110
4 101101111001001 5 111100110001110 6 011100111101111 7 111001010010010 8 111101111101111
9 111101111001110 + 000010111010000 - 000000111000000 . 000000000000010 / 001001010100100
? 110001010000010 : 000010000010000
""".split()
F35 = {" ": np.zeros((5, 3), bool)}
for _ch, _bits in zip(_F35_SRC[0::2], _F35_SRC[1::2]):
    F35[_ch] = np.array([int(_b) for _b in _bits], bool).reshape(5, 3)


def asciify(s):
    s = unicodedata.normalize("NFKD", str(s)).replace("ß", "ss")
    return "".join(c if " " <= c <= "~" else ("" if unicodedata.combining(c) else "?") for c in s)


def text(a, x, y, s, c, font=F57, adv=6):
    H, W = a.shape
    for ch in s:
        g = font.get(ch)
        if g is None:
            g = font.get(ch.upper(), font["?"])
        h, w = g.shape
        x0, x1 = max(x, 0), min(x + w, W)
        y1 = min(y + h, H)
        if x0 < x1 and 0 <= y < H:
            sub = a[y:y1, x0:x1]
            sub[g[:y1 - y, x0 - x:x1 - x]] = c
        x += adv
        if x >= W:
            break


def tiny(a, x, y, s, c):
    text(a, x, y, s.upper(), c, F35, 4)


def tiny_center(a, x, y, w, s, c):
    tiny(a, x + (w - (len(s) * 4 - 1)) // 2, y, s, c)


# ------------------------------------------------------------ drawing helpers

def rect(a, x, y, w, h, c):
    a[max(y, 0):max(y + h, 0), max(x, 0):max(x + w, 0)] = c


def bevel(a, x, y, w, h, lt, dk):
    rect(a, x, y, w, 1, lt)
    rect(a, x, y, 1, h, lt)
    rect(a, x, y + h - 1, w, 1, dk)
    rect(a, x + w - 1, y, 1, h, dk)


def inset(a, x, y, w, h, c=BLACK):
    rect(a, x, y, w, h, c)
    bevel(a, x, y, w, h, EDGE_DK, INSET_LT)


def tri_right(a, x, y, h, c):
    for i in range(h):
        rect(a, x, y + i, min(i, h - 1 - i) + 1, 1, c)


def tri_left(a, x, y, h, c):
    n = (h + 1) // 2
    for i in range(h):
        w = min(i, h - 1 - i) + 1
        rect(a, x + n - w, y + i, w, 1, c)


def button(a, x, y, w, h, down=False):
    rect(a, x, y, w, h, FACE_PR if down else FACE)
    if down:
        bevel(a, x, y, w, h, FACE_DK, FACE_LT)
    else:
        bevel(a, x, y, w, h, FACE_LT, FACE_DK)
        rect(a, x + 1, y + h - 2, w - 2, 1, FACE_MD)
    return 1 if down else 0


def digit(a, x, y, n, on, off):
    # Segments touch only at their corners, so a lit digit never smears into a blob.
    segs = [(2, 0, 5, 2), (7, 2, 2, 4), (7, 7, 2, 4), (2, 11, 5, 2), (0, 7, 2, 4), (0, 2, 2, 4), (2, 6, 5, 1)]
    lit = {0: "abcdef", 1: "bc", 2: "abged", 3: "abgcd", 4: "fgbc", 5: "afgcd", 6: "afgedc",
           7: "abc", 8: "abcdefg", 9: "abcdfg"}
    names = "abcdefg"
    for i, (sx, sy, w, h) in enumerate(segs):
        rect(a, x + sx, y + sy, w, h, on if n is not None and names[i] in lit[n] else off)


def titlebar(a, w, title, x0=0):
    rect(a, 3, 2, w - 6, 10, TITLE_BG)
    for y in range(3, 11, 2):
        rect(a, 16, y, w - 46, 1, GOLD if y % 4 == 3 else GOLD_LT)
        rect(a, 16, y + 1, w - 46, 1, GOLD_DK)
    tw = len(title) * 6
    tx = (w - tw) // 2
    rect(a, tx - 5, 2, tw + 9, 10, TITLE_BG)
    text(a, tx, 3, title, TITLE_TX)
    # menu square
    button(a, 5, 3, 8, 8)
    rect(a, 7, 5, 4, 1, ICON)
    rect(a, 7, 7, 4, 1, ICON)
    # close
    button(a, w - 12, 3, 9, 8)
    for i in range(5):
        a[4 + i + 1 - 1, w - 10 + i] = ICON
        a[4 + i + 1 - 1, w - 6 - i] = ICON


def shell(w, h, title):
    a = np.zeros((h, w), np.uint8)
    for y in range(h):
        a[y, :] = BODY[min(y * len(BODY) // h, len(BODY) - 1)]
    bevel(a, 0, 0, w, h, EDGE_LT, EDGE_DK)
    bevel(a, 1, 1, w - 2, h - 2, EDGE_MD, EDGE_DK)
    titlebar(a, w, title)
    return a


# -------------------------------------------------------------- sixel encoder

_RLE = re.compile(rb"(.)\1{3,}", re.S)


def _rle(m):
    return b"!%d%s" % (len(m.group(0)), m.group(1))


def sixel(img, palette):
    """Encode an indexed image as Sixel. Registers are local to each image, so a
    region only needs as many colours as it actually uses."""
    h, w = img.shape
    pad = -h % 6
    if pad:
        img = np.vstack([img, np.repeat(img[-1:], pad, 0)])
        h += pad
    used = np.unique(img)
    if len(used) == 1 and used[0] == DESK:
        return b""
    lut = np.zeros(256, np.intp)
    lut[used] = np.arange(len(used))
    idx = lut[img]
    bands = h // 6
    codes = np.zeros((bands, len(used), w), np.uint8)
    bi = np.arange(bands)[:, None]
    xi = np.arange(w)[None, :]
    for r in range(6):
        codes[bi, idx[r::6], xi] |= 1 << r
    present = codes.any(axis=2)
    codes += 63
    out = [b'\x1bP0;1;0q"1;1;%d;%d' % (w, h)]
    for i, c in enumerate(used):
        if c == DESK:  # never painted, so the terminal's own background shows around the windows
            present[:, i] = False
            continue
        r, g, b = palette[c]
        out.append(b"#%d;2;%d;%d;%d" % (i, r * 100 // 255, g * 100 // 255, b * 100 // 255))
    for b in range(bands):
        rows = []
        for c in np.nonzero(present[b])[0]:
            rows.append(b"#%d%s" % (c, _RLE.sub(_rle, codes[b, c].tobytes().rstrip(b"?"))))
        out.append(b"$".join(rows))
        out.append(b"-")
    out[-1] = b"\x1b\\"
    return b"".join(out)


# ------------------------------------------------------------------- metadata

class Track:
    __slots__ = ("path", "title", "artist", "album", "seconds", "kbps", "khz", "channels", "scanned", "bad")

    def __init__(self, path):
        self.path = str(path)
        self.title = Path(path).stem
        self.artist = ""
        self.album = ""
        self.seconds = None
        self.kbps = 0
        self.khz = 0
        self.channels = 2
        self.scanned = False
        self.bad = False

    @property
    def label(self):
        return f"{self.artist} - {self.title}" if self.artist else self.title


def _id3_text(data):
    if not data:
        return ""
    enc, raw = data[0], data[1:]
    try:
        if enc == 0:
            s = raw.decode("latin-1")
        elif enc == 1:
            s = raw.decode("utf-16")
        elif enc == 2:
            s = raw.decode("utf-16-be")
        else:
            s = raw.decode("utf-8")
    except UnicodeDecodeError:
        s = raw.decode("latin-1")
    return s.split("\0")[0].strip()


def read_id3(path):
    """Return (title, artist, album, size_of_id3v2_tag), from ID3v2.2-2.4 then ID3v1."""
    title = artist = album = ""
    tag_size = 0
    with open(path, "rb") as f:
        head = f.read(10)
        if len(head) == 10 and head[:3] == b"ID3":
            ver = head[3]
            tag_size = 10 + ((head[6] & 127) << 21 | (head[7] & 127) << 14 | (head[8] & 127) << 7 | (head[9] & 127))
            body = f.read(tag_size - 10)
            pos = 0
            if head[5] & 0x40 and ver >= 3 and len(body) > 4:  # extended header
                n = struct.unpack(">I", body[:4])[0]
                pos = (n + 4 if ver == 3 else (body[0] & 127) << 21 | (body[1] & 127) << 14 |
                       (body[2] & 127) << 7 | (body[3] & 127))
            idlen, hdr = (3, 6) if ver == 2 else (4, 10)
            want_t, want_a, want_l = (b"TT2", b"TP1", b"TAL") if ver == 2 else (b"TIT2", b"TPE1", b"TALB")
            while pos + hdr <= len(body) and body[pos] != 0:
                fid = body[pos:pos + idlen]
                if ver == 2:
                    n = int.from_bytes(body[pos + 3:pos + 6], "big")
                elif ver == 3:
                    n = int.from_bytes(body[pos + 4:pos + 8], "big")
                else:
                    b = body[pos + 4:pos + 8]
                    n = (b[0] & 127) << 21 | (b[1] & 127) << 14 | (b[2] & 127) << 7 | (b[3] & 127)
                data = body[pos + hdr:pos + hdr + n]
                if fid == want_t:
                    title = _id3_text(data)
                elif fid == want_a:
                    artist = _id3_text(data)
                elif fid == want_l:
                    album = _id3_text(data)
                pos += hdr + n
        if not title:
            f.seek(0, 2)
            if f.tell() >= 128:
                f.seek(-128, 2)
                tail = f.read(128)
                if tail[:3] == b"TAG":
                    title = tail[3:33].split(b"\0")[0].decode("latin-1").strip()
                    artist = artist or tail[33:63].split(b"\0")[0].decode("latin-1").strip()
                    album = album or tail[63:93].split(b"\0")[0].decode("latin-1").strip()
    return title, artist, album, tag_size


def scan(track):
    try:
        size = os.path.getsize(track.path)
        tag = 0
        if track.path.lower().endswith(".mp3"):
            title, artist, album, tag = read_id3(track.path)
            track.title = title or track.title
            track.artist = artist
            track.album = album
        info = miniaudio.get_file_info(track.path)
        track.seconds = info.duration
        track.khz = round(info.sample_rate / 1000)
        track.channels = info.nchannels
        if info.duration > 0:
            track.kbps = round((size - tag) * 8 / info.duration / 1000)
    except Exception:
        track.bad = True
    track.scanned = True


# ---------------------------------------------------------------------- audio

EQ_FREQS = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000]


class Equalizer:
    """Ten-band linear-phase graphic EQ, run as FFT overlap-save over a stereo stream."""
    TAPS = 2047
    FFT = 8192
    HOP = FFT - TAPS + 1
    DELAY = (TAPS - 1) // 2

    def __init__(self):
        self.hist = np.zeros((self.TAPS - 1, 2), np.float32)
        self.pending = np.zeros((0, 2), np.float32)
        self.H = self._design([0.0] * 10, 0.0, False)
        self.H_old = None
        self.skip = self.DELAY
        self.left = 0

    def reset(self):
        self.hist[:] = 0
        self.pending = self.pending[:0]
        self.skip = self.DELAY
        self.left = 0

    def _design(self, bands, preamp, on):
        n = self.FFT // 2 + 1
        f = np.maximum(np.linspace(0, SR / 2, n), 1.0)
        db = np.interp(np.log(f), np.log(EQ_FREQS), bands) + preamp if on else np.zeros(n)
        zero = np.fft.irfft(10 ** (db / 20))
        h = np.roll(zero, self.DELAY)[:self.TAPS] * np.hanning(self.TAPS)
        return np.fft.rfft(h, self.FFT)[:, None]

    def set(self, bands, preamp, on):
        self.H_old, self.H = self.H, self._design(bands, preamp, on)

    def _block(self, block):
        data = np.vstack([self.hist, block])
        self.hist = data[-(self.TAPS - 1):].copy()
        X = np.fft.rfft(data, self.FFT, axis=0)
        y = np.fft.irfft(X * self.H, self.FFT, axis=0)[self.TAPS - 1:]
        old = self.H_old
        if old is not None:
            self.H_old = None
            y0 = np.fft.irfft(X * old, self.FFT, axis=0)[self.TAPS - 1:]
            fade = np.linspace(0, 1, len(y))[:, None]
            y = y0 * (1 - fade) + y * fade
        return y.astype(np.float32)

    def process(self, pcm, flush=False):
        data = np.vstack([self.pending, pcm])
        self.left += len(pcm)
        if flush:
            data = np.vstack([data, np.zeros((self.DELAY, 2), np.float32)])
            data = np.vstack([data, np.zeros((-len(data) % self.HOP, 2), np.float32)])
        out = []
        pos = 0
        while len(data) - pos >= self.HOP:
            out.append(self._block(data[pos:pos + self.HOP]))
            pos += self.HOP
        self.pending = data[pos:].copy()
        if not out:
            return np.zeros((0, 2), np.float32)
        y = np.vstack(out)
        if self.skip:
            n = min(self.skip, len(y))
            y = y[n:]
            self.skip -= n
        y = y[:self.left]  # the flush padding must not lengthen the track
        self.left -= len(y)
        return y


class Engine:
    """A decode thread fills a short FIFO; the device callback drains it."""
    FIFO = int(SR * 0.45)

    def __init__(self, null_output=False):
        self.lock = threading.Condition()
        self.fifo = collections.deque()
        self.fifo_frames = 0
        self.head = 0
        self.cmd = None
        self.stream = None
        self.eof = False
        self.eq = Equalizer()
        self.eq_change = None
        self.playing = False
        self.paused = False
        self.ended = False
        self.error = None
        self.frames = 0
        self.gain_now = np.array([0.7, 0.7], np.float32)
        self.gain_to = self.gain_now.copy()
        self.scope = np.zeros((2048, 2), np.float32)
        self.scope_pos = 0
        self.alive = True
        self.silent = False
        self.worker = threading.Thread(target=self._decode_loop, daemon=True)
        self.worker.start()
        self.device = None
        kw = dict(output_format=miniaudio.SampleFormat.FLOAT32, nchannels=2, sample_rate=SR,
                  buffersize_msec=120, app_name="sixamp")
        pump = self._pump()
        next(pump)
        try:
            if null_output:
                raise miniaudio.MiniaudioError("null output requested")
            self.device = miniaudio.PlaybackDevice(**kw)
            self.device.start(pump)
        except Exception:
            self.silent = True
            self.device = miniaudio.PlaybackDevice(backends=[miniaudio.Backend.NULL], **kw)
            pump = self._pump()
            next(pump)
            self.device.start(pump)

    def _decode_loop(self):
        while True:
            with self.lock:
                while self.alive and not self.cmd and (self.eof or not self.stream or self.fifo_frames >= self.FIFO):
                    self.lock.wait(0.1)
                if not self.alive:
                    return
                cmd, self.cmd = self.cmd, None
                change, self.eq_change = self.eq_change, None
                stream = self.stream
            if change:
                self.eq.set(*change)
            if cmd:
                stream = self._open(*cmd)
                if stream is None:
                    continue
            try:
                chunk = np.array(next(stream), np.float32).reshape(-1, 2)
                last = False
            except StopIteration:
                chunk = np.zeros((0, 2), np.float32)
                last = True
            except Exception as e:
                self.error = str(e)
                chunk = np.zeros((0, 2), np.float32)
                last = True
            out = np.clip(self.eq.process(chunk, flush=last), -1, 1)
            with self.lock:
                if self.cmd:
                    continue
                if len(out):
                    self.fifo.append(out)
                    self.fifo_frames += len(out)
                self.eof = last

    def _open(self, path, frame):
        try:
            stream = miniaudio.stream_file(path, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=2,
                                           sample_rate=SR, frames_to_read=4096, seek_frame=frame)
        except Exception as e:
            with self.lock:
                self.error = str(e)
                self.playing = False
                self.stream = None
            return None
        self.eq.reset()
        with self.lock:
            self.stream = stream
            self.eof = False
        return stream

    def _pump(self):
        n = yield b""
        while True:
            n = yield self._render(n).tobytes()

    def _render(self, n):
        out = np.zeros((n, 2), np.float32)
        got = 0
        with self.lock:
            active = self.playing and not self.paused
            while active and got < n and self.fifo:
                chunk = self.fifo[0]
                take = min(n - got, len(chunk) - self.head)
                out[got:got + take] = chunk[self.head:self.head + take]
                got += take
                self.head += take
                if self.head >= len(chunk):
                    self.fifo.popleft()
                    self.head = 0
                self.fifo_frames -= take
            if active:
                self.frames += got
                if got < n and self.eof and not self.fifo:
                    self.playing = False
                    self.ended = True
            self.lock.notify_all()
        k = min(n, len(self.scope))
        pos = self.scope_pos
        idx = (pos + np.arange(k)) % len(self.scope)
        self.scope[idx] = out[-k:]  # visualizers ignore the volume control, as they always did
        self.scope_pos = (pos + k) % len(self.scope)
        ramp_ = np.linspace(0, 1, n, dtype=np.float32)[:, None]
        out *= self.gain_now + (self.gain_to - self.gain_now) * ramp_
        self.gain_now = self.gain_to.copy()
        return out

    def load(self, path, seconds=0.0):
        with self.lock:
            self.fifo.clear()
            self.fifo_frames = 0
            self.head = 0
            self.frames = int(seconds * SR)
            self.cmd = (path, self.frames)
            self.playing = True
            self.paused = False
            self.ended = False
            self.eof = False
            self.error = None
            self.lock.notify_all()

    def stop(self):
        with self.lock:
            self.playing = False
            self.paused = False
            self.stream = None
            self.fifo.clear()
            self.fifo_frames = 0
            self.head = 0
            self.frames = 0

    def set_gain(self, volume, balance):
        v = (volume ** 2)
        self.gain_to = np.array([v * min(1, 1 - balance), v * min(1, 1 + balance)], np.float32)

    def set_eq(self, bands, preamp, on):
        with self.lock:
            self.eq_change = (list(bands), preamp, on)
            self.lock.notify_all()

    @property
    def position(self):
        return self.frames / SR

    def waveform(self, n):
        idx = (self.scope_pos - n + np.arange(n)) % len(self.scope)
        return self.scope[idx].mean(axis=1)

    def close(self):
        self.alive = False
        with self.lock:
            self.lock.notify_all()
        if self.device:
            self.device.close()


class Analyzer:
    def __init__(self, bars=19, height=16):
        self.n = 2048
        self.win = np.hanning(self.n)
        self.norm = self.win.sum() / 2
        edges = np.geomspace(55, 15000, bars + 1)
        self.bins = np.maximum(np.round(edges * self.n / SR).astype(int), 1)
        self.centre = np.sqrt(edges[:-1] * edges[1:])
        self.tilt = 3.0 * np.log2(self.centre / 150)
        self.height = height
        self.bars = np.zeros(bars)
        self.peaks = np.zeros(bars)
        self.hold = np.zeros(bars)

    def update(self, wave):
        mag = np.abs(np.fft.rfft(wave * self.win)) / self.norm
        cs = np.concatenate([[0.0], np.cumsum(mag ** 2)])
        lo, hi = self.bins[:-1], np.maximum(self.bins[1:], self.bins[:-1] + 1)
        energy = (cs[hi] - cs[lo]) / np.maximum(hi - lo, 1) * np.sqrt(hi - lo)
        db = 10 * np.log10(energy + 1e-12) + self.tilt
        h = np.clip((db + 50) / 44 * self.height, 0, self.height)
        self.bars = np.maximum(h, self.bars - 1.1)
        fresh = self.bars >= self.peaks
        self.hold = np.where(fresh, 8, self.hold - 1)
        self.peaks = np.where(fresh, self.bars, np.where(self.hold > 0, self.peaks, self.peaks - 0.35))
        self.peaks = np.maximum(self.peaks, 0)


# ---------------------------------------------------------------------- input

class PosixConsole:
    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.saved = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)

    def poll(self, timeout):
        if select.select([self.fd], [], [], timeout)[0]:
            return os.read(self.fd, 4096)
        return b""

    def size(self):
        rows, cols, xpx, ypx = struct.unpack(
            "HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8))
        return rows, cols, xpx, ypx

    def restore(self):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)


class WindowsConsole:
    """The same job on the Win32 console. It cannot select() on stdin, so a thread reads it."""
    VT_INPUT = 0x0200
    EXTENDED_FLAGS = 0x0080  # set without QUICK_EDIT, so clicks reach the player
    PROCESSED_OUTPUT = 0x0001
    VT_OUTPUT = 0x0004
    UTF8 = 65001

    def __init__(self):
        import ctypes
        import msvcrt
        import queue
        from ctypes import wintypes as w
        self.ctypes = ctypes
        k = self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetStdHandle.argtypes = [w.DWORD]
        k.GetStdHandle.restype = w.HANDLE
        k.GetConsoleMode.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        k.SetConsoleMode.argtypes = [w.HANDLE, w.DWORD]
        k.ReadConsoleW.argtypes = [w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID]
        k.SetConsoleCP.argtypes = k.SetConsoleOutputCP.argtypes = [w.UINT]
        self.hin = k.GetStdHandle(-10 & 0xFFFFFFFF)
        self.hout = k.GetStdHandle(-11 & 0xFFFFFFFF)
        self.in_mode, self.out_mode = w.DWORD(), w.DWORD()
        if not (k.GetConsoleMode(self.hin, ctypes.byref(self.in_mode))
                and k.GetConsoleMode(self.hout, ctypes.byref(self.out_mode))):
            sys.exit("sixamp: run me in Windows Terminal, not through a pipe or Git Bash's mintty.")
        self.cp = (k.GetConsoleCP(), k.GetConsoleOutputCP())
        k.SetConsoleCP(self.UTF8)
        k.SetConsoleOutputCP(self.UTF8)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        vt = k.SetConsoleMode(self.hout, self.out_mode.value | self.PROCESSED_OUTPUT | self.VT_OUTPUT)
        vt = vt and k.SetConsoleMode(self.hin, self.VT_INPUT | self.EXTENDED_FLAGS)
        if not vt:
            self.restore()
            sys.exit("sixamp: this console has no VT support. Use Windows Terminal 1.22 or newer.")
        self.q = queue.Queue()
        self.empty = queue.Empty
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        ctypes = self.ctypes
        buf = ctypes.create_unicode_buffer(1024)
        got = ctypes.c_ulong()
        while self.k.ReadConsoleW(self.hin, buf, 1024, ctypes.byref(got), None):
            raw = ctypes.string_at(buf, got.value * 2)
            self.q.put(raw.decode("utf-16-le", "ignore").encode())

    def poll(self, timeout):
        try:
            data = self.q.get(timeout=timeout) if timeout > 0 else self.q.get_nowait()
            while not self.q.empty():
                data += self.q.get_nowait()
            return data
        except self.empty:
            return b""

    def size(self):
        s = os.get_terminal_size(sys.stdout.fileno())
        return s.lines, s.columns, 0, 0  # the console API has no pixel size; probe_terminal asks

    def restore(self):
        self.k.SetConsoleMode(self.hin, self.in_mode.value)
        self.k.SetConsoleMode(self.hout, self.out_mode.value)
        self.k.SetConsoleCP(self.cp[0])
        self.k.SetConsoleOutputCP(self.cp[1])


class Input:
    MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
    CSI = re.compile(r"\x1b\[([0-9;?]*)([ -/]*[@-~])")
    SS3 = re.compile(r"\x1bO(.)")
    NAMES = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "Z": "backtab",
             "1~": "home", "7~": "home", "4~": "end", "8~": "end", "3~": "delete", "2~": "insert",
             "5~": "pageup", "6~": "pagedown"}

    def __init__(self, console):
        self.console = console
        self.buf = ""
        self.tail = b""

    def read(self, timeout):
        data = self.console.poll(timeout)
        if data:
            data = self.tail + data
            try:
                self.buf += data.decode()
                self.tail = b""
            except UnicodeDecodeError as e:
                self.buf += data[:e.start].decode()
                self.tail = data[e.start:] if len(data) - e.start < 4 else b""
        events = []
        while self.buf:
            b = self.buf
            if b[0] != "\x1b":
                events.append(("key", {"\r": "enter", "\n": "enter", "\x7f": "backspace", "\b": "backspace",
                                       "\t": "tab", " ": "space"}.get(b[0], b[0])))
                self.buf = b[1:]
                continue
            m = self.MOUSE.match(b)
            if m:
                events.append(("mouse", int(m[1]), int(m[2]), int(m[3]), m[4] == "M"))
                self.buf = b[m.end():]
                continue
            m = self.CSI.match(b)
            if m:
                key = m[2] if not m[1] or m[2] != "~" else m[1].split(";")[0] + "~"
                if m[2] in "ABCDHF" and m[1] == "1":
                    key = m[2]
                if key in self.NAMES:
                    events.append(("key", self.NAMES[key]))
                self.buf = b[m.end():]
                continue
            m = self.SS3.match(b)
            if m:
                if m[1] in self.NAMES:
                    events.append(("key", self.NAMES[m[1]]))
                self.buf = b[m.end():]
                continue
            if len(b) == 1 or (b.startswith("\x1b[") and len(b) < 24):
                more = self.console.poll(0.03)
                if more:
                    self.buf += more.decode(errors="ignore")
                    continue
                if len(b) == 1:
                    events.append(("key", "esc"))
                self.buf = ""
                continue
            self.buf = b[2:]
        return events


def probe_terminal(console):
    """Ask the terminal what it can do. Everything has a safe default."""
    rows0, cols0 = console.size()[:2]
    info = {"sixel": False, "cell": None, "pixels": None, "mouse_px": False, "registers": None,
            "rows0": rows0 or 24, "cols0": cols0 or 80}
    out = sys.stdout.buffer
    out.write(b"\x1b[?1;3;256S\x1b[?1;1;0S\x1b[16t\x1b[14t\x1b[?1016$p\x1b[c")
    out.flush()
    buf = b""
    deadline = time.monotonic() + 0.6
    while time.monotonic() < deadline:
        buf += console.poll(0.05)
        if re.search(rb"\x1b\[\?[0-9;]*c", buf):
            break
    m = re.search(rb"\x1b\[\?([0-9;]*)c", buf)
    if m and b"4" in m[1].split(b";"):
        info["sixel"] = True
    m = re.search(rb"\x1b\[6;(\d+);(\d+)t", buf)
    if m and int(m[1]) and int(m[2]):
        info["cell"] = (int(m[2]), int(m[1]))
    m = re.search(rb"\x1b\[4;(\d+);(\d+)t", buf)
    if m and int(m[1]) and int(m[2]):
        info["pixels"] = (int(m[2]), int(m[1]))
    m = re.search(rb"\x1b\[\?1016;(\d+)\$y", buf)
    if m and m[1] in (b"1", b"2", b"3"):
        info["mouse_px"] = True
    for m in re.finditer(rb"\x1b\[\?1;0;(\d+)S", buf):
        info["registers"] = int(m[1])
    info["raw"] = buf
    return info


class MouseShare:
    """pyterm (formerly pyvterm) publishes its live cell size and exact pointer position. ConPTY only reports whole
    cells and may eat size queries, and a theme can change the cell size while we run."""

    # Fixed names, because the first tab starts before the plugin loads.
    NAMES = {"pyterm": "pyterm_sixamp_share", "pyvterm": "pyvterm_sixamp_share"}

    def __init__(self, name):
        if WINDOWS:
            self.map = mmap.mmap(-1, 20, tagname=name)
        else:
            with open(os.path.join(tempfile.gettempdir(), f"{name}_{os.getuid()}"), "rb") as f:
                self.map = mmap.mmap(f.fileno(), 20, access=mmap.ACCESS_READ)
        if self.map[:4] != b"PVTM":
            raise ValueError("no pointer data")

    def read(self):
        return struct.unpack("<ii", self.map[4:12])

    def cell(self):
        return struct.unpack("<ii", self.map[12:20])


def open_mouse_share():
    name = MouseShare.NAMES.get(os.environ.get("TERM_PROGRAM", ""))
    if not name:
        return None
    try:
        return MouseShare(name)
    except (OSError, TypeError, ValueError):
        return None


# Next to this file when run from a checkout; installed copies log to the cache folder instead.
_here = Path(__file__).resolve().parent
DEBUG_FILE = (LOG_FILE.parent if "site-packages" in _here.parts or "dist-packages" in _here.parts
              else _here) / "sixamp-debug.log"
debug_on = False


def debug(*items):
    if debug_on:
        with open(DEBUG_FILE, "a", encoding="utf-8") as f:
            print(time.strftime("%H:%M:%S"), *items, file=f)


# ------------------------------------------------------------------ the modal

class Modal:
    """A list window in the same skin: file browser, jump box, help and info."""

    def __init__(self, title, items, on_pick=None, hint="", on_add=None, parent=None, on_add_all=None):
        self.title = title
        self.items = items
        self.on_pick = on_pick
        self.on_add = on_add
        self.on_add_all = on_add_all
        self.parent = parent
        self.hint = hint
        self.query = ""
        self.sel = 0
        self.top = 0
        self.rows = 8
        self.rect = (0, 0, 0, 0)
        self.note = ""

    @property
    def filterable(self):
        return self.on_pick is not None

    @property
    def visible(self):
        q = self.query.lower()
        return [i for i in self.items if q in i[0].lower()] if q else self.items

    def move(self, d):
        n = len(self.visible)
        self.sel = max(0, min(n - 1, self.sel + d)) if n else 0

    def keep_visible(self):
        self.top = max(0, min(self.top, max(len(self.visible) - self.rows, 0)))
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + self.rows:
            self.top = self.sel - self.rows + 1

    def draw(self, a, rh=9):
        self.rh = rh
        H, W = a.shape
        w = min(W - 8, 330)
        h = min(H - 8, 190)
        x, y = (W - w) // 2, (H - h) // 2
        self.rect = (x, y, w, h)
        box = shell(w, h, asciify(self.title)[-((w - 70) // 6):])
        filter_h = 12 if self.filterable else 0
        inset(box, 8, 16, w - 16, filter_h or 1, BLACK)
        if self.filterable:
            text(box, 11, 18, ("Find: " + asciify(self.query) + "_")[-(w - 24) // 6:], GREEN)
        ly = 16 + filter_h + 2
        lh = h - ly - 16
        inset(box, 8, ly, w - 16, lh)
        self.rows = max((lh - 4) // rh, 1)
        self.keep_visible()
        vis = self.visible
        chars = (w - 26) // 6
        for r, item in enumerate(vis[self.top:self.top + self.rows]):
            ry = ly + 2 + r * rh + (rh - 9) // 2
            if self.filterable and self.top + r == self.sel:
                rect(box, 10, ly + 1 + r * rh, w - 20, rh, SEL_BG)
            col = WHITE if (self.filterable and self.top + r == self.sel) else GOLD_LT if item[2] else GREEN
            text(box, 12, ry, asciify(item[0])[:chars], col)
        if not vis:
            text(box, 12, ly + 2, "(nothing here)", GREY_TX)
        if len(vis) > self.rows:
            th = max(lh * self.rows // len(vis), 6)
            ty = ly + (lh - th) * self.top // max(len(vis) - self.rows, 1)
            rect(box, w - 12, ty, 3, th, FACE_MD)
        tiny(box, 9, h - 11, (self.note or self.hint)[:(w - 16) // 4], LABEL)
        a[y:y + h, x:x + w] = box


# ---------------------------------------------------------------- application

PRESETS = {
    "Flat": [0] * 10, "Rock": [5, 3, -2, -3, -1, 2, 5, 6, 6, 6], "Pop": [-1, 2, 4, 5, 3, 0, -1, -1, -1, -1],
    "Classical": [0, 0, 0, 0, 0, 0, -4, -4, -4, -6], "Dance": [6, 4, 1, 0, 0, -3, -4, -4, 0, 0],
    "Full Bass": [8, 8, 7, 4, 1, -3, -6, -7, -8, -8], "Full Treble": [-7, -7, -7, -3, 1, 6, 9, 9, 9, 10],
    "Laptop": [3, 6, 3, -2, -1, 1, 3, 6, 8, 9], "Live": [-3, 0, 3, 4, 4, 4, 3, 2, 2, 2],
    "Club": [0, 0, 3, 4, 4, 4, 2, 0, 0, 0], "Reggae": [0, 0, 0, -4, 0, 5, 5, 0, 0, 0],
}
PL_BUTTONS = (("pl_add", 12, "ADD"), ("pl_rem", 38, "REM"), ("pl_clr", 64, "CLR"), ("pl_sort", 90, "SORT"),
              ("pl_fmt", 116, "FMT"))
FORMATS = [("Artist - Title", "%n. %a - %t"), ("Title - Artist", "%n. %t - %a"), ("Title", "%n. %t"),
           ("Artist - Album - Title", "%n. %a - %l - %t"), ("File name", "%n. %f"), ("No numbers", "%a - %t")]
HELP = """z  previous      x  play
c  pause         v  stop
b  next          l  open files
Left/Right  seek 5 seconds
+ -  volume      [ ]  balance
Up/Down  list    Enter  play it
PgUp/PgDn page   Del  remove it
j  jump          i  track info
r  repeat        s  shuffle
e  EQ window     p  playlist
o  EQ on/off     n  next preset
d  scale         t  time mode
w  visualizer    f  list format
q  quit
The mouse works on everything.
Wheel: list, sliders, else volume."""


SETTLE = 0.2  # seconds without a size change before repainting; longer than the plugin's HOLD


class App:
    TW = 275
    WH = 116

    def __init__(self, args, term, console):
        self.args = args
        self.term = term
        self.console = console
        self.out = sys.stdout.fileno()
        self.engine = Engine(null_output=args.no_audio)
        self.share = open_mouse_share()
        self.exact_mouse = bool(self.share) or term["mouse_px"]
        debug("exact mouse", self.exact_mouse, "shared", bool(self.share))
        self.analyzer = Analyzer()
        self.tracks = []
        self.cur = -1
        self.sel = 0
        self.top = 0
        self.volume = 0.8
        self.balance = 0.0
        self.eq_bands = [0.0] * 10
        self.preamp = 0.0
        self.eq_on = True
        self.preset = "Flat"
        self.shuffle = False
        self.repeat = False
        self.format = FORMATS[0][1]
        self.order = []
        self.history = []
        self.vis_mode = 0
        self.show_eq = True
        self.show_pl = True
        self.remaining = False
        self.cwd = str(Path.cwd())
        self.max_scale = args.scale or 3
        self.fixed_scale = bool(args.scale)
        self.modal = None
        self.pressed = None
        self.drag = None
        self.drag_pos = None
        self.flash = ""
        self.flash_until = 0
        self.last_click = (0, None)
        self.marquee = 0.0
        self.last_title = None
        self.dirty_all = True
        self.running = True
        self.need_layout = True
        self.prev = None
        self.frame_no = 0
        self.pl_cache_key = None
        self.pl_cache = None
        self.blink = 0
        self.scan_q = collections.deque()
        self.scan_cv = threading.Condition()
        threading.Thread(target=self._scan_loop, daemon=True).start()
        self.skin_main = self._build_main()
        self.skin_eq = self._build_eq()
        self.load_state()
        self.format = args.format or self.format
        if args.paths:
            self.tracks = []
            self.add_paths(args.paths)
            self.sel = 0
        self.engine.set_gain(self.volume, self.balance)
        self.engine.set_eq(self.eq_bands, self.preamp, self.eq_on)
        if args.paths and self.tracks:
            self.play(0)

    # ---- state

    def load_state(self):
        try:
            s = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return
        try:
            self.volume = float(s.get("volume", self.volume))
            self.balance = float(s.get("balance", 0))
            bands = s.get("eq", self.eq_bands)
            if len(bands) == 10:
                self.eq_bands = [float(b) for b in bands]
            self.preamp = float(s.get("preamp", 0))
            self.eq_on = bool(s.get("eq_on", True))
            self.preset = str(s.get("preset", "Flat"))
            self.shuffle = bool(s.get("shuffle", False))
            self.repeat = bool(s.get("repeat", False))
            self.format = str(s.get("format") or self.format)
            self.vis_mode = int(s.get("vis", 0)) % 3
            self.show_eq = bool(s.get("show_eq", True))
            self.show_pl = bool(s.get("show_pl", True))
            self.remaining = bool(s.get("remaining", False))
            if os.path.isdir(s.get("cwd", "")):
                self.cwd = s["cwd"]
            if not self.args.paths:
                self.add_tracks([p for p in s.get("playlist", []) if os.path.exists(p)])
                self.sel = min(int(s.get("sel", 0)), max(len(self.tracks) - 1, 0))
        except (TypeError, ValueError):
            pass

    def save_state(self):
        s = dict(volume=self.volume, balance=self.balance, eq=self.eq_bands, preamp=self.preamp, eq_on=self.eq_on,
                 preset=self.preset, shuffle=self.shuffle, repeat=self.repeat, format=self.format, vis=self.vis_mode,
                 show_eq=self.show_eq, show_pl=self.show_pl, remaining=self.remaining, cwd=self.cwd,
                 playlist=[t.path for t in self.tracks], sel=self.sel)
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(s, indent=1))
        except OSError:
            pass

    # ---- playlist

    def _scan_loop(self):
        while True:
            with self.scan_cv:
                while not self.scan_q:
                    self.scan_cv.wait()
                t = self.scan_q.popleft()
            scan(t)

    def add_tracks(self, paths):
        n = 0
        for p in paths:
            t = Track(p)
            self.tracks.append(t)
            with self.scan_cv:
                self.scan_q.append(t)
                self.scan_cv.notify()
            n += 1
        self.pl_cache_key = None
        self.rebuild_order()
        return n

    def expand(self, path):
        p = Path(path).expanduser()
        if p.is_dir():
            found = []
            for root, dirs, files in os.walk(p):
                dirs.sort(key=str.lower)
                for f in sorted(files, key=str.lower):
                    if Path(f).suffix.lower() in AUDIO_EXTS:
                        found.append(os.path.join(root, f))
            return found
        if p.suffix.lower() in LIST_EXTS:
            found = []
            try:
                raw = p.read_bytes()
                try:
                    lines = raw.decode("utf-8-sig").splitlines()
                except UnicodeDecodeError:
                    lines = raw.decode("latin-1").splitlines()
            except OSError:
                return []
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#"):
                    q = (p.parent / line).resolve()
                    if q.is_file() and q.suffix.lower() in AUDIO_EXTS:
                        found.append(str(q))
            return found
        return [str(p.resolve())] if p.is_file() and p.suffix.lower() in AUDIO_EXTS else []

    def add_paths(self, paths):
        return self.add_tracks([f for p in paths for f in self.expand(p)])

    def entry(self, i, t):
        """One playlist line, from the format template. Empty tags leave no stray dashes."""
        path = Path(t.path)
        values = {"n": str(i + 1), "a": t.artist, "t": t.title, "l": t.album, "f": path.name,
                  "d": path.parent.name, "%": "%"}
        line = re.sub(r"%(.)", lambda m: values.get(m[1], m[0]), self.format)
        line = re.sub(r"(\s+-)+\s+-\s+", " - ", line)
        line = re.sub(r"^(\d+\.)\s+-\s+", r"\1 ", line.strip())
        line = re.sub(r"^-\s+", "", line)
        return re.sub(r"\s+-$", "", line)

    def cycle_format(self):
        known = [f for _, f in FORMATS]
        i = known.index(self.format) if self.format in known else -1
        name, self.format = FORMATS[(i + 1) % len(FORMATS)]
        self.pl_cache_key = None
        self.message(f"LIST FORMAT: {name}", 2)

    def rebuild_order(self):
        self.order = list(range(len(self.tracks)))
        if self.shuffle:
            random.shuffle(self.order)

    def remove(self, i):
        if not 0 <= i < len(self.tracks):
            return
        del self.tracks[i]
        if i == self.cur:
            self.cur = -1
            self.engine.stop()
        elif i < self.cur:
            self.cur -= 1
        self.sel = max(0, min(self.sel, len(self.tracks) - 1))
        self.history = [h - (h > i) for h in self.history if h != i]
        self.rebuild_order()
        self.pl_cache_key = None

    # ---- transport

    def message(self, s, seconds=2.0):
        self.flash = s
        self.flash_until = time.monotonic() + seconds

    def play(self, i, remember=True):
        if not 0 <= i < len(self.tracks):
            return
        if remember and self.cur >= 0 and self.cur != i:
            self.history.append(self.cur)
        self.cur = i
        self.sel = i
        self.ensure_visible()
        self.engine.load(self.tracks[i].path)
        self.marquee = 0
        self.pl_cache_key = None

    def play_pressed(self):
        if self.engine.paused:
            self.engine.paused = False
        elif self.cur >= 0:
            self.play(self.cur, remember=False)
        elif self.tracks:
            self.play(self.sel)
        else:
            self.open_browser()

    def pause_pressed(self):
        if self.engine.playing:
            self.engine.paused = not self.engine.paused

    def stop_pressed(self):
        self.engine.stop()

    def seek(self, seconds):
        if self.cur < 0 or not self.engine.playing:
            return
        t = self.tracks[self.cur]
        limit = max((t.seconds or 0) - 1, 0)
        paused = self.engine.paused
        self.engine.load(t.path, max(0, min(seconds, limit)))
        self.engine.paused = paused

    def next_track(self, auto=False):
        if not self.tracks:
            return
        if self.shuffle:
            at = self.order.index(self.cur) if self.cur in self.order else -1
            nxt = at + 1
            if nxt >= len(self.order):
                if auto and not self.repeat:
                    self.engine.stop()
                    return
                random.shuffle(self.order)
                nxt = 0
            self.play(self.order[nxt])
        else:
            nxt = self.cur + 1
            if nxt >= len(self.tracks):
                if auto and not self.repeat:
                    self.engine.stop()
                    self.cur = max(self.cur, 0)
                    return
                nxt = 0
            self.play(nxt)

    def prev_track(self):
        if not self.tracks:
            return
        if self.shuffle and self.history:
            self.play(self.history.pop(), remember=False)
        else:
            self.play((self.cur - 1) % len(self.tracks))

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v))
        self.engine.set_gain(self.volume, self.balance)
        self.message(f"VOLUME: {round(self.volume * 100)}%", 1)

    def set_balance(self, b):
        self.balance = max(-1.0, min(1.0, b))
        if abs(self.balance) < 0.06:
            self.balance = 0.0
        self.engine.set_gain(self.volume, self.balance)
        side = "CENTER" if not self.balance else f"{round(abs(self.balance) * 100)}% {'LEFT' if self.balance < 0 else 'RIGHT'}"
        self.message(f"BALANCE: {side}", 1)

    def step_balance(self, d):
        self.set_balance(round(self.balance * 10 + d) / 10)  # 10% steps always pass through centre

    def set_eq(self):
        self.engine.set_eq(self.eq_bands, self.preamp, self.eq_on)

    def cycle_preset(self):
        names = list(PRESETS)
        i = names.index(self.preset) if self.preset in names else -1
        self.preset = names[(i + 1) % len(names)]
        self.eq_bands = [float(v) for v in PRESETS[self.preset]]
        self.eq_on = True
        self.set_eq()
        self.message(f"EQ PRESET: {self.preset}")

    def toggle_scale(self):
        self.fixed_scale = True
        best = self.best_layout(99)[0]
        self.max_scale = self.scale % max(best, 1) + 1 if best > 1 else 1
        self.need_layout = True

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        self.rebuild_order()
        self.message(f"SHUFFLE: {'ON' if self.shuffle else 'OFF'}", 1)

    def toggle_repeat(self):
        self.repeat = not self.repeat
        self.message(f"REPEAT: {'ON' if self.repeat else 'OFF'}", 1)

    # ---- modals

    def open_browser(self, path=None):
        path = str(Path(path or self.cwd).expanduser())
        self.cwd = path
        items = []
        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError as e:
            self.message(f"CANNOT OPEN FOLDER: {e.strerror}")
            entries = []
        if str(Path(path).parent) != path:
            items.append(("../", str(Path(path).parent), True))
        elif WINDOWS:
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                drive = letter + ":\\"
                if os.path.exists(drive) and drive.lower() != path.lower():
                    items.append((drive, drive, True))
        for e in entries:
            if e.name.startswith("."):
                continue
            if e.is_dir():
                items.append((e.name + "/", e.path, True))
            elif Path(e.name).suffix.lower() in AUDIO_EXTS | LIST_EXTS:
                items.append((e.name, e.path, False))

        def pick(item):
            if item[2]:
                self.open_browser(item[1])
                return
            start = len(self.tracks)
            if self.add_paths([item[1]]):
                self.play(start)
            self.modal = None

        def add(item):
            n = self.add_paths([item[1]])
            self.modal.note = f"Added {n} file{'s' * (n != 1)}"
            self.modal.move(1)

        def add_all(_):
            n = self.add_paths([path])
            self.modal.note = f"Added {n} file{'s' * (n != 1)}"

        self.modal = Modal(path, items, pick, "Enter open  Space add  Ctrl-A add all  Bksp up  Esc close",
                           add, str(Path(path).parent), add_all)

    def open_jump(self):
        items = [(self.entry(i, t), i, False) for i, t in enumerate(self.tracks)]

        def pick(item):
            self.play(item[1])
            self.modal = None
        self.modal = Modal("Jump to file", items, pick, "Type to search  Enter play  Esc close")

    def open_help(self):
        self.modal = Modal("Sixamp help", [(l, None, False) for l in HELP.splitlines()], hint="Esc close")

    def open_info(self):
        i = self.cur if self.cur >= 0 else self.sel
        if not 0 <= i < len(self.tracks):
            return
        t = self.tracks[i]
        rows = [f"Title:   {t.title}", f"Artist:  {t.artist or '-'}", f"Album:   {t.album or '-'}", f"Length:  {self.mmss(t.seconds or 0)}",
                f"Bitrate: {t.kbps} kbps", f"Sample:  {t.khz} kHz, {'stereo' if t.channels > 1 else 'mono'}", ""]
        p = t.path
        rows += [p[i:i + 50] for i in range(0, len(p), 50)]
        self.modal = Modal("Track info", [(r, None, False) for r in rows], hint="Esc close")

    # ---- static skins

    def _build_main(self):
        a = shell(self.TW, self.WH, "SIXAMP")
        inset(a, 9, 19, 95, 42)
        inset(a, 108, 19, 158, 19)
        inset(a, 110, 41, 22, 11)
        tiny(a, 134, 44, "KBPS", LABEL)
        inset(a, 156, 41, 16, 11)
        tiny(a, 174, 44, "KHZ", LABEL)
        return a

    def _build_eq(self):
        a = shell(self.TW, self.WH, "SIXAMP EQUALIZER")
        inset(a, 86, 17, 113, 19)
        rect(a, 87, 26, 111, 1, GREEN_FAINT)
        tiny(a, 43, 40, "+12", LABEL)
        tiny(a, 47, 66, "0", LABEL)
        tiny(a, 43, 93, "-12", LABEL)
        tiny(a, 57, 66, "DB", GREY_TX)
        tiny_center(a, 12, 104, 32, "PREAMP", LABEL)
        names = ["60", "170", "310", "600", "1K", "3K", "6K", "12K", "14K", "16K"]
        for i, n in enumerate(names):
            tiny_center(a, 76 + 18 * i, 104, 18, n, LABEL)
        return a

    # ---- layout

    def best_layout(self, cap):
        W, H = self.avail
        n_lower = int(self.show_eq)
        stack_h = self.WH * (1 + n_lower) + (self.WH if self.show_pl else 0)
        opts = [(min(W // self.TW, H // stack_h), 0)]
        if self.show_pl:
            two_h = self.WH * (1 + n_lower)
            opts.append((min(W // (self.TW * 2), H // two_h), 1))
        s, side = max(opts, key=lambda o: (o[0], o[1]))
        return max(min(s, cap), 0), side, stack_h

    def layout(self):
        rows, cols, xpx, ypx = self.console.size()
        cols, rows = cols or 80, rows or 24
        cell = self.term["cell"]
        if xpx and ypx and not self.args.cell:
            cell = (xpx // cols, ypx // rows)
        elif not cell and self.term["pixels"]:  # measured at startup, so only rows/cols at startup apply
            cell = (self.term["pixels"][0] // self.term["cols0"], self.term["pixels"][1] // self.term["rows0"])
        if self.share and min(self.share.cell()) > 0:
            cell = self.share.cell()
        self.cell_guessed = not cell
        cell = cell or (10, 20)
        self.cw, self.ch = max(cell[0], 1), max(cell[1], 1)
        self.cols, self.rows = cols, rows
        # Two rows stay free: a Sixel image that reaches the last row scrolls the screen.
        self.avail = (cols * self.cw, max(rows - 2, 1) * self.ch)
        s, side, stack_h = self.best_layout(self.max_scale)
        self.too_small = s < 1
        s = max(s, 1)
        self.scale = s
        nh_av = self.avail[1] // s
        y = self.WH * (1 + int(self.show_eq))
        if side:
            self.nw, self.pl_pos = self.TW * 2, (self.TW, 0)
            self.pl_h = min(max(y, 232), max(nh_av, 58))
            self.nh = max(y, self.pl_h)
        else:
            self.nw = self.TW
            self.pl_pos = (0, y)
            self.pl_h = max(58, min(nh_av - y, 290))
            self.nh = y + (self.pl_h if self.show_pl else 0)
        self.eq_pos = (0, self.WH)
        self.pl_skin = None
        # A canvas that fills a whole number of cells.
        self.pw = -(-self.nw * s // self.cw) * self.cw
        self.ph = -(-self.nh * s // self.ch) * self.ch
        # Never draw past the screen: on a too-small terminal the skin is cropped, not scrolled.
        self.pw = min(self.pw, cols * self.cw)
        self.ph = min(self.ph, max(rows - 2, 1) * self.ch)
        self.ox = max((cols - self.pw // self.cw) // 2, 0)
        self.prev = None
        self.dirty_all = True
        self.pl_cache_key = None
        self.hits = self._build_hits()
        self.need_layout = False
        debug("layout cell", (self.cw, self.ch), "guessed", self.cell_guessed, "terminal", (cols, rows),
              "scale", s, "canvas", (self.pw, self.ph), "col_offset", self.ox, "side", side)

    def _build_hits(self):
        h = []

        def add(name, x, y, w, ht, ox=0, oy=0):
            h.append((name, x + ox, y + oy, w, ht))
        for n, x, w in (("prev", 16, 23), ("play", 39, 23), ("pause", 62, 23), ("stop", 85, 23), ("next", 108, 22)):
            add(n, x, 88, w, 18)
        add("eject", 136, 89, 22, 16)
        add("shuffle", 164, 89, 47, 15)
        add("repeat", 211, 89, 28, 15)
        add("toggle_eq", 219, 57, 23, 12)
        add("toggle_pl", 242, 57, 23, 12)
        add("vol", 107, 56, 68, 11)
        add("bal", 177, 56, 38, 11)
        add("pos", 16, 72, 243, 10)
        add("time", 24, 22, 76, 18)
        add("vis", 24, 42, 76, 16)
        add("quit", 263, 3, 9, 8)
        for i, n in enumerate("oidv"):
            add("clutter_" + n, 10, 21 + i * 9, 10, 9)
        if self.show_eq:
            ox, oy = self.eq_pos
            add("eq_on", 14, 18, 26, 12, ox, oy)
            add("eq_reset", 41, 18, 32, 12, ox, oy)
            add("eq_presets", 217, 18, 44, 12, ox, oy)
            add("eq_close", 263, 3, 9, 8, ox, oy)
            add("preamp", 21, 38, 14, 63, ox, oy)
            for i in range(10):
                add(f"band{i}", 78 + 18 * i, 38, 14, 63, ox, oy)
        if self.show_pl:
            ox, oy = self.pl_pos
            H = self.pl_h
            add("pl_close", 263, 3, 9, 8, ox, oy)
            add("pl_list", 12, 20, 243, H - 62, ox, oy)
            add("pl_bar", 257, 20, 9, H - 62, ox, oy)
            for n, x, _ in PL_BUTTONS:
                add(n, x, H - 32, 24, 18, ox, oy)
        return h

    # ---- rendering

    @staticmethod
    def mmss(s):
        s = int(max(s, 0))
        return f"{s // 60}:{s % 60:02d}"

    def current_title(self):
        now = time.monotonic()
        if now < self.flash_until:
            return self.flash, True
        if self.engine.error:
            return "CANNOT PLAY THIS FILE", True
        if self.cur >= 0 and self.cur < len(self.tracks):
            t = self.tracks[self.cur]
            return f"{asciify(self.entry(self.cur, t))} ({self.mmss(t.seconds or 0)})", False
        return "SIXAMP - PLAYING SIX PIXELS AT A TIME  -  PRESS L TO OPEN FILES", False

    def draw_main(self, a, dt):
        e = self.engine
        p = self.pressed
        a[:self.WH, :self.TW] = self.skin_main
        # clutterbar
        for i, n in enumerate("OIDV"):
            down = p == "clutter_" + n.lower()
            tiny(a, 13 + (1 if down else 0), 23 + i * 9, n, WHITE if down else GREY_TX)
        # status icon
        if e.playing and not e.paused:
            tri_right(a, 25, 27, 9, GREEN)
        elif e.playing:
            rect(a, 25, 27, 3, 9, GOLD_LT)
            rect(a, 30, 27, 3, 9, GOLD_LT)
        else:
            rect(a, 25, 28, 7, 7, RED)
        # time
        t = self.tracks[self.cur] if 0 <= self.cur < len(self.tracks) else None
        pos = e.position
        if self.drag == "pos" and self.drag_pos is not None and t:
            pos = self.drag_pos * (t.seconds or 0)
        show = e.playing and t is not None
        blink_off = e.paused and int(time.monotonic() * 2) % 2
        if show and self.remaining and t.seconds:
            pos = max(t.seconds - pos, 0)
            rect(a, 37, 29, 5, 2, GREEN)
        m, s = int(pos) // 60 % 100, int(pos) % 60
        ds = [m // 10, m % 10, s // 10, s % 10] if show and not blink_off else [None] * 4
        for dx, n in zip((44, 56, 73, 85), ds):
            digit(a, dx, 24, n, GREEN, GREEN_DIM)
        col = GREEN if show and not blink_off else GREEN_DIM
        rect(a, 68, 27, 2, 2, col)
        rect(a, 68, 32, 2, 2, col)
        # visualizer
        self.draw_vis(a, 24, 42)
        # marquee
        title, fixed = self.current_title()
        self.draw_marquee(a, title, fixed, dt)
        # bitrate and channels
        kbps, khz, stereo = (t.kbps, t.khz, t.channels > 1) if t else (0, 0, False)
        if t and t.kbps:
            text(a, 112, 43, f"{kbps:>3}"[:3], GREEN)
            text(a, 158, 43, f"{khz:>2}"[:2], GREEN)
        tiny(a, 204, 44, "MONO", GREEN if t and not stereo and e.playing else GREEN_FAINT)
        tiny(a, 226, 44, "STEREO", GREEN if t and stereo and e.playing else GREEN_FAINT)
        # sliders
        self.draw_slider(a, 107, 56, 68, self.volume, VOLBAR[min(int(self.volume * 28), 27)], p == "vol")
        bal_col = VOLBAR[min(int(abs(self.balance) * 27), 27)]
        self.draw_slider(a, 177, 56, 38, (self.balance + 1) / 2, bal_col, p == "bal")
        self.draw_toggle(a, 219, 57, 23, 12, "EQ", self.show_eq, p == "toggle_eq")
        self.draw_toggle(a, 242, 57, 23, 12, "PL", self.show_pl, p == "toggle_pl")
        # position bar
        inset(a, 16, 72, 243, 10, C("#10101e"))
        rect(a, 18, 76, 239, 2, EDGE_DK)
        if t and e.playing and t.seconds:
            frac = self.drag_pos if self.drag == "pos" and self.drag_pos is not None else e.position / t.seconds
            frac = max(0, min(frac, 1))
            x = 17 + int(frac * (241 - 28))
            rect(a, 18, 76, max(x - 18, 0), 2, GREEN_MD)
            button(a, x, 73, 28, 8, p == "pos")
            rect(a, x + 13, 75, 2, 4, FACE_DK)
        # transport
        def face(name, x, y, w, h):
            return button(a, x, y, w, h, p == name)
        o = face("prev", 16, 88, 23, 18)
        rect(a, 22 + o, 93 + o, 2, 8, ICON)
        tri_left(a, 24 + o, 93 + o, 8, ICON)
        tri_left(a, 28 + o, 93 + o, 8, ICON)
        o = face("play", 39, 88, 23, 18)
        tri_right(a, 47 + o, 92 + o, 10, ICON)
        o = face("pause", 62, 88, 23, 18)
        rect(a, 69 + o, 93 + o, 3, 8, ICON)
        rect(a, 74 + o, 93 + o, 3, 8, ICON)
        o = face("stop", 85, 88, 23, 18)
        rect(a, 92 + o, 93 + o, 9, 8, ICON)
        o = face("next", 108, 88, 22, 18)
        tri_right(a, 113 + o, 93 + o, 8, ICON)
        tri_right(a, 117 + o, 93 + o, 8, ICON)
        rect(a, 122 + o, 93 + o, 2, 8, ICON)
        o = face("eject", 136, 89, 22, 16)
        for i in range(5):
            rect(a, 147 - i + o, 92 + i + o, 2 * i + 1 - 1 + 1, 1, ICON)
        rect(a, 142 + o, 98 + o, 11, 2, ICON)
        self.draw_toggle(a, 164, 89, 47, 15, "SHUFFLE", self.shuffle, p == "shuffle")
        self.draw_toggle(a, 211, 89, 28, 15, "REP", self.repeat, p == "repeat")
        # logo: a bolt
        for i, (x, w) in enumerate([(254, 4), (253, 4), (252, 4), (251, 7), (254, 3), (253, 3), (252, 2), (251, 2), (250, 2)]):
            rect(a, x, 92 + i, w, 1, GOLD_LT if i < 5 else GOLD)

    def draw_marquee(self, a, title, fixed, dt):
        x0, y0, w = 111, 24, 152
        if title != self.last_title:
            self.last_title = title
            self.marquee = 0
        row = np.full((8, w), BLACK, np.uint8)
        n = len(title) * 6
        if n <= w or fixed:
            text(row, 0, 0, title[:w // 6], GREEN)
        else:
            self.marquee += dt * 24
            gap = title + "  ***  "
            cycle = len(gap) * 6
            off = int(self.marquee) % cycle
            first = off // 6
            shown = (gap * 2)[first:first + w // 6 + 2]
            text(row, -(off % 6), 0, shown, GREEN)
        a[y0:y0 + 8, x0:x0 + w] = row

    def draw_slider(self, a, x, y, w, frac, col, down):
        inset(a, x, y + 2, w, 7, C("#10101e"))
        rect(a, x + 1, y + 3, w - 2, 5, col)
        tx = x + int(frac * (w - 14))
        button(a, tx, y, 14, 11, down)
        rect(a, tx + 6, y + 3, 2, 5, FACE_DK)

    def draw_toggle(self, a, x, y, w, h, label, lit, down):
        o = button(a, x, y, w, h, down)
        rect(a, x + 3 + o, y + 2 + o, w - 6, 2, LED_ON if lit else LED_OFF)
        tiny_center(a, x + o, y + h - 6 + o, w, label, ICON)

    def draw_vis(self, a, x, y):
        e = self.engine
        w, h = 76, 16
        rect(a, x, y, w, h, BLACK)
        if self.vis_mode == 2:
            return
        for yy in range(y + 1, y + h, 2):
            a[yy, x::2][:w // 2] = C("#141420")
        if self.vis_mode == 0:
            an = self.analyzer
            an.update(e.waveform(an.n))
            for i in range(len(an.bars)):
                bh = int(an.bars[i] + 0.5)
                bx = x + i * 4
                for r in range(bh):
                    level = 15 - r
                    a[y + level, bx:bx + 3] = VIS[level]
                pk = int(an.peaks[i] + 0.5)
                if pk > 0:
                    a[y + 16 - min(pk, 16), bx:bx + 3] = VIS_PEAK
        else:
            wave = e.waveform(w * 8)[::8]
            ys = np.clip((wave * 7.5 + 8).astype(int), 0, h - 1)
            prev = ys[0]
            for i, v in enumerate(ys):
                lo, hi = min(prev, v), max(prev, v)
                a[y + lo:y + hi + 1, x + i] = VIS[min(abs(int(v) - 8) * 2, 15)]
                prev = v

    def draw_eq(self, a):
        ox, oy = self.eq_pos
        sub = a[oy:oy + self.WH, ox:ox + self.TW]
        sub[:] = self.skin_eq
        p = self.pressed
        self.draw_toggle(sub, 14, 18, 26, 12, "ON", self.eq_on, p == "eq_on")
        o = button(sub, 41, 18, 32, 12, p == "eq_reset")
        tiny_center(sub, 41 + o, 22 + o, 32, "RESET", ICON)
        o = button(sub, 217, 18, 44, 12, p == "eq_presets")
        tiny_center(sub, 217 + o, 22 + o, 44, "PRESETS", ICON)
        # response curve
        xs = np.arange(109)
        pts = 2 + xs * 9 / 108
        vals = np.interp(pts, np.arange(2, 12), self.eq_bands)
        ys = (26 - vals / 12 * 7.5).astype(int)
        col = GREEN if self.eq_on else GREEN_FAINT
        for x, y in zip(xs, ys):
            sub[y, 88 + x] = col
        self.draw_vslider(sub, 21, self.preamp, p == "preamp")
        for i, v in enumerate(self.eq_bands):
            self.draw_vslider(sub, 78 + 18 * i, v, p == f"band{i}")

    def draw_vslider(self, a, x, value, down):
        y, h = 38, 63
        inset(a, x + 5, y, 4, h, C("#10101e"))
        t = int((12 - value) / 24 * (h - 11))
        col = VOLBAR[min(max(13 - int(value / 12 * 13), 0), 27)]
        mid = y + (h - 11) // 2 + 5
        cy = y + t + 5
        rect(a, x + 6, min(mid, cy), 2, abs(mid - cy) + 1, col)
        button(a, x + 1, y + t, 12, 11, down)
        rect(a, x + 3, y + t + 5, 8, 1, FACE_DK)

    def draw_playlist(self, a):
        ox, oy = self.pl_pos
        H = self.pl_h
        rh = self.row_h
        visible = max((H - 66) // rh, 1)
        self.pl_rows = visible
        n = len(self.tracks)
        self.top = max(0, min(self.top, max(n - visible, 0)))
        rows = self.tracks[self.top:self.top + visible]
        total = sum(t.seconds or 0 for t in self.tracks)
        key = (self.top, self.sel, self.cur, n, visible, H, self.pressed if str(self.pressed).startswith("pl_") else 0,
               tuple((t.title, t.artist, t.album, t.seconds) for t in rows), int(total), self.engine.playing,
               self.format, rh)
        if key != self.pl_cache_key:
            self.pl_cache_key = key
            b = shell(self.TW, H, "SIXAMP PLAYLIST")
            lh = H - 62
            inset(b, 12, 20, 243, lh)
            for i, t in enumerate(rows):
                idx = self.top + i
                y = 22 + i * rh + (rh - 9) // 2
                if idx == self.sel:
                    rect(b, 13, 21 + i * rh, 241, rh, SEL_BG)
                col = WHITE if idx == self.cur else RED if t.bad else GREEN
                dur = self.mmss(t.seconds) if t.seconds else ""
                room = (238 - len(dur) * 6 - 6) // 6
                text(b, 15, y, asciify(self.entry(idx, t))[:room], col)
                text(b, 252 - len(dur) * 6, y, dur, col)
            rect(b, 257, 20, 9, lh, BODY[6])
            inset(b, 258, 20, 7, lh, C("#10101e"))
            if n > visible:
                th = max(lh * visible // n, 8)
                ty = 20 + (lh - th) * self.top // max(n - visible, 1)
            else:
                th, ty = lh, 20
            button(b, 258, ty, 7, th)
            for name, x, label in PL_BUTTONS:
                o = button(b, x, H - 32, 24, 18, self.pressed == name)
                tiny_center(b, x + o, H - 26 + o, 24, label, ICON)
            inset(b, 142, H - 32, 123, 18)
            word = f"{n} TRACK{'S' * (n != 1)}  {self.hms(total)}"
            text(b, 146, H - 27, word[:19], GREEN)
            self.pl_cache = b
        a[oy:oy + H, ox:ox + self.TW] = self.pl_cache

    @staticmethod
    def hms(s):
        s = int(s)
        return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"

    def compose(self, dt):
        a = np.full((self.nh_full, self.nw_full), DESK, np.uint8)
        self.draw_main(a, dt)
        if self.show_eq:
            self.draw_eq(a)
        if self.show_pl:
            self.draw_playlist(a)
        if self.modal:
            view = a[:self.ph // self.scale, :self.pw // self.scale]  # keep it on screen when cropped
            self.modal.draw(view if min(view.shape[0] / 80, view.shape[1] / 200) >= 1 else a, self.row_h)
        return a

    # ---- output

    def write(self, data):
        view = memoryview(data)
        while view:
            try:
                n = os.write(self.out, view)
                view = view[n:]
            except BlockingIOError:
                time.sleep(0.005)

    def present(self, native):
        s = self.scale
        img = np.repeat(np.repeat(native, s, 0), s, 1) if s > 1 else native
        img = img[:self.ph, :self.pw]
        out = [b"\x1b[?2026h"]
        first = self.prev is None
        if not first and ((img == DESK) & (self.prev != DESK)).any():
            # Background is unpainted, so a closed dialog's pixels can only go by clearing the screen.
            out.append(b"\x1b[2J")
            self.status_key = None
            first = True
        if first:
            diff = np.ones(img.shape, bool)
        else:
            diff = img != self.prev
            if not diff.any():
                return 0
        cw, ch = self.cw, self.ch
        gh, gw = img.shape[0] // ch, img.shape[1] // cw
        tiles = diff.reshape(gh, ch, gw, cw).any(axis=(1, 3))
        row_any = tiles.any(axis=1)
        rows = np.nonzero(row_any)[0]
        groups = np.split(rows, np.nonzero(np.diff(rows) > 1)[0] + 1) if len(rows) else []
        gap = max(120 // cw, 2)
        step_r, step_c = max(480 // ch, 1), max(480 // cw, 1)
        for grp in groups:
            r0, r1 = grp[0], grp[-1] + 1
            cols = np.nonzero(tiles[r0:r1].any(axis=0))[0]
            spans = np.split(cols, np.nonzero(np.diff(cols) > gap)[0] + 1)
            for sp in spans:
                # Terminals cap image size (xterm: 1000x1000), so big areas go out as tiles.
                for rt in range(r0, r1, step_r):
                    for ct in range(sp[0], sp[-1] + 1, step_c):
                        c1 = min(ct + step_c, sp[-1] + 1)
                        y0, y1 = rt * ch, min(rt + step_r, r1) * ch
                        y1 = min(y0 + -(-(y1 - y0) // 6) * 6, img.shape[0])  # bands are 6 pixels tall
                        data = sixel(img[y0:y1, ct * cw:c1 * cw], PALETTE)
                        if data:
                            out.append(b"\x1b[%d;%dH" % (rt + 1, ct + self.ox + 1) + data)
        out.append(b"\x1b[?2026l")
        data = b"".join(out)
        self.write(data)
        self.prev = img.copy()
        return len(data)

    def draw_status(self):
        hint = " z<< x> c|| v[] b>>  arrows seek/list  +- vol  l open  j jump  e eq  p list  ? help  q quit"
        if self.engine.silent:
            hint = " NO AUDIO DEVICE - playing silently |" + hint
        if self.too_small:
            hint = " Terminal too small: enlarge it or lower the font size |" + hint
        if os.environ.get("TERM_PROGRAM") in MouseShare.NAMES and not self.share:
            hint = " pyterm's sixamp_support plugin is off, so the skin may break |" + hint
        elif self.cell_guessed:
            hint = " Cell size unknown, guessed 10x20. If the skin looks scrambled, use --cell WxH |" + hint
        hint = hint[:self.cols - 1]
        self.write(f"\x1b[{self.rows};1H\x1b[0m\x1b[2K\x1b[2m{hint}\x1b[0m".encode())

    # ---- events

    def native_xy(self, cx, cy):
        if self.share:
            px, py = self.share.read()
            px -= self.ox * self.cw
        elif self.term["mouse_px"]:
            px, py = cx - self.ox * self.cw, cy
        else:
            px, py = (cx - 1 - self.ox) * self.cw + self.cw // 2, (cy - 1) * self.ch + self.ch // 2
        return px // self.scale, py // self.scale

    def hit(self, x, y):
        for name, hx, hy, w, h in self.hits:
            if hx <= x < hx + w and hy <= y < hy + h:
                return name
        if self.exact_mouse:
            return None
        # A click only says which cell it was in, and a cell can be larger than a small button.
        # Take whatever covers most of that cell, rather than missing it.
        cw, ch = self.cw / self.scale, self.ch / self.scale
        x0, y0 = x - cw / 2, y - ch / 2
        best, area = None, 0
        for name, hx, hy, w, h in self.hits:
            a = max(min(x0 + cw, hx + w) - max(x0, hx), 0) * max(min(y0 + ch, hy + h) - max(y0, hy), 0)
            if a > area:
                best, area = name, a
        return best

    @property
    def row_h(self):
        """List row height in skin pixels. With cell-only mouse reports a row must be at least one
        cell tall, or some rows fall between cells and can never be clicked."""
        if self.exact_mouse:
            return 9
        return max(9, -(-self.ch // self.scale))

    def over(self, x, y, rx, ry, w, h):
        """Is the click on this rectangle? With cell-only mouse reports, anywhere in the cell counts."""
        pad_x, pad_y = (0, 0) if self.exact_mouse else (self.cw / self.scale / 2, self.ch / self.scale / 2)
        return rx - pad_x <= x < rx + w + pad_x and ry - pad_y <= y < ry + h + pad_y

    def on_mouse(self, b, cx, cy, down):
        x, y = self.native_xy(cx, cy)
        debug("mouse", b, (cx, cy), "down" if down else "up", "skin", (x, y), "hit", self.hit(x, y))
        code = b & 3
        wheel = bool(b & 64)
        motion = bool(b & 32)
        if self.modal:
            return self.on_modal_mouse(b, x, y, down, wheel, motion)
        if wheel:
            d = -1 if code == 0 else 1
            target = self.hit(x, y)
            if target in ("pl_list", "pl_bar"):
                self.top += d * 3
            elif target == "bal":
                self.step_balance(-d)
            elif target == "preamp":
                self.set_slider(target, max(-12.0, min(12.0, self.preamp - d)))
            elif (target or "").startswith("band"):
                band = self.eq_bands[int(target[4:])]
                self.set_slider(target, max(-12.0, min(12.0, band - d)))
            elif down:
                self.set_volume(self.volume - d * 0.04)
            return
        if motion:
            if self.drag:
                self.drag_to(self.drag, x, y)
            return
        if down and code == 0:
            name = self.hit(x, y)
            self.pressed = name
            if name in ("pos", "vol", "bal", "preamp", "pl_bar") or (name or "").startswith("band"):
                self.drag = name
                self.drag_to(name, x, y)
            elif name == "pl_list":
                self.list_click(x, y)
        elif not down:
            name = self.hit(x, y)
            if self.drag:
                if self.drag == "pos" and self.drag_pos is not None and 0 <= self.cur < len(self.tracks):
                    self.seek(self.drag_pos * (self.tracks[self.cur].seconds or 0))
                self.drag = None
                self.drag_pos = None
            elif name and name == self.pressed:
                self.click(name)
            self.pressed = None

    def on_modal_mouse(self, b, x, y, down, wheel, motion):
        m = self.modal
        if wheel:
            if down:
                m.top += -3 if (b & 3) == 0 else 3
                m.top = max(0, min(m.top, max(len(m.visible) - m.rows, 0)))
                m.sel = max(m.top, min(m.sel, m.top + m.rows - 1))
            return
        if motion or not down or (b & 3):
            return
        mx, my, w, h = m.rect
        if not (mx <= x < mx + w and my <= y < my + h) or self.over(x, y, mx + w - 12, my + 3, 9, 8):
            self.modal = None
            return
        if m.filterable:
            ly = my + 16 + 12 + 2 + 2
            row = (y - ly + 1) // m.rh
            if 0 <= row < m.rows and m.top + row < len(m.visible):
                idx = m.top + row
                now = time.monotonic()
                double = self.last_click == (idx, "m") and now - self.last_time < 0.45
                self.last_click, self.last_time = (idx, "m"), now
                m.sel = idx
                if double:
                    m.on_pick(m.visible[idx])

    last_time = 0.0

    def list_click(self, x, y):
        ox, oy = self.pl_pos
        row = (y - oy - 21) // self.row_h
        idx = self.top + row
        if 0 <= idx < len(self.tracks):
            now = time.monotonic()
            double = self.last_click == (idx, "p") and now - self.last_time < 0.45
            self.last_click, self.last_time = (idx, "p"), now
            self.sel = idx
            debug("list select", idx)
            if double:
                self.play(idx)

    def set_slider(self, name, v):
        if name == "preamp":
            self.preamp = v
        else:
            self.eq_bands[int(name[4:])] = v
            self.preset = "Custom"
        self.set_eq()
        self.message(f"EQ: {'PREAMP' if name == 'preamp' else EQ_FREQS[int(name[4:])]} {v:+.1f} DB", 1)

    def drag_to(self, name, x, y):
        pad_x, pad_y = (0, 0) if self.exact_mouse else (self.cw / self.scale / 2, self.ch / self.scale / 2)
        if name == "pos":
            self.drag_pos = max(0.0, min(1.0, (x - 31) / (241 - 28)))
        elif name == "vol":
            self.set_volume((x - 114) / (68 - 14))
        elif name == "bal":
            # With cell-only mouse reports the exact middle may be unreachable, so snap within a cell.
            near = abs(x - 196) <= max(pad_x, 1)
            self.set_balance(0.0 if near else (x - 184) / (38 - 14) * 2 - 1)
        elif name == "preamp" or name.startswith("band"):
            offset = y - self.eq_pos[1] - 38 - 31.5
            v = 0.0 if abs(offset) <= max(pad_y, 1.5) else (0.5 - offset / 52) * 24 - 12
            self.set_slider(name, round(max(-12, min(12, v)) * 2) / 2)
        elif name == "pl_bar":
            n, vis = len(self.tracks), self.pl_rows
            lh = self.pl_h - 62
            self.top = int((y - self.pl_pos[1] - 20) / max(lh, 1) * max(n - vis + 1, 1))

    def click(self, name):
        actions = {
            "prev": self.prev_track, "play": self.play_pressed, "pause": self.pause_pressed, "stop": self.stop_pressed,
            "next": self.next_track, "eject": self.open_browser, "shuffle": self.toggle_shuffle,
            "repeat": self.toggle_repeat, "toggle_eq": self.toggle_eq, "toggle_pl": self.toggle_pl,
            "time": self.toggle_time, "vis": self.cycle_vis, "quit": self.quit, "clutter_o": self.open_browser,
            "clutter_i": self.open_info, "clutter_d": self.toggle_scale, "clutter_v": self.cycle_vis,
            "eq_on": self.toggle_eq_on, "eq_reset": self.eq_reset, "eq_presets": self.cycle_preset,
            "eq_close": self.toggle_eq, "pl_close": self.toggle_pl, "pl_add": self.open_browser,
            "pl_rem": lambda: self.remove(self.sel), "pl_clr": self.clear_list, "pl_sort": self.sort_list,
            "pl_fmt": self.cycle_format,
        }
        if name in actions:
            actions[name]()

    def toggle_eq(self):
        self.show_eq = not self.show_eq
        self.need_layout = True

    def toggle_pl(self):
        self.show_pl = not self.show_pl
        self.need_layout = True

    def toggle_time(self):
        self.remaining = not self.remaining

    def cycle_vis(self):
        self.vis_mode = (self.vis_mode + 1) % 3
        self.message("VISUALIZER: " + ("SPECTRUM", "OSCILLOSCOPE", "OFF")[self.vis_mode], 1)

    def toggle_eq_on(self):
        self.eq_on = not self.eq_on
        self.set_eq()
        self.message(f"EQUALIZER: {'ON' if self.eq_on else 'OFF'}", 1)

    def eq_reset(self):
        self.eq_bands = [0.0] * 10
        self.preamp = 0.0
        self.preset = "Flat"
        self.set_eq()

    def clear_list(self):
        self.engine.stop()
        self.tracks = []
        self.cur = -1
        self.sel = self.top = 0
        self.history = []
        self.rebuild_order()

    def sort_list(self):
        current = self.tracks[self.cur] if self.cur >= 0 else None
        self.tracks.sort(key=lambda t: t.label.lower())
        self.cur = self.tracks.index(current) if current else -1
        self.history = []
        self.rebuild_order()

    def quit(self):
        self.running = False

    def ensure_visible(self):
        vis = getattr(self, "pl_rows", 8)
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + vis:
            self.top = self.sel - vis + 1

    def on_key(self, k):
        if k == "\x03":
            return self.quit()
        if self.modal:
            return self.on_modal_key(k)
        move = {"up": -1, "down": 1, "pageup": -getattr(self, "pl_rows", 8), "pagedown": getattr(self, "pl_rows", 8)}
        if k in move:
            if self.tracks:
                self.sel = max(0, min(len(self.tracks) - 1, self.sel + move[k]))
                self.ensure_visible()
            return
        table = {
            "z": self.prev_track, "x": self.play_pressed, "c": self.pause_pressed, "space": self.pause_pressed,
            "v": self.stop_pressed, "b": self.next_track, "l": self.open_browser, "j": self.open_jump,
            "i": self.open_info, "?": self.open_help, "h": self.open_help, "q": self.quit, "r": self.toggle_repeat,
            "s": self.toggle_shuffle, "e": self.toggle_eq, "p": self.toggle_pl, "o": self.toggle_eq_on,
            "n": self.cycle_preset, "f": self.cycle_format, "d": self.toggle_scale, "t": self.toggle_time, "w": self.cycle_vis,
            "+": lambda: self.set_volume(self.volume + 0.04), "=": lambda: self.set_volume(self.volume + 0.04),
            "-": lambda: self.set_volume(self.volume - 0.04), "[": lambda: self.step_balance(-1),
            "]": lambda: self.step_balance(1), "enter": lambda: self.play(self.sel),
            "delete": lambda: self.remove(self.sel), "home": lambda: self.jump_sel(0),
            "end": lambda: self.jump_sel(len(self.tracks) - 1),
            "left": lambda: self.seek(self.engine.position - 5), "right": lambda: self.seek(self.engine.position + 5),
        }
        if k in table:
            table[k]()

    def jump_sel(self, i):
        if self.tracks:
            self.sel = i
            self.ensure_visible()

    def on_modal_key(self, k):
        m = self.modal
        if k == "esc" or (not m.filterable and k in ("q", "enter", "?")):
            self.modal = None
        elif k in ("up", "down", "pageup", "pagedown", "home", "end"):
            if m.filterable:
                d = {"up": -1, "down": 1, "pageup": -m.rows, "pagedown": m.rows, "home": -10 ** 6, "end": 10 ** 6}[k]
                m.move(d)
            else:
                d = {"up": -1, "down": 1, "pageup": -m.rows, "pagedown": m.rows, "home": -10 ** 6, "end": 10 ** 6}[k]
                m.top = max(0, min(m.top + d, max(len(m.visible) - m.rows, 0)))
        elif not m.filterable:
            return
        elif k == "enter":
            if m.visible:
                m.on_pick(m.visible[m.sel])
        elif k == "backspace":
            if m.query:
                m.query = m.query[:-1]
                m.sel = 0
            elif m.parent:
                self.open_browser(m.parent)
        elif (k == "space" or k == "insert") and m.on_add:
            if m.visible:
                m.on_add(m.visible[m.sel])
        elif k == "\x01" and m.on_add_all:
            m.on_add_all(None)
        elif k == "space":
            m.query += " "
            m.sel = 0
        elif len(k) == 1 and k >= " ":
            m.query += k
            m.sel = 0

    # ---- main loop

    def run(self):
        last = time.monotonic()
        seen = laid_out = None
        settle_until = 0.0
        self.status_key = None
        inp = Input(self.console)
        while self.running:
            now = time.monotonic()
            dt, last = now - last, now
            # Size or font changes (a window resize, a pyterm theme) arrive in steps and through a
            # delayed pipe, so hold the display until they have settled, then repaint once.
            grid = (self.console.size()[:2], self.share.cell() if self.share else None)
            if seen is not None and grid != seen:
                settle_until = now + SETTLE
            seen = grid
            settling = now < settle_until
            if not settling and (self.need_layout or grid != laid_out):
                laid_out = grid
                self.write(b"\x1b[2J")
                self.layout()
                self.nh_full = max(self.nh, self.ph // self.scale + 1)
                self.nw_full = max(self.nw, self.pw // self.scale + 1)
                self.status_key = None
            if self.engine.ended:
                self.engine.ended = False
                if self.engine.error:
                    if 0 <= self.cur < len(self.tracks):
                        self.tracks[self.cur].bad = True
                self.next_track(auto=True)
            elif self.engine.error and not self.engine.playing and self.cur >= 0:
                self.tracks[self.cur].bad = True
                self.message("CANNOT PLAY THIS FILE", 2)
                self.engine.error = None
                self.pl_cache_key = None
                if self.cur + 1 < len(self.tracks) or self.repeat:
                    self.next_track(auto=True)
            if self.drag and self.share:  # follow the pointer inside a cell too, for smooth sliders
                self.drag_to(self.drag, *self.native_xy(0, 0))
            if not settling:
                self.present(self.compose(dt))
                key = (self.cols, self.rows, self.engine.silent, self.too_small)
                if key != self.status_key:
                    self.status_key = key
                    self.draw_status()
            self.frame_no += 1
            if self.args.frames and self.frame_no >= self.args.frames:
                break
            budget = (1 / 30 if self.engine.playing else 1 / 15) - (time.monotonic() - now)
            for ev in inp.read(max(budget, 0.001)):
                if ev[0] == "key":
                    self.on_key(ev[1])
                else:
                    self.on_mouse(*ev[1:])
        self.save_state()


def main():
    ap = argparse.ArgumentParser(prog="sixamp", description="A 1998-style MP3 player drawn with Sixel graphics.")
    ap.add_argument("--version", action="version", version=f"sixamp {__version__}")
    ap.add_argument("paths", nargs="*", help="audio files, folders or .m3u playlists")
    ap.add_argument("--scale", type=int, choices=range(1, 9), metavar="N", help="pixel scale (default: fit, up to 3)")
    ap.add_argument("--format", metavar="TEXT", help="playlist line, e.g. '%%n. %%a - %%t'. %%n number, %%a artist, "
                    "%%t title, %%l album, %%f file name, %%d folder, %%%% a percent sign")
    ap.add_argument("--force", action="store_true", help="skip the Sixel check")
    ap.add_argument("--cell", metavar="WxH", help="terminal cell size in pixels, if it cannot report one (e.g. 10x20)")
    ap.add_argument("--no-audio", action="store_true", help="do not open an audio device")
    ap.add_argument("--probe", action="store_true", help="show what the terminal reports, and a test image, then exit")
    ap.add_argument("--debug", action="store_true", help="write sixamp-debug.log next to this script")
    ap.add_argument("--frames", type=int, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.cell and not re.fullmatch(r"[1-9]\d*[xX][1-9]\d*", args.cell):
        ap.error("--cell needs a size like 10x20")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.exit("sixamp: run me in a terminal.")
    console = WindowsConsole() if WINDOWS else PosixConsole()
    saved_err = os.dup(2)
    app = None
    try:
        term = probe_terminal(console)
        global debug_on
        debug_on = args.debug or args.probe
        debug("platform", sys.platform, "python", sys.version.split()[0], "term_program",
              os.environ.get("TERM_PROGRAM"))
        debug("size", console.size(), "probe", term)
        if args.probe:
            rows, cols, xpx, ypx = console.size()
            bar = np.repeat(np.repeat(np.array([[RED, GREEN, GOLD_LT, WHITE]], np.uint8), 40, 1), 60, 0)
            sys.stdout.buffer.write(b"\x1b[2J\x1b[H" + sixel(bar, PALETTE) + b"\r\n")
            print(f"size {cols}x{rows} cells, {xpx}x{ypx} pixels\r\n{term}\r\n"
                  "You should see red, green, gold and white stripes above.\r", flush=True)
            return
        if args.cell:
            term["cell"] = tuple(int(v) for v in args.cell.lower().split("x"))
        if not term["sixel"] and not args.force:
            sys.exit("sixamp: this terminal does not report Sixel support.\n"
                     "Try Windows Terminal 1.22+, foot, WezTerm, Konsole, mlterm, iTerm2 or `xterm -ti vt340`,\n"
                     "or pass --force if you know better.")
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        log = os.open(LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        os.dup2(log, 2)
        os.close(log)
        sys.stdout.buffer.write(b"\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[2J\x1b[?1002h\x1b[?1006h")
        if term["mouse_px"]:
            sys.stdout.buffer.write(b"\x1b[?1016h")
        sys.stdout.buffer.flush()
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))
        app = App(args, term, console)
        app.run()
    finally:
        try:
            if not args.probe:
                sys.stdout.buffer.write(b"\x1b[?1016l\x1b[?1006l\x1b[?1002l\x1b[?7h\x1b[?25h\x1b[?1049l")
            sys.stdout.buffer.flush()
        except OSError:
            pass
        console.restore()
        if app:
            app.engine.close()
        os.dup2(saved_err, 2)


if __name__ == "__main__":
    main()
