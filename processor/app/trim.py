"""Find where the actual music starts and ends.

Pure numpy so it can run (and be tested) anywhere. Works on mono float32 samples.

Detection passes, in order, each one only ever moves the start later / the end earlier:

1. **Silence** at either edge (below ``silence_db`` dBFS).
2. **SponsorBlock ``music_offtopic``** segments touching an edge - crowd-sourced markers for
   the talking / skits / ads that music videos put before and after the song.
3. **Quiet ambience** - a sustained edge region far quieter than the body of the track
   (room tone, whispered dialogue, fade-in noise).
4. **Repetitive loops** - an edge region made of the same short pattern repeated many times
   (a beat looping under the video intro or end credits). The region must be long
   (``loop_min_seconds``), contain several repeats and end in a real change; if the "loop" would
   cover most of the track it is treated as the song itself and left alone.
5. Silence again, inside the new bounds.

A final sanity check reverts content cuts that would leave too little of the track.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

BLOCK = 0.1  # seconds per analysis block
N_BANDS = 40


@dataclass
class TrimOptions:
    mode: str = "smart"  # smart | silence | off
    silence_db: float = -50.0
    quiet_db: float = 24.0  # region this many dB below the track median counts as ambience
    quiet_min_seconds: float = 2.0
    loop_min_seconds: float = 20.0
    loop_min_repeats: float = 3.0
    loop_similarity: float = 0.9
    loop_max_period: float = 16.0
    loop_keep_periods: float = 0.0  # keep N repetitions of the loop as a lead-in / tail
    max_cut_fraction: float = 0.45  # never let content cuts remove more than this share of the file
    min_keep_seconds: float = 30.0


@dataclass
class TrimResult:
    start: float
    end: float
    duration: float
    fade_in: float = 0.02
    fade_out: float = 0.05
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.duration, 2),
            "kept_seconds": round(self.end - self.start, 2),
            "fade_in": self.fade_in,
            "fade_out": self.fade_out,
            "reasons": self.reasons,
        }


def _fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"


# --------------------------------------------------------------------------------------------
# Feature extraction
# --------------------------------------------------------------------------------------------

def block_features(samples: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (rms_db[n_blocks], log-band spectra[n_blocks, N_BANDS]) at BLOCK resolution."""
    hop = int(round(sr * BLOCK))
    n_fft = 1 << int(np.ceil(np.log2(hop)))
    n_blocks = max(1, int(np.ceil(len(samples) / hop)))
    padded = np.zeros(n_blocks * hop + n_fft, dtype=np.float32)
    padded[: len(samples)] = samples

    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_blocks)[:, None]
    frames = padded[idx]

    rms = np.sqrt(np.mean(padded[: n_blocks * hop].reshape(n_blocks, hop) ** 2, axis=1) + 1e-12)
    rms_db = 20 * np.log10(rms + 1e-12)

    spec = np.abs(np.fft.rfft(frames * np.hanning(n_fft)[None, :], axis=1)) ** 2
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    edges = np.geomspace(60, min(11000, sr / 2 - 1), N_BANDS + 1)
    band_idx = np.clip(np.digitize(freqs, edges) - 1, -1, N_BANDS)
    bands = np.zeros((n_blocks, N_BANDS), dtype=np.float64)
    for b in range(N_BANDS):
        mask = band_idx == b
        if mask.any():
            bands[:, b] = spec[:, mask].sum(axis=1)
    return rms_db, np.log(bands + 1e-9)


def _smooth(x: np.ndarray, width: int) -> np.ndarray:
    """Centered moving average that stays unbiased at the edges (no zero padding)."""
    if width <= 1 or len(x) == 0:
        return x
    kernel = np.ones(width)
    counts = np.convolve(np.ones(len(x)), kernel, mode="same")
    if x.ndim == 1:
        return np.convolve(x, kernel, mode="same") / counts
    return np.stack([np.convolve(x[:, i], kernel, mode="same") / counts for i in range(x.shape[1])], axis=1)


# --------------------------------------------------------------------------------------------
# Individual detectors (all work in block indices)
# --------------------------------------------------------------------------------------------

def silence_bounds(rms_db: np.ndarray, lo: int, hi: int, threshold_db: float) -> tuple[int, int]:
    active = np.nonzero(rms_db[lo:hi] > threshold_db)[0]
    if active.size == 0:
        return lo, hi
    return lo + int(active[0]), lo + int(active[-1]) + 1


def quiet_edge(rms_db: np.ndarray, lo: int, hi: int, opts: TrimOptions, from_end: bool) -> int:
    """Return the new boundary (block index) after skipping a sustained quiet edge region."""
    body = rms_db[lo:hi]
    loud = body[body > opts.silence_db]
    if loud.size < 50:
        return hi if from_end else lo
    floor = np.median(loud) - opts.quiet_db
    smoothed = _smooth(body, int(1.0 / BLOCK))
    seq = smoothed[::-1] if from_end else smoothed
    above = np.nonzero(seq >= floor)[0]
    if above.size == 0:
        return hi if from_end else lo
    run = int(above[0])
    if run * BLOCK < opts.quiet_min_seconds:
        return hi if from_end else lo
    return hi - run if from_end else lo + run


def loop_edge(feats: np.ndarray, lo: int, hi: int, opts: TrimOptions, from_end: bool) -> tuple[int, float] | None:
    """Detect a repetitive loop hugging one edge. Returns (new_boundary_block, period_seconds)."""
    seg = feats[lo:hi]
    if from_end:
        seg = seg[::-1]
    n = len(seg)
    min_len = int(opts.loop_min_seconds / BLOCK)
    if n < 2 * min_len:
        return None

    # z-score per band over the whole segment so the shared timbre of the track cancels out,
    # then smooth a little to tolerate sub-block misalignment between repetitions.
    z = (seg - seg.mean(axis=0)) / (seg.std(axis=0) + 1e-6)
    z = _smooth(z, 3)
    norms = np.linalg.norm(z, axis=1) + 1e-9

    search = min(n, int(n * opts.max_cut_fraction) + int(opts.loop_max_period / BLOCK) * 2)
    best: tuple[int, int] | None = None  # (loop_len_blocks, period_blocks)
    for period in range(int(1.0 / BLOCK), int(opts.loop_max_period / BLOCK) + 1):
        m = search - period
        if m <= min_len:
            break
        sims = np.einsum("ij,ij->i", z[:m], z[period : period + m]) / (norms[:m] * norms[period : period + m])
        sims = _smooth(sims, int(1.0 / BLOCK))
        # Tolerate up to 1s of mismatch right at the edge (partial block, fade, click).
        above = np.nonzero(sims >= opts.loop_similarity)[0]
        if above.size == 0 or above[0] > int(1.0 / BLOCK):
            continue
        below = np.nonzero(sims[above[0]:] < opts.loop_similarity)[0]
        run = int(above[0]) + (int(below[0]) if below.size else m - int(above[0]))
        loop_len = run + period
        if loop_len < min_len or loop_len / period < opts.loop_min_repeats:
            continue
        if best is None or loop_len > best[0] + int(0.5 / BLOCK):
            best = (loop_len, period)

    if best is None:
        return None
    loop_len, period = best
    if loop_len >= n * opts.max_cut_fraction:
        return None  # the "loop" is the song itself (e.g. a looped beat track)
    cut = loop_len - int(round(opts.loop_keep_periods * period))
    if cut * BLOCK < opts.loop_min_seconds / 2:
        return None
    return (hi - cut if from_end else lo + cut), period * BLOCK


def sponsorblock_edges(segments: list[dict], start: float, end: float, slack: float = 3.0) -> tuple[float, float, list[str]]:
    """Apply SponsorBlock segments that touch the current start/end (chains of segments too)."""
    reasons: list[str] = []
    segs = sorted(
        ((float(s["segment"][0]), float(s["segment"][1]), s.get("category", "?")) for s in segments or []),
        key=lambda s: s[0],
    )
    changed = True
    while changed:
        changed = False
        for s0, s1, cat in segs:
            if s0 <= start + slack and s1 > start and s1 < end:
                reasons.append(f"start: skipped {_fmt(start)}-{_fmt(s1)} {cat} (SponsorBlock)")
                start, changed = s1, True
            elif s1 >= end - slack and s0 < end and s0 > start:
                reasons.append(f"end: skipped {_fmt(s0)}-{_fmt(end)} {cat} (SponsorBlock)")
                end, changed = s0, True
    return start, end, reasons


# --------------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------------

def suggest_cuts(
    samples: np.ndarray,
    sr: int,
    opts: TrimOptions | None = None,
    sponsor_segments: list[dict] | None = None,
    content_type: str = "music",
) -> TrimResult:
    opts = opts or TrimOptions()
    duration = len(samples) / sr
    result = TrimResult(start=0.0, end=duration, duration=duration)
    if opts.mode == "off" or duration < 1:
        return result

    rms_db, feats = block_features(samples, sr)
    n = len(rms_db)

    lo, hi = silence_bounds(rms_db, 0, n, opts.silence_db)
    if lo > 0:
        result.reasons.append(f"start: removed {lo * BLOCK:.1f}s of silence")
    if hi < n:
        result.reasons.append(f"end: removed {(n - hi) * BLOCK:.1f}s of silence")
    result.start, result.end = lo * BLOCK, min(duration, hi * BLOCK)

    if opts.mode != "smart" or content_type == "podcast":
        return result

    silence_start, silence_end = result.start, result.end
    content_reasons: list[str] = []

    s, e, sb_reasons = sponsorblock_edges(sponsor_segments or [], result.start, result.end)
    content_reasons += sb_reasons
    lo, hi = int(s / BLOCK), int(np.ceil(e / BLOCK))

    new_lo = quiet_edge(rms_db, lo, hi, opts, from_end=False)
    if new_lo > lo:
        content_reasons.append(f"start: skipped {_fmt(lo * BLOCK)}-{_fmt(new_lo * BLOCK)} quiet ambience/talk")
        lo = new_lo
    new_hi = quiet_edge(rms_db, lo, hi, opts, from_end=True)
    if new_hi < hi:
        content_reasons.append(f"end: skipped {_fmt(new_hi * BLOCK)}-{_fmt(hi * BLOCK)} quiet ambience/talk")
        hi = new_hi

    intro = loop_edge(feats, lo, hi, opts, from_end=False)
    if intro:
        content_reasons.append(f"start: skipped {_fmt(lo * BLOCK)}-{_fmt(intro[0] * BLOCK)} repetitive intro loop (~{intro[1]:.1f}s pattern)")
        lo = intro[0]
    outro = loop_edge(feats, lo, hi, opts, from_end=True)
    if outro:
        content_reasons.append(f"end: skipped {_fmt(outro[0] * BLOCK)}-{_fmt(hi * BLOCK)} repetitive outro loop (~{outro[1]:.1f}s pattern)")
        hi = outro[0]

    lo, hi = silence_bounds(rms_db, lo, hi, opts.silence_db)
    start, end = lo * BLOCK, min(duration, hi * BLOCK)

    kept = end - start
    total = silence_end - silence_start
    if content_reasons and kept < max(opts.min_keep_seconds, total * 0.35):
        result.reasons.append(
            f"kept full track: content cuts would leave only {kept:.0f}s of {total:.0f}s (review manually)"
        )
        return result

    if start > silence_start + 0.5:
        result.fade_in = 0.3
    if end < silence_end - 0.5:
        result.fade_out = 2.0
    result.start, result.end = start, end
    result.reasons += content_reasons
    return result


def apply_override(result: TrimResult, start: float | None, end: float | None) -> TrimResult:
    if start is not None:
        result.start = max(0.0, min(start, result.duration))
        result.reasons.append(f"start: set manually to {_fmt(result.start)}")
        result.fade_in = 0.05
    if end is not None:
        result.end = max(result.start + 1, min(end, result.duration))
        result.reasons.append(f"end: set manually to {_fmt(result.end)}")
        result.fade_out = 1.0
    return result
