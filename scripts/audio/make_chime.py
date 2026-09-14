"""Write the waiting-room board's call chime, ``src/static/audio/chime.wav`` (Issue 60).

The chime is made here from arithmetic rather than taken from a sound library, so it has no licence to
track and anyone can see exactly what it is: two bell-like notes falling a major third (E5 then C5), each
a sine with two soft overtones and an exponential decay, one and a quarter seconds in all. It is distinct
from a phone's ring or a notification sound, gentle enough to hear forty times a morning, and it runs
before every spoken call so the room looks up before the number is said.

The output is deterministic: running this again writes the same bytes, which
``tests/unit/display/test_announcements.py`` checks against the committed file.

Usage::

    python -m scripts.audio.make_chime            # writes src/static/audio/chime.wav
    python -m scripts.audio.make_chime --check    # exits 1 if the committed file differs
"""

from __future__ import annotations

import argparse
import io
import math
import struct
import sys
import wave
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
CHIME_FILE: Final = REPO_ROOT / "src" / "static" / "audio" / "chime.wav"

SAMPLE_RATE: Final = 22_050
#: (frequency in Hz, when it starts in seconds, how long it rings in seconds)
NOTES: Final = ((659.25, 0.0, 0.7), (523.25, 0.45, 0.8))
#: Overtones as (multiple of the note, loudness relative to it).
OVERTONES: Final = ((1.0, 1.0), (2.0, 0.28), (3.0, 0.08))
#: Seconds for a note to fall to about a third of its loudness.
DECAY: Final = 0.22
#: Seconds of fade-in, so a note does not click.
ATTACK: Final = 0.006
PEAK: Final = 0.7


def samples() -> list[float]:
    """The chime, as samples between -1 and 1."""
    length = max(start + ring for _, start, ring in NOTES)
    out = [0.0] * math.ceil(length * SAMPLE_RATE)
    for frequency, start, ring in NOTES:
        first = round(start * SAMPLE_RATE)
        for i in range(round(ring * SAMPLE_RATE)):
            t = i / SAMPLE_RATE
            envelope = min(1.0, t / ATTACK) * math.exp(-t / DECAY)
            tone = sum(
                weight * math.sin(2 * math.pi * frequency * multiple * t)
                for multiple, weight in OVERTONES
            )
            out[first + i] += envelope * tone
    loudest = max(abs(value) for value in out)
    return [value / loudest * PEAK for value in out]


def wav_bytes() -> bytes:
    """The chime as a 16-bit mono WAV file."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(
            b"".join(struct.pack("<h", round(value * 32767)) for value in samples())
        )
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    """Write the chime, or with ``--check`` say whether the committed one is current."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    expected = wav_bytes()
    if args.check:
        current = CHIME_FILE.read_bytes() if CHIME_FILE.exists() else b""
        if current != expected:
            print(
                f"{CHIME_FILE} is out of date: run python -m scripts.audio.make_chime"
            )
            return 1
        print(f"{CHIME_FILE} is current")
        return 0
    CHIME_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHIME_FILE.write_bytes(expected)
    print(f"wrote {CHIME_FILE} ({len(expected)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
