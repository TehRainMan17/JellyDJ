"""
Tests for the YouTube rip cover art pipeline added in the image-on-ingest feature.

Covers:
- _best_thumbnail: selects highest-resolution thumbnail from yt-dlp info
- _crop_to_square: center-crops a 16:9 JPEG to square
- _fetch_cover_art: prefers Cover Art Archive, falls back to YouTube thumbnail
- _embed_cover_art: embeds APIC frame into an MP3
- _save_cover_file: writes cover.jpg, skips if already present

Run inside the container:
    docker exec jellydj-backend python -m pytest tests/test_youtube_rip_cover_art.py -v
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Stub heavy backend/container deps so the pure helpers can be imported ──────
# The router imports fastapi, sqlalchemy, auth, database, etc. at module load.
# We only need the pure helper functions here — stub out everything that would
# require the full container environment.
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-for-unit-tests-32b!")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_STUB_MODS = [
    "fastapi", "fastapi.params",
    "sqlalchemy", "sqlalchemy.orm",
    "pydantic",
    "auth", "database", "models", "crypto",
    "routers.playlist_import",
    # httpx is used inside the helpers but may not be installed locally;
    # stub it so import succeeds and individual tests can control its behaviour.
    "httpx",
]
for _m in _STUB_MODS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()

# pydantic.BaseModel must survive as a real base class — use object as fallback
_pydantic = sys.modules["pydantic"]
if not hasattr(_pydantic.BaseModel, "__init_subclass__"):
    _pydantic.BaseModel = object

# ---------------------------------------------------------------------------
# Helpers to build minimal test fixtures
# ---------------------------------------------------------------------------

def _make_jpeg_bytes(width: int, height: int, color=(100, 150, 200)) -> bytes:
    """Return a minimal JPEG image of the given dimensions."""
    from PIL import Image
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_mp3(path: Path) -> None:
    """Write a tiny valid MP3 (silent, ID3v2.3 header) for mutagen to open."""
    # mutagen can open a file with an empty-ish frame; use a real silent MP3
    # fixture if available, otherwise create one with pydub/ffmpeg.  For
    # lightweight unit tests we use mutagen itself to bootstrap a bare file.
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TIT2

    # Write 1 frame of silence (valid enough for mutagen)
    # Build a minimal ID3 header + one blank MPEG frame so mutagen can open it.
    # Rather than encoding real audio we write a 32-byte fake frame that mutagen
    # accepts for tag operations.
    id3_header = (
        b"ID3"           # magic
        b"\x03\x00"      # version 2.3.0
        b"\x00"          # flags
        b"\x00\x00\x00\x00"  # size = 0 (no tags yet)
    )
    # Minimal valid MPEG frame: sync word + layer 3 + 128kbps + 44100Hz + stereo
    mpeg_frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    path.write_bytes(id3_header + mpeg_frame)


# ---------------------------------------------------------------------------
# _best_thumbnail
# ---------------------------------------------------------------------------

class TestBestThumbnail:
    def test_picks_largest(self):
        from routers.youtube_rip import _best_thumbnail
        info = {
            "thumbnails": [
                {"url": "http://small.jpg", "width": 120, "height": 90},
                {"url": "http://large.jpg", "width": 1280, "height": 720},
                {"url": "http://medium.jpg", "width": 640, "height": 480},
            ]
        }
        assert _best_thumbnail(info) == "http://large.jpg"

    def test_falls_back_to_thumbnail_key(self):
        from routers.youtube_rip import _best_thumbnail
        info = {"thumbnail": "http://fallback.jpg"}
        assert _best_thumbnail(info) == "http://fallback.jpg"

    def test_empty_returns_none(self):
        from routers.youtube_rip import _best_thumbnail
        assert _best_thumbnail({}) is None

    def test_skips_thumbnails_without_url(self):
        from routers.youtube_rip import _best_thumbnail
        info = {
            "thumbnails": [
                {"width": 1280, "height": 720},          # no url
                {"url": "http://ok.jpg", "width": 640, "height": 480},
            ]
        }
        assert _best_thumbnail(info) == "http://ok.jpg"


# ---------------------------------------------------------------------------
# _crop_to_square
# ---------------------------------------------------------------------------

class TestCropToSquare:
    def test_16x9_becomes_square(self):
        pytest.importorskip("PIL")
        from routers.youtube_rip import _crop_to_square
        from PIL import Image

        img_bytes = _make_jpeg_bytes(1280, 720)
        result = _crop_to_square(img_bytes)
        img = Image.open(io.BytesIO(result))
        w, h = img.size
        assert w == h == 720

    def test_already_square_unchanged_dimensions(self):
        pytest.importorskip("PIL")
        from routers.youtube_rip import _crop_to_square
        from PIL import Image

        img_bytes = _make_jpeg_bytes(500, 500)
        result = _crop_to_square(img_bytes)
        img = Image.open(io.BytesIO(result))
        assert img.size == (500, 500)

    def test_portrait_becomes_square(self):
        pytest.importorskip("PIL")
        from routers.youtube_rip import _crop_to_square
        from PIL import Image

        img_bytes = _make_jpeg_bytes(400, 600)
        result = _crop_to_square(img_bytes)
        img = Image.open(io.BytesIO(result))
        w, h = img.size
        assert w == h == 400

    def test_returns_original_bytes_on_pil_failure(self):
        from routers.youtube_rip import _crop_to_square
        garbage = b"not an image"
        result = _crop_to_square(garbage)
        assert result == garbage


# ---------------------------------------------------------------------------
# _fetch_cover_art
# ---------------------------------------------------------------------------

class TestFetchCoverArt:
    """
    _fetch_cover_art does `import httpx` inside the function body.
    We control httpx behaviour by patching sys.modules['httpx'].get directly,
    which is what the function resolves to after our stub is in place.

    Signature: _fetch_cover_art(release_mbid, release_group_mbid, thumbnail_url)
    """

    def _mock_response(self, status: int, content: bytes):
        resp = MagicMock()
        resp.status_code = status
        resp.content = content
        return resp

    def test_uses_release_caa_when_release_mbid_present(self):
        from routers.youtube_rip import _fetch_cover_art
        fake_art = b"JPEG_BYTES"
        sys.modules["httpx"].get.return_value = self._mock_response(200, fake_art)
        result = _fetch_cover_art("some-release-mbid", None, "http://thumbnail.jpg")
        assert result == fake_art
        call_url = sys.modules["httpx"].get.call_args[0][0]
        assert "coverartarchive.org/release/some-release-mbid" in call_url

    def test_falls_back_to_release_group_caa_when_release_404(self):
        from routers.youtube_rip import _fetch_cover_art
        fake_art = b"RELEASE_GROUP_ART"
        sys.modules["httpx"].get.side_effect = [
            self._mock_response(404, b""),           # release MBID → 404
            self._mock_response(200, fake_art),      # release-group MBID → 200
        ]
        result = _fetch_cover_art("rel-mbid", "rg-mbid", "http://thumbnail.jpg")
        assert result == fake_art
        # Second call should target the release-group endpoint
        second_url = sys.modules["httpx"].get.call_args_list[1][0][0]
        assert "coverartarchive.org/release-group/rg-mbid" in second_url
        sys.modules["httpx"].get.side_effect = None

    def test_falls_back_to_thumbnail_when_no_mbids(self):
        pytest.importorskip("PIL")
        from routers.youtube_rip import _fetch_cover_art
        thumbnail_bytes = _make_jpeg_bytes(1280, 720)
        sys.modules["httpx"].get.return_value = self._mock_response(200, thumbnail_bytes)
        result = _fetch_cover_art(None, None, "http://thumbnail.jpg")
        assert result is not None
        from PIL import Image
        img = Image.open(io.BytesIO(result))
        assert img.size == (720, 720)

    def test_falls_back_to_thumbnail_when_both_caa_fail(self):
        pytest.importorskip("PIL")
        from routers.youtube_rip import _fetch_cover_art
        thumbnail_bytes = _make_jpeg_bytes(1280, 720)
        sys.modules["httpx"].get.side_effect = [
            self._mock_response(404, b""),           # release → 404
            self._mock_response(404, b""),           # release-group → 404
            self._mock_response(200, thumbnail_bytes),
        ]
        result = _fetch_cover_art("rel-mbid", "rg-mbid", "http://thumbnail.jpg")
        assert result is not None
        sys.modules["httpx"].get.side_effect = None

    def test_returns_none_when_all_sources_fail(self):
        from routers.youtube_rip import _fetch_cover_art
        sys.modules["httpx"].get.side_effect = Exception("network error")
        result = _fetch_cover_art("some-mbid", "rg-mbid", "http://thumbnail.jpg")
        assert result is None
        sys.modules["httpx"].get.side_effect = None

    def test_returns_none_when_no_sources(self):
        from routers.youtube_rip import _fetch_cover_art
        result = _fetch_cover_art(None, None, None)
        assert result is None

    def test_skips_release_group_when_only_release_group_missing(self):
        """If only release_mbid is present and succeeds, release-group is never called."""
        from routers.youtube_rip import _fetch_cover_art
        fake_art = b"ART"
        sys.modules["httpx"].get.return_value = self._mock_response(200, fake_art)
        result = _fetch_cover_art("rel-mbid", None, "http://thumbnail.jpg")
        assert result == fake_art
        assert sys.modules["httpx"].get.call_count == 1


# ---------------------------------------------------------------------------
# _save_cover_file
# ---------------------------------------------------------------------------

class TestSaveCoverFile:
    def test_writes_cover_jpg(self):
        from routers.youtube_rip import _save_cover_file
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            _save_cover_file(dest, b"FAKE_IMAGE")
            assert (dest / "cover.jpg").read_bytes() == b"FAKE_IMAGE"

    def test_does_not_overwrite_existing_cover(self):
        from routers.youtube_rip import _save_cover_file
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            cover = dest / "cover.jpg"
            cover.write_bytes(b"ORIGINAL")
            _save_cover_file(dest, b"NEW_IMAGE")
            assert cover.read_bytes() == b"ORIGINAL"


# ---------------------------------------------------------------------------
# _embed_cover_art (integration with mutagen)
# ---------------------------------------------------------------------------

class TestEmbedCoverArt:
    def test_embeds_apic_frame(self):
        """Verify _embed_cover_art calls mutagen with the correct APIC frame."""
        pytest.importorskip("mutagen")
        from routers.youtube_rip import _embed_cover_art
        from mutagen.id3 import APIC

        mock_tags = MagicMock()
        mock_audio = MagicMock()
        mock_audio.tags = mock_tags

        fake_art = b"FAKE_JPEG"
        with patch("mutagen.mp3.MP3", return_value=mock_audio):
            _embed_cover_art(Path("/fake/song.mp3"), fake_art)

        mock_tags.delall.assert_called_once_with("APIC")
        assert mock_tags.add.called
        added_frame = mock_tags.add.call_args[0][0]
        assert isinstance(added_frame, APIC)
        assert added_frame.data == fake_art
        assert added_frame.type == 3        # Front cover
        mock_audio.save.assert_called_once()

    def test_embed_is_non_fatal_on_corrupt_file(self):
        """_embed_cover_art should log a warning and not raise on a bad file."""
        from routers.youtube_rip import _embed_cover_art
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "corrupt.mp3"
            bad_path.write_bytes(b"not an mp3")
            # Should not raise
            _embed_cover_art(bad_path, b"some bytes")
