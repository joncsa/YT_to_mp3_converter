import numpy as np

from app.trim import TrimOptions, apply_override, sponsorblock_edges, suggest_cuts

SR = 22050


def _tone_sequence(rng, seconds, note_len=0.25, amp=0.3):
    """Non-repeating 'music': random chords + noise hits, one event every note_len seconds."""
    out = []
    n = int(note_len * SR)
    t = np.arange(n) / SR
    for _ in range(int(seconds / note_len)):
        freqs = rng.choice([110, 147, 165, 196, 220, 262, 294, 330, 392, 440, 523, 587, 659, 784], 3)
        chord = sum(np.sin(2 * np.pi * f * t) for f in freqs) / 3
        hit = rng.standard_normal(n) * np.exp(-t * 30) * rng.uniform(0, 1)
        out.append((chord * 0.8 + hit * 0.5) * amp)
    return np.concatenate(out).astype(np.float32)


def _song(rng, intro_loop=0.0, outro_loop=0.0, body=150.0, lead_silence=2.0, tail_silence=3.0):
    parts = [np.zeros(int(lead_silence * SR), np.float32)]
    if intro_loop:
        pattern = _tone_sequence(rng, 4.0)
        parts.append(np.tile(pattern, int(intro_loop / 4.0)))
    parts.append(_tone_sequence(rng, body))
    if outro_loop:
        pattern = _tone_sequence(rng, 3.0)
        parts.append(np.tile(pattern, int(outro_loop / 3.0)))
    parts.append(np.zeros(int(tail_silence * SR), np.float32))
    return np.concatenate(parts)


def test_silence_only_track_edges():
    rng = np.random.default_rng(1)
    audio = _song(rng)
    r = suggest_cuts(audio, SR)
    assert abs(r.start - 2.0) < 0.2
    assert abs(r.end - 152.0) < 0.3
    assert not any("loop" in x for x in r.reasons)


def test_intro_and_outro_loops_are_removed():
    rng = np.random.default_rng(2)
    audio = _song(rng, intro_loop=32, outro_loop=30)
    r = suggest_cuts(audio, SR)
    # music body spans 34s .. 184s
    assert abs(r.start - 34.0) < 1.5, r.reasons
    assert abs(r.end - 184.0) < 1.5, r.reasons
    assert any("intro loop" in x for x in r.reasons)
    assert any("outro loop" in x for x in r.reasons)
    assert r.fade_out == 2.0


def test_short_intro_riff_is_kept():
    rng = np.random.default_rng(3)
    audio = _song(rng, intro_loop=12)  # 3 bars of riff: a normal song intro, not filler
    r = suggest_cuts(audio, SR)
    assert r.start < 2.5, r.reasons


def test_track_that_is_one_long_loop_is_not_gutted():
    rng = np.random.default_rng(4)
    pattern = _tone_sequence(rng, 4.0)
    audio = np.tile(pattern, 45)  # 3 minute loop-based beat
    r = suggest_cuts(audio, SR)
    assert r.end - r.start > 170, r.reasons


def test_quiet_ambient_intro_is_removed():
    rng = np.random.default_rng(5)
    ambience = (rng.standard_normal(8 * SR) * 0.003).astype(np.float32)  # ~ -50..-45 dBFS room tone
    audio = np.concatenate([ambience, _tone_sequence(rng, 120)])
    r = suggest_cuts(audio, SR, TrimOptions(silence_db=-60))
    assert abs(r.start - 8.0) < 1.0, r.reasons


def test_podcast_only_trims_silence():
    rng = np.random.default_rng(6)
    audio = _song(rng, intro_loop=32)
    r = suggest_cuts(audio, SR, content_type="podcast")
    assert abs(r.start - 2.0) < 0.2


def test_mode_off():
    rng = np.random.default_rng(7)
    audio = _song(rng, intro_loop=32)
    r = suggest_cuts(audio, SR, TrimOptions(mode="off"))
    assert r.start == 0 and r.end == len(audio) / SR


def test_sponsorblock_edges_chain():
    segs = [
        {"segment": [0.0, 12.5], "category": "music_offtopic"},
        {"segment": [12.0, 20.0], "category": "music_offtopic"},
        {"segment": [100.0, 110.0], "category": "music_offtopic"},  # mid-song: untouched
        {"segment": [200.0, 240.0], "category": "music_offtopic"},
    ]
    s, e, reasons = sponsorblock_edges(segs, 0.0, 240.0)
    assert (s, e) == (20.0, 200.0)
    assert len(reasons) == 3


def test_manual_override():
    rng = np.random.default_rng(8)
    r = suggest_cuts(_song(rng), SR)
    r = apply_override(r, 10.0, 100.0)
    assert (r.start, r.end) == (10.0, 100.0)
