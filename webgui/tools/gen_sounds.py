"""Generate the three short alert WAVs into webgui/static/sounds/.

Run once from webgui/:  ..\.venv\Scripts\python tools/gen_sounds.py
Pure stdlib (wave + math) so the assets can be regenerated deterministically.
"""
import math
import pathlib
import struct
import wave

OUT = pathlib.Path(__file__).resolve().parents[1] / "static" / "sounds"
RATE = 44100

# name -> list of (freq_hz, seconds) segments (a tiny motif each)
SOUNDS = {
    "chime": [(880, 0.12), (1320, 0.18)],
    "bell":  [(1568, 0.06), (1175, 0.30)],
    "ping":  [(2093, 0.10)],
}


def _samples(segments):
    for freq, dur in segments:
        n = int(RATE * dur)
        for i in range(n):
            env = math.sin(math.pi * i / n)            # smooth in/out envelope
            yield 0.5 * env * math.sin(2 * math.pi * freq * i / RATE)


def write_wav(path, segments):
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        frames = b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767))
                          for s in _samples(segments))
        w.writeframes(frames)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, segs in SOUNDS.items():
        write_wav(OUT / f"{name}.wav", segs)
        print("wrote", OUT / f"{name}.wav")
