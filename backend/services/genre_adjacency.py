"""
JellyDJ — Genre Adjacency Map

A hardcoded web of genre relationships used by the genre_adjacent playlist block.
Relationships are bidirectional where genres are siblings, and parent→child for
broader-to-specific genre containment (e.g., Pop contains Indie Pop).

Design principles:
  - Hip-Hop / Rap is deliberately isolated from Folk, Rock, Classical, and Country.
    The only bridges are: R&B (via hip-hop soul), Electronic (via trip-hop / lo-fi),
    and Jazz (via acid jazz).  This prevents rap from bleeding into unrelated
    recommendation paths.
  - Pop is a commercial crossover hub and connects broadly, but NOT to Hip-Hop
    directly.  Pop Rap is the bridge and is listed under Hip-Hop, not Pop.
  - Blues is the ancestral root of most American popular music and has wide
    but shallow connections.
  - Classical connects only to Ambient / New Age / Progressive Rock / Soundtrack.
  - All keys and values use the same normalisation as norm_genre():
    lowercase, hyphens/underscores → spaces, collapsed whitespace.

Usage:
    from services.genre_adjacency import GENRE_ADJACENCY, norm_genre
"""
from __future__ import annotations
import re


def norm_genre(s: str) -> str:
    """Normalise a genre string for map lookup (mirrors recommender._norm_genre)."""
    s = s.lower().strip()
    s = re.sub(r"[-_]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


# ---------------------------------------------------------------------------
# Genre adjacency web
# Each key maps to a list of directly adjacent genres.
# The relationship is conceptually bidirectional (A→B implies B→A), but both
# directions are written explicitly so lookups are a single dict get().
# ---------------------------------------------------------------------------

GENRE_ADJACENCY: dict[str, list[str]] = {

    # ── ROCK ────────────────────────────────────────────────────────────────
    "rock": [
        "classic rock", "hard rock", "pop rock", "blues rock",
        "alternative rock", "progressive rock", "psychedelic rock",
        "indie rock", "garage rock",
    ],
    "classic rock": ["rock", "hard rock", "blues rock", "psychedelic rock"],
    "hard rock": ["rock", "heavy metal", "classic rock", "glam rock"],
    "pop rock": ["rock", "pop", "alternative rock", "indie rock", "power pop"],
    "alternative rock": [
        "rock", "indie rock", "pop rock", "grunge", "post punk", "new wave", "garage rock",
    ],
    "indie rock": [
        "alternative rock", "indie pop", "post punk", "garage rock", "art rock", "pop rock",
    ],
    "progressive rock": [
        "rock", "art rock", "jazz fusion", "psychedelic rock", "classic rock",
    ],
    "psychedelic rock": ["rock", "classic rock", "progressive rock", "indie rock"],
    "grunge": ["alternative rock", "punk", "heavy metal", "indie rock"],
    "punk": ["alternative rock", "post punk", "garage rock", "hardcore punk", "grunge", "ska"],
    "hardcore punk": ["punk", "alternative rock", "heavy metal"],
    "post punk": ["punk", "alternative rock", "new wave", "indie rock", "gothic rock"],
    "new wave": ["post punk", "synth pop", "pop", "alternative rock"],
    "heavy metal": ["hard rock", "rock", "thrash metal", "doom metal", "hardcore punk"],
    "thrash metal": ["heavy metal", "hard rock", "punk"],
    "doom metal": ["heavy metal", "gothic rock"],
    "gothic rock": ["post punk", "alternative rock", "doom metal"],
    "garage rock": ["rock", "indie rock", "punk", "blues rock", "alternative rock"],
    "blues rock": ["rock", "blues", "classic rock", "hard rock", "garage rock"],
    "art rock": ["progressive rock", "indie rock", "alternative rock"],
    "glam rock": ["rock", "hard rock", "pop rock"],
    "folk rock": ["rock", "folk", "americana", "singer songwriter", "indie folk"],
    "country rock": ["rock", "country", "americana", "alt country"],
    "power pop": ["pop rock", "indie pop", "rock", "alternative rock"],
    "pop punk": ["punk", "pop rock", "alternative rock", "indie pop"],
    "emo": ["punk", "post punk", "pop punk", "alternative rock"],

    # ── POP ─────────────────────────────────────────────────────────────────
    "pop": [
        "pop rock", "dance pop", "indie pop", "synth pop", "electro pop",
        "r&b", "country pop", "latin pop", "new wave",
    ],
    "dance pop": ["pop", "electronic", "electro pop", "house", "synth pop"],
    "indie pop": [
        "pop", "indie rock", "pop rock", "alternative rock", "folk",
        "indie folk", "singer songwriter", "electro pop",
    ],
    "synth pop": ["pop", "electronic", "new wave", "electro pop", "dance pop", "synthwave"],
    "electro pop": ["pop", "synth pop", "electronic", "dance pop", "indie pop"],
    "country pop": ["pop", "country"],
    "latin pop": ["pop", "latin"],
    "k pop": ["pop", "dance pop", "electro pop"],
    "j pop": ["pop", "dance pop"],
    "sophisti pop": ["pop", "r&b", "soul", "jazz"],
    "dream pop": ["indie pop", "shoegaze", "alternative rock", "ambient"],
    "shoegaze": ["dream pop", "alternative rock", "indie rock", "post punk"],

    # ── ELECTRONIC ──────────────────────────────────────────────────────────
    "electronic": [
        "house", "techno", "synth pop", "ambient", "downtempo",
        "drum and bass", "trip hop", "electronica",
    ],
    "electronica": ["electronic", "ambient", "idm", "downtempo", "synth pop"],
    "house": ["electronic", "techno", "deep house", "progressive house", "dance pop", "disco"],
    "deep house": ["house", "electronic", "ambient", "downtempo"],
    "progressive house": ["house", "trance", "electronic"],
    "tech house": ["house", "techno", "electronic"],
    "tropical house": ["house", "pop", "dance pop", "electronic"],
    "techno": ["electronic", "house", "industrial", "minimal techno"],
    "minimal techno": ["techno", "electronic", "ambient"],
    "trance": ["electronic", "progressive house", "ambient", "psy trance"],
    "psy trance": ["trance", "electronic", "psychedelic rock"],
    "drum and bass": ["electronic", "jungle", "breakbeat"],
    "jungle": ["drum and bass", "electronic", "dub", "reggae"],
    "breakbeat": ["electronic", "drum and bass"],
    "dubstep": ["electronic", "drum and bass", "future bass", "grime"],
    "future bass": ["electronic", "dubstep", "pop", "dance pop"],
    "ambient": [
        "electronic", "downtempo", "new age", "classical", "deep house",
        "minimal techno", "dream pop",
    ],
    "downtempo": ["electronic", "ambient", "trip hop", "chillout", "nu jazz"],
    "chillout": ["downtempo", "ambient", "trip hop", "electronica"],
    "trip hop": ["electronic", "downtempo", "soul", "jazz", "hip hop soul"],
    "synthwave": ["electronic", "synth pop", "new wave", "electro pop"],
    "vaporwave": ["synthwave", "electronic", "chillout"],
    "idm": ["electronic", "ambient", "experimental", "electronica"],
    "experimental": ["idm", "art rock", "free jazz", "ambient"],
    "industrial": ["electronic", "techno", "heavy metal"],
    "grime": ["electronic", "dubstep", "hip hop"],
    "disco": ["funk", "house", "pop", "soul", "r&b"],
    "nu disco": ["disco", "house", "electronic", "indie pop"],
    "chillwave": ["electronic", "synth pop", "indie pop", "ambient", "chillout"],

    # ── JAZZ ────────────────────────────────────────────────────────────────
    "jazz": [
        "blues", "soul", "funk", "bossa nova", "jazz fusion",
        "swing", "smooth jazz", "latin jazz", "nu jazz",
    ],
    "jazz fusion": ["jazz", "progressive rock", "funk", "rock", "acid jazz"],
    "bebop": ["jazz", "swing", "cool jazz", "hard bop"],
    "cool jazz": ["jazz", "bebop", "bossa nova", "smooth jazz"],
    "hard bop": ["jazz", "bebop", "blues", "soul jazz"],
    "modal jazz": ["jazz", "bebop", "free jazz"],
    "free jazz": ["jazz", "experimental", "avant garde"],
    "avant garde": ["free jazz", "experimental", "classical"],
    "swing": ["jazz", "blues", "big band", "bebop"],
    "big band": ["jazz", "swing", "blues"],
    "acid jazz": ["jazz", "funk", "soul", "downtempo", "nu jazz"],
    "smooth jazz": ["jazz", "soul", "r&b", "easy listening", "cool jazz"],
    "latin jazz": ["jazz", "latin", "salsa", "bossa nova"],
    "soul jazz": ["jazz", "soul", "funk", "hard bop"],
    "nu jazz": ["jazz", "electronic", "downtempo", "acid jazz", "trip hop"],
    "easy listening": ["smooth jazz", "pop", "soul", "new age"],
    "new age": ["ambient", "classical", "easy listening", "folk"],

    # ── BLUES ───────────────────────────────────────────────────────────────
    "blues": [
        "jazz", "soul", "rock", "country", "folk", "r&b",
        "blues rock", "chicago blues", "delta blues",
    ],
    "chicago blues": ["blues", "rock", "r&b", "electric blues"],
    "delta blues": ["blues", "folk", "country blues"],
    "country blues": ["blues", "country", "folk", "delta blues"],
    "electric blues": ["blues", "blues rock", "rock", "chicago blues"],

    # ── R&B / SOUL ──────────────────────────────────────────────────────────
    "r&b": [
        "soul", "pop", "funk", "neo soul", "contemporary r&b",
        "motown", "hip hop soul", "disco",
    ],
    "soul": [
        "r&b", "blues", "jazz", "gospel", "funk",
        "neo soul", "motown", "soul jazz",
    ],
    "funk": ["soul", "r&b", "disco", "jazz", "blues", "acid jazz"],
    "neo soul": ["soul", "r&b", "jazz", "funk", "hip hop soul", "trip hop"],
    "gospel": ["soul", "blues", "country", "r&b", "americana"],
    "motown": ["soul", "r&b", "pop", "funk"],
    "hip hop soul": ["r&b", "soul", "neo soul", "trip hop"],
    # ^ hip hop soul is the R&B ↔ Hip-Hop bridge — listed under both families
    "contemporary r&b": ["r&b", "pop", "soul", "neo soul"],
    "quiet storm": ["r&b", "soul", "smooth jazz", "easy listening"],

    # ── COUNTRY / AMERICANA ─────────────────────────────────────────────────
    "country": [
        "americana", "folk", "bluegrass", "country rock", "blues",
        "country pop", "outlaw country", "honky tonk",
    ],
    "americana": [
        "country", "folk", "blues", "bluegrass", "rock",
        "singer songwriter", "country rock", "gospel",
    ],
    "bluegrass": ["country", "folk", "americana", "country blues"],
    "outlaw country": ["country", "americana", "rock", "blues"],
    "alt country": ["country", "americana", "indie rock", "folk rock"],
    "honky tonk": ["country", "americana", "blues"],
    "western": ["country", "folk", "americana"],

    # ── FOLK ────────────────────────────────────────────────────────────────
    "folk": [
        "americana", "singer songwriter", "indie folk", "celtic folk",
        "bluegrass", "country", "blues", "folk rock", "acoustic",
    ],
    "indie folk": [
        "folk", "indie pop", "singer songwriter", "alternative rock",
        "folk rock", "dream pop",
    ],
    "celtic folk": ["folk", "folk rock", "world"],
    "singer songwriter": [
        "folk", "indie folk", "americana", "pop", "indie pop", "acoustic",
    ],
    "acoustic": ["folk", "singer songwriter", "indie folk", "country"],
    "world": ["folk", "latin", "reggae", "celtic folk", "jazz"],

    # ── LATIN ───────────────────────────────────────────────────────────────
    "latin": [
        "latin pop", "salsa", "bossa nova", "latin jazz",
        "cumbia", "tango", "merengue", "samba", "world",
    ],
    "salsa": ["latin", "latin jazz", "cumbia", "merengue", "son cubano"],
    "samba": ["bossa nova", "latin", "world"],
    "bossa nova": ["samba", "jazz", "latin", "cool jazz", "latin jazz"],
    "reggaeton": ["latin", "latin pop", "dancehall", "cumbia", "hip hop"],
    # ^ reggaeton is Latin AND Hip-Hop adjacent — intentional
    "cumbia": ["latin", "salsa", "merengue"],
    "tango": ["latin", "world", "jazz"],
    "merengue": ["latin", "salsa", "cumbia", "bachata"],
    "bachata": ["latin", "merengue", "salsa"],
    "son cubano": ["salsa", "latin", "latin jazz"],
    "bolero": ["latin", "jazz", "easy listening"],

    # ── REGGAE ──────────────────────────────────────────────────────────────
    "reggae": ["ska", "dancehall", "dub", "world", "soul", "roots reggae"],
    "roots reggae": ["reggae", "gospel", "soul"],
    "ska": ["reggae", "punk", "rocksteady", "soul"],
    "rocksteady": ["ska", "reggae", "soul"],
    "dancehall": ["reggae", "reggaeton"],
    "dub": ["reggae", "electronic", "jungle", "trip hop"],

    # ── HIP-HOP / RAP ───────────────────────────────────────────────────────
    # Deliberately isolated from Folk, Rock, Classical, and Country.
    # Bridges: R&B (hip hop soul), Electronic (trip hop, lo fi), Jazz (acid jazz).
    "hip hop": [
        "r&b", "hip hop soul", "alternative hip hop", "trap",
        "lo fi hip hop", "grime",
    ],
    "trap": ["hip hop", "electronic"],
    "alternative hip hop": ["hip hop", "r&b", "jazz", "trip hop"],
    "lo fi hip hop": ["hip hop", "jazz", "downtempo", "chillout", "nu jazz"],
    "old school hip hop": ["hip hop", "r&b", "funk"],
    "gangsta rap": ["hip hop", "trap"],
    "rap": ["hip hop", "trap", "r&b"],

    # ── CLASSICAL ───────────────────────────────────────────────────────────
    "classical": ["ambient", "new age", "progressive rock", "avant garde", "soundtrack"],
    "orchestral": ["classical", "soundtrack", "ambient"],
    "opera": ["classical"],
    "soundtrack": ["orchestral", "classical", "ambient", "new age"],
    "chamber music": ["classical", "jazz", "ambient"],
}
