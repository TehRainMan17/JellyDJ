"""
Audio waveform analysis service.

Analyzes every LibraryTrack that has not yet been processed
(audio_analyzed_at IS NULL) or whose audio_analysis_version is older than
CURRENT_VERSION.

Audio is streamed directly from Jellyfin via:
  GET /Audio/{itemId}/stream?static=true&api_key={key}

static=true bypasses Jellyfin transcoding so librosa receives the source file
(MP3, FLAC, etc.) exactly as it is stored on disk.  No local filesystem path
mapping is required.

Features extracted per track:
  bpm             — tempo in beats-per-minute (integer, median of frame estimates)
  musical_key     — tonal center and mode e.g. "C Major", "F# Minor"
  key_confidence  — Krumhansl-Schmuckler correlation score 0-1
  energy          — RMS loudness normalized 0-1
  loudness_db     — integrated loudness in dBFS
  beat_strength   — normalized mean onset-envelope (clarity of the rhythmic pulse) 0-1
  time_signature  — estimated beats per bar (3 or 4; default 4 when uncertain)
  acousticness    — heuristic estimate 0-1 (1 = purely acoustic, 0 = fully electronic)
"""

import io
import logging
import numpy as np
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import or_

log = logging.getLogger(__name__)

CURRENT_VERSION = 1
_SAMPLE_RATE = 22050       # Hz — sufficient for all audio features
_BATCH_COMMIT = 10         # commit to DB every N tracks to limit lock time

# Krumhansl-Schmuckler key profiles (major and minor)
_KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                       2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                       2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F",
               "F#", "G", "G#", "A", "A#", "B"]


# ── Key detection ─────────────────────────────────────────────────────────────

def _detect_key(chroma_mean: np.ndarray) -> tuple[str, float]:
    """
    Krumhansl-Schmuckler algorithm.  Returns ("C Major", 0.87) style tuple.
    chroma_mean must be shape (12,) with pitch classes C=0 … B=11.
    """
    best_r = -2.0
    best_key = "C Major"

    for i in range(12):
        rotated = np.roll(chroma_mean, -i)
        r_major = float(np.corrcoef(rotated, _KS_MAJOR)[0, 1])
        r_minor = float(np.corrcoef(rotated, _KS_MINOR)[0, 1])
        if r_major > best_r:
            best_r = r_major
            best_key = f"{_NOTE_NAMES[i]} Major"
        if r_minor > best_r:
            best_r = r_minor
            best_key = f"{_NOTE_NAMES[i]} Minor"

    # Normalize correlation to 0-1 confidence
    confidence = float(np.clip((best_r + 1) / 2, 0, 1))
    return best_key, confidence


# ── Time signature ────────────────────────────────────────────────────────────

def _estimate_time_signature(onset_env: np.ndarray, sr: int, hop: int = 512) -> int:
    """
    Heuristic: compute the auto-correlation of the onset envelope and compare
    energy at lags corresponding to 3- and 4-beat groupings.  Returns 3 or 4.
    """
    try:
        import librosa
        tempo_bpm, _ = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=hop)
        if hasattr(tempo_bpm, "__len__"):
            tempo_bpm = float(tempo_bpm[0])
        else:
            tempo_bpm = float(tempo_bpm)
        if tempo_bpm <= 0:
            return 4

        beat_frames = (60.0 / tempo_bpm) * sr / hop
        ac = np.correlate(onset_env, onset_env, mode="full")
        ac = ac[len(ac) // 2:]

        def _ac_at(beats):
            lag = int(round(beat_frames * beats))
            return float(ac[lag]) if lag < len(ac) else 0.0

        score_4 = _ac_at(4) + _ac_at(8)
        score_3 = _ac_at(3) + _ac_at(6)
        return 3 if score_3 > score_4 * 1.1 else 4
    except Exception:
        return 4


# ── Acousticness ──────────────────────────────────────────────────────────────

def _estimate_acousticness(y: np.ndarray, sr: int) -> float:
    """
    Heuristic acousticness estimate based on:
      - Zero-crossing rate (electronic music has lower ZCR per note event)
      - Spectral contrast (acoustic instruments have higher contrast in mid bands)
    Returns 0-1 where 1 = fully acoustic.
    """
    try:
        import librosa
        zcr = float(librosa.feature.zero_crossing_rate(y=y)[0].mean())
        contrast = librosa.feature.spectral_contrast(y=y, sr=sr)
        mid_contrast = float(contrast[2:5].mean())  # bands 2-4 (mid-range)

        zcr_score = float(np.clip(1.0 - zcr * 10, 0, 1))
        contrast_score = float(np.clip(mid_contrast / 40.0, 0, 1))
        return round(float(0.4 * zcr_score + 0.6 * contrast_score), 4)
    except Exception:
        return 0.5


# ── Jellyfin streaming ────────────────────────────────────────────────────────

async def _get_jellyfin_context(db: Session) -> tuple[str, str] | None:
    """
    Returns (base_url, api_key) for Jellyfin API calls, or None if Jellyfin
    is not configured or unreachable.  Call once per batch; do not call per-track.
    """
    import httpx
    from models import ConnectionSettings
    from crypto import decrypt

    conn = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not conn or not conn.base_url:
        return None

    base_url = conn.base_url.rstrip("/")
    api_key = decrypt(conn.api_key_encrypted)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{base_url}/System/Ping",
                headers={"X-Emby-Token": api_key},
            )
        if r.status_code not in (200, 204):
            return None
    except Exception as exc:
        log.warning("Jellyfin ping failed: %s", exc)
        return None

    return base_url, api_key


async def _stream_track_bytes(
    jellyfin_item_id: str,
    base_url: str,
    api_key: str,
) -> bytes | None:
    """
    Download the raw audio file for a track from Jellyfin and return its bytes.

    Returns None on any network error or non-200 response.
    """
    import httpx

    url = f"{base_url}/Audio/{jellyfin_item_id}/stream"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.get(url, params={"static": "true", "api_key": api_key})
        if r.status_code != 200:
            log.debug("Jellyfin stream returned %d for item %s", r.status_code, jellyfin_item_id)
            return None
        return r.content
    except Exception as exc:
        log.debug("Jellyfin stream failed for %s: %s", jellyfin_item_id, exc)
        return None


# ── Per-track analysis ────────────────────────────────────────────────────────

def _analyze_audio(audio_bytes: bytes, step_cb=None) -> dict:
    """
    Decode raw audio bytes with librosa and extract all features.
    Returns a dict of feature name → value ready to write to LibraryTrack.
    Raises on unrecoverable errors.

    step_cb(msg) is called before each major step so the caller can surface
    fine-grained progress to the UI.
    """
    def _step(msg):
        if step_cb:
            step_cb(msg)

    import librosa

    _step("Decoding audio…")
    y, sr = librosa.load(io.BytesIO(audio_bytes), sr=_SAMPLE_RATE, mono=True)
    if len(y) < sr:  # less than 1 second → skip
        raise ValueError("Audio too short")

    _step("Detecting BPM…")
    tempo_frames = librosa.feature.tempo(y=y, sr=sr)
    bpm = int(round(float(np.median(tempo_frames))))
    bpm = max(20, min(300, bpm))

    _step("Detecting key…")
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    musical_key, key_confidence = _detect_key(chroma)

    _step("Measuring loudness…")
    rms = librosa.feature.rms(y=y)[0]
    loudness_db = float(librosa.amplitude_to_db(rms, ref=np.max).mean())
    energy = float(np.clip((loudness_db + 60) / 60, 0.0, 1.0))

    _step("Measuring beat strength…")
    hop = 512
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    peak = float(onset_env.max()) or 1e-6
    beat_strength = float(np.clip(onset_env.mean() / peak, 0, 1))

    _step("Estimating time signature…")
    time_sig = _estimate_time_signature(onset_env, sr, hop)

    _step("Estimating acousticness…")
    acousticness = _estimate_acousticness(y, sr)

    return {
        "bpm": bpm,
        "musical_key": musical_key,
        "key_confidence": round(key_confidence, 4),
        "energy": round(energy, 4),
        "loudness_db": round(loudness_db, 2),
        "beat_strength": round(beat_strength, 4),
        "time_signature": time_sig,
        "acousticness": acousticness,
    }


# ── Batch runner ──────────────────────────────────────────────────────────────

async def analyze_new_tracks(
    db: Session,
    set_state=None,   # callable(done, total, failed, phase, current_track=None, status_line=None)
    version: int = CURRENT_VERSION,
):
    """
    Analyze all LibraryTrack rows that have never been analyzed or whose
    audio_analysis_version is older than `version`.

    Audio is streamed from Jellyfin's /Audio/{id}/stream endpoint — no local
    filesystem path mapping is required.  Commits every _BATCH_COMMIT tracks
    to avoid long DB lock periods.
    """
    from models import LibraryTrack

    def _emit(done, total, failed, phase, current_track=None, status_line=None):
        if set_state:
            set_state(done, total, failed, phase,
                      current_track=current_track, status_line=status_line)

    pending = db.query(LibraryTrack).filter(
        or_(
            LibraryTrack.audio_analyzed_at.is_(None),
            LibraryTrack.audio_analysis_version < version,
        )
    ).all()

    total = len(pending)
    done = 0
    failed = 0

    log.info("Audio analysis: %d tracks to process", total)
    _emit(done, total, failed, "Starting…")

    jf = await _get_jellyfin_context(db)
    if jf is None:
        log.error("Audio analysis: Jellyfin is not configured or unreachable")
        _emit(0, total, total, "Jellyfin unavailable", status_line="✗ Could not reach Jellyfin")
        return {"done": 0, "failed": total, "total": total}
    jf_base_url, jf_api_key = jf

    for i, track in enumerate(pending):
        item_id = track.jellyfin_item_id
        label = f"{track.artist_name} – {track.track_name}" if track.track_name else item_id
        phase = f"Scanning… ({i + 1}/{total})"

        _emit(done, total, failed, phase,
              current_track=label, status_line=f"Fetching audio: {label}")

        try:
            audio_bytes = await _stream_track_bytes(item_id, jf_base_url, jf_api_key)
            if audio_bytes is None:
                log.debug("No audio received for %s", label)
                failed += 1
                _emit(done, total, failed, phase,
                      current_track=label, status_line=f"⚠ Stream failed: {label}")
                continue

            def _step(msg, _label=label):
                _emit(done, total, failed, phase,
                      current_track=_label, status_line=f"{msg} — {_label}")

            features = _analyze_audio(audio_bytes, step_cb=_step)
            for attr, val in features.items():
                setattr(track, attr, val)
            track.audio_analyzed_at = datetime.utcnow()
            track.audio_analysis_version = version
            done += 1

            result_line = (
                f"✓ {label}  —  "
                f"{features['bpm']} BPM · {features['musical_key']} · "
                f"energy {features['energy']:.2f} · "
                f"beat strength {features['beat_strength']:.2f}"
            )
            log.info(result_line)
            _emit(done, total, failed, phase,
                  current_track=label, status_line=result_line)

        except Exception as exc:
            log.warning("Audio analysis failed for %s: %s", label, exc)
            failed += 1
            _emit(done, total, failed, phase,
                  current_track=label, status_line=f"✗ Error: {label} — {exc}")

        if (i + 1) % _BATCH_COMMIT == 0:
            db.commit()

    db.commit()
    log.info("Audio analysis complete: %d done, %d failed / skipped", done, failed)
    _emit(done, total, failed, "Complete",
          status_line=f"✓ Complete — {done} analyzed, {failed} skipped")
    return {"done": done, "failed": failed, "total": total}
