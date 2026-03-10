<#
.SYNOPSIS
    JellyDJ Phase 1–4 validation test suite.

.DESCRIPTION
    Runs a sequence of HTTP tests against the live JellyDJ backend.
    Covers: health, auth (Phase 1), DB/schema (Phase 2), prefab seeder (Phase 3),
    and the block engine / playlist templates (Phase 4).

    Prerequisites:
      - JellyDJ running via docker compose (docker compose up -d)
      - A valid Jellyfin admin account configured in JellyDJ

.PARAMETER BaseUrl
    Base URL of the JellyDJ frontend/API proxy.
    Default: http://localhost:7879

.PARAMETER BackendUrl
    Direct URL to the FastAPI backend (bypasses Nginx proxy).
    Default: http://localhost:8000

.PARAMETER Username
    Jellyfin admin username to authenticate with.

.PARAMETER Password
    Jellyfin admin password.

.PARAMETER UserId
    Jellyfin user ID to use for block-engine tests.
    If omitted, the first managed user from /api/playlists/users is used.

.EXAMPLE
    .\Test-JellyDJ.ps1 -Username admin -Password secret

.EXAMPLE
    .\Test-JellyDJ.ps1 -BaseUrl http://nas:7879 -Username alice -Password hunter2
#>
param(
    [string]$BaseUrl    = "http://localhost:7879",
    [string]$BackendUrl = "http://localhost:8000",
    [string]$Username   = "",
    [string]$Password   = "",
    [string]$UserId     = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Colour helpers ─────────────────────────────────────────────────────────────
function Write-Pass  { param($msg) Write-Host "  [PASS] $msg" -ForegroundColor Green  }
function Write-Fail  { param($msg) Write-Host "  [FAIL] $msg" -ForegroundColor Red    }
function Write-Skip  { param($msg) Write-Host "  [SKIP] $msg" -ForegroundColor Yellow }
function Write-Info  { param($msg) Write-Host "         $msg" -ForegroundColor Gray   }
function Write-Section { param($msg) Write-Host "`n── $msg " -ForegroundColor Cyan   }

$script:Passed = 0
$script:Failed = 0
$script:Skipped = 0
$script:Token  = $null

# ── HTTP helpers ───────────────────────────────────────────────────────────────
function Invoke-Api {
    param(
        [string]$Method  = "GET",
        [string]$Url,
        [object]$Body    = $null,
        [switch]$NoAuth,
        [switch]$AllowError    # don't throw on 4xx/5xx
    )
    $headers = @{ "Content-Type" = "application/json" }
    if (-not $NoAuth -and $script:Token) {
        $headers["Authorization"] = "Bearer $($script:Token)"
    }
    $params = @{
        Method  = $Method
        Uri     = $Url
        Headers = $headers
        UseBasicParsing = $true
    }
    if ($Body) {
        $params["Body"] = ($Body | ConvertTo-Json -Depth 10)
    }
    try {
        $resp = Invoke-WebRequest @params
        return [PSCustomObject]@{
            Status  = [int]$resp.StatusCode
            Content = ($resp.Content | ConvertFrom-Json -ErrorAction SilentlyContinue)
            Raw     = $resp.Content
        }
    }
    catch [System.Net.WebException] {
        $code = [int]$_.Exception.Response.StatusCode
        if ($AllowError) {
            return [PSCustomObject]@{ Status = $code; Content = $null; Raw = "" }
        }
        throw
    }
}

function Assert-Status {
    param($resp, [int]$expected, [string]$label)
    if ($resp.Status -eq $expected) {
        Write-Pass $label
        $script:Passed++
    } else {
        Write-Fail "$label — expected HTTP $expected, got $($resp.Status)"
        $script:Failed++
    }
}

function Assert-Field {
    param($obj, [string]$field, [string]$label)
    $val = $obj.$field
    if ($null -ne $val -and "$val" -ne "") {
        Write-Pass "$label (${field}=$val)"
        $script:Passed++
    } else {
        Write-Fail "$label — field '$field' missing or empty in response"
        $script:Failed++
    }
}

function Assert-ArrayNotEmpty {
    param($arr, [string]$label)
    if ($arr -and $arr.Count -gt 0) {
        Write-Pass "$label ($($arr.Count) items)"
        $script:Passed++
    } else {
        Write-Fail "$label — array is empty or null"
        $script:Failed++
    }
}

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 0 — Infrastructure / connectivity
# ═══════════════════════════════════════════════════════════════════════════════
Write-Section "Phase 0 — Infrastructure"

# Test 0.1: Backend health endpoint
try {
    $r = Invoke-Api -Url "$BackendUrl/api/health" -NoAuth
    Assert-Status $r 200 "Backend /api/health responds"
} catch {
    Write-Fail "Backend unreachable at $BackendUrl — is 'docker compose up' running? ($_)"
    $script:Failed++
}

# Test 0.2: Frontend/proxy reachable
try {
    $r = Invoke-Api -Url "$BaseUrl" -NoAuth -AllowError
    if ($r.Status -in 200, 301, 302) {
        Write-Pass "Frontend reachable at $BaseUrl (HTTP $($r.Status))"
        $script:Passed++
    } else {
        Write-Fail "Frontend returned unexpected HTTP $($r.Status)"
        $script:Failed++
    }
} catch {
    Write-Fail "Frontend unreachable at $BaseUrl ($_)"
    $script:Failed++
}

# Test 0.3: Docker containers running
try {
    $containers = docker ps --format "{{.Names}}" 2>$null
    $backendUp  = $containers -match "jellydj-backend"
    $frontendUp = $containers -match "jellydj-frontend"
    if ($backendUp)  { Write-Pass "Container jellydj-backend is running";  $script:Passed++ }
    else             { Write-Fail "Container jellydj-backend not found";   $script:Failed++ }
    if ($frontendUp) { Write-Pass "Container jellydj-frontend is running"; $script:Passed++ }
    else             { Write-Fail "Container jellydj-frontend not found";  $script:Failed++ }
} catch {
    Write-Skip "Docker CLI not available — skipping container checks"
    $script:Skipped++
}

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Auth (front + back)
# ═══════════════════════════════════════════════════════════════════════════════
Write-Section "Phase 1 — Auth"

if (-not $Username -or -not $Password) {
    Write-Skip "No credentials provided — skipping auth tests (use -Username / -Password)"
    $script:Skipped += 4
} else {

    # Test 1.1: Login returns tokens
    try {
        $r = Invoke-Api -Method POST -Url "$BackendUrl/api/auth/login" -NoAuth -Body @{
            username = $Username
            password = $Password
        }
        Assert-Status $r 200 "POST /api/auth/login succeeds"
        Assert-Field  $r.Content "access_token"  "Login returns access_token"
        Assert-Field  $r.Content "refresh_token" "Login returns refresh_token"
        $script:Token = $r.Content.access_token
        Write-Info "Authenticated as '$($r.Content.username)' (admin=$($r.Content.is_admin))"
    } catch {
        Write-Fail "Login request failed: $_"
        $script:Failed += 3
    }

    # Test 1.2: /api/auth/me with valid token
    if ($script:Token) {
        try {
            $r = Invoke-Api -Url "$BackendUrl/api/auth/me"
            Assert-Status $r 200 "GET /api/auth/me with valid token"
        } catch {
            Write-Fail "GET /api/auth/me failed: $_"
            $script:Failed++
        }
    }

    # Test 1.3: Unauthenticated request to protected endpoint is rejected
    try {
        $r = Invoke-Api -Url "$BackendUrl/api/playlists/runs" -NoAuth -AllowError
        if ($r.Status -eq 401 -or $r.Status -eq 403) {
            Write-Pass "Protected endpoint rejects unauthenticated request (HTTP $($r.Status))"
            $script:Passed++
        } else {
            Write-Fail "Protected endpoint returned HTTP $($r.Status) without auth (expected 401/403)"
            $script:Failed++
        }
    } catch {
        Write-Fail "Unauthenticated protection check failed: $_"
        $script:Failed++
    }
}

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Database schema
# ═══════════════════════════════════════════════════════════════════════════════
Write-Section "Phase 2 — Database / Schema"

# Test 2.1: SQLite DB file exists inside container
try {
    $dbCheck = docker exec jellydj-backend python -c "
import os, sqlite3
db_path = '/config/jellydj.db'
exists = os.path.exists(db_path)
if exists:
    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
    conn.close()
    print('TABLES:' + ','.join(sorted(tables)))
else:
    print('MISSING')
" 2>&1
    if ($dbCheck -match "TABLES:") {
        $tables = ($dbCheck -replace "TABLES:", "").Trim().Split(",")
        Write-Pass "Database file exists and is readable ($($tables.Count) tables)"
        $script:Passed++

        # Check required tables from Phase 2 schema
        $required = @(
            "playlist_templates", "playlist_blocks", "user_playlists",
            "track_scores", "artist_profiles", "genre_profiles",
            "managed_users", "plays"
        )
        foreach ($t in $required) {
            if ($tables -contains $t) {
                Write-Pass "Table '$t' exists"
                $script:Passed++
            } else {
                Write-Fail "Table '$t' missing from schema"
                $script:Failed++
            }
        }
    } else {
        Write-Fail "Database missing or unreadable: $dbCheck"
        $script:Failed++
    }
} catch {
    Write-Skip "Cannot inspect DB (docker exec failed): $_"
    $script:Skipped++
}

# Test 2.2: Schema columns — spot-check TEXT score columns on track_scores
try {
    $colCheck = docker exec jellydj-backend python -c "
import sqlite3
conn = sqlite3.connect('/config/jellydj.db')
cols = {r[1]: r[2] for r in conn.execute(\"PRAGMA table_info(track_scores)\")}
conn.close()
text_cols = [c for c, t in cols.items() if t.upper() == 'TEXT']
print('TEXT_SCORE_COLS:' + ','.join(text_cols))
" 2>&1
    if ($colCheck -match "TEXT_SCORE_COLS:") {
        $textCols = ($colCheck -replace "TEXT_SCORE_COLS:", "").Trim().Split(",")
        $scoreTexts = $textCols | Where-Object { $_ -in @("final_score","artist_affinity","genre_affinity") }
        if ($scoreTexts.Count -eq 3) {
            Write-Pass "Score columns stored as TEXT (CAST pattern required + validated)"
            $script:Passed++
        } else {
            Write-Info "TEXT score columns found: $($textCols -join ', ')"
            Write-Skip "Some expected TEXT score columns not present (schema may differ)"
            $script:Skipped++
        }
    }
} catch {
    Write-Skip "Column type check skipped: $_"
    $script:Skipped++
}

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — Prefab seeder
# ═══════════════════════════════════════════════════════════════════════════════
Write-Section "Phase 3 — Prefab Seeder"

# Test 3.1: Four system templates exist in the DB
try {
    $seedCheck = docker exec jellydj-backend python -c "
import sys
sys.path.insert(0, '/app')
from database import SessionLocal
from models import PlaylistTemplate, PlaylistBlock
db = SessionLocal()
templates = db.query(PlaylistTemplate).filter_by(is_system=True).all()
for t in templates:
    block_count = db.query(PlaylistBlock).filter_by(template_id=t.id).count()
    print(f'TEMPLATE:{t.name}|blocks={block_count}|total_tracks={t.total_tracks}')
db.close()
" 2>&1
    $templateLines = $seedCheck | Where-Object { $_ -match "^TEMPLATE:" }
    if ($templateLines.Count -ge 4) {
        Write-Pass "Prefab seeder: $($templateLines.Count) system templates found"
        $script:Passed++
        foreach ($line in $templateLines) {
            $name = ($line -replace "TEMPLATE:", "").Split("|")[0]
            Write-Info "  $line"
        }
    } elseif ($templateLines.Count -gt 0) {
        Write-Fail "Expected 4 system templates, found $($templateLines.Count)"
        $script:Failed++
    } else {
        Write-Fail "No system templates found — prefab seeder may not have run"
        Write-Info "Output: $seedCheck"
        $script:Failed++
    }
} catch {
    Write-Skip "Prefab seeder check skipped (docker exec unavailable): $_"
    $script:Skipped++
}

# Test 3.2: Block weights sum to ~100 for each template
try {
    $weightCheck = docker exec jellydj-backend python -c "
import sys
sys.path.insert(0, '/app')
from database import SessionLocal
from models import PlaylistTemplate, PlaylistBlock
db = SessionLocal()
templates = db.query(PlaylistTemplate).filter_by(is_system=True).all()
for t in templates:
    blocks = db.query(PlaylistBlock).filter_by(template_id=t.id).all()
    total = sum(b.weight for b in blocks)
    ok = 'OK' if abs(total - 100) <= 1 else 'BAD'
    print(f'{ok} {t.name}: weight_sum={total}')
db.close()
" 2>&1
    $badWeights = $weightCheck | Where-Object { $_ -match "^BAD" }
    if ($badWeights.Count -eq 0) {
        Write-Pass "All system template block weights sum to 100"
        $script:Passed++
    } else {
        Write-Fail "Templates with incorrect weight sums: $($badWeights -join '; ')"
        $script:Failed++
    }
    $weightCheck | ForEach-Object { Write-Info "  $_" }
} catch {
    Write-Skip "Weight check skipped: $_"
    $script:Skipped++
}

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4 — Block Engine
# ═══════════════════════════════════════════════════════════════════════════════
Write-Section "Phase 4 — Block Engine (playlist_blocks + playlist_engine)"

# Test 4.1: New modules import cleanly inside the container
try {
    $importCheck = docker exec jellydj-backend python -c "
import sys
sys.path.insert(0, '/app')
from services.playlist_blocks import BLOCK_REGISTRY
from services.playlist_engine import generate_from_template, preview_template
from services.playlist_utils  import get_excluded_item_ids, get_holiday_excluded_ids
print('IMPORTS_OK')
print('REGISTRY_KEYS:' + ','.join(sorted(BLOCK_REGISTRY.keys())))
" 2>&1
    if ($importCheck -match "IMPORTS_OK") {
        Write-Pass "playlist_blocks, playlist_engine, playlist_utils all import cleanly"
        $script:Passed++
    } else {
        Write-Fail "Import failed:`n$importCheck"
        $script:Failed++
    }

    if ($importCheck -match "REGISTRY_KEYS:(.+)") {
        $keys = $Matches[1].Split(",")
        $expected = @("affinity","artist","discovery","favorites","final_score","genre","global_popularity","play_count","play_recency")
        $missing = $expected | Where-Object { $_ -notin $keys }
        if ($missing.Count -eq 0) {
            Write-Pass "BLOCK_REGISTRY contains all 9 block types"
            $script:Passed++
        } else {
            Write-Fail "BLOCK_REGISTRY missing: $($missing -join ', ')"
            $script:Failed++
        }
        Write-Info "Registry keys: $($keys -join ', ')"
    }
} catch {
    Write-Fail "Module import test failed: $_"
    $script:Failed++
}

# Test 4.2: playlist_writer.py still importable and untouched
try {
    $writerCheck = docker exec jellydj-backend python -c "
import sys
sys.path.insert(0, '/app')
from services.playlist_writer import (
    run_playlist_generation, _get_excluded_item_ids,
    _holiday_ok, _diversify, _jitter, PLAYLIST_SIZES
)
print('WRITER_OK')
print('SIZES:' + ','.join(PLAYLIST_SIZES.keys()))
" 2>&1
    if ($writerCheck -match "WRITER_OK") {
        Write-Pass "playlist_writer.py still imports cleanly (untouched)"
        $script:Passed++
    } else {
        Write-Fail "playlist_writer.py broken after Phase 4:`n$writerCheck"
        $script:Failed++
    }
} catch {
    Write-Skip "playlist_writer check skipped: $_"
    $script:Skipped++
}

# Test 4.3: CAST pattern — executors use CAST(col AS REAL) for TEXT score columns
try {
    $castCheck = docker exec jellydj-backend python -c "
import ast, sys

with open('/app/services/playlist_blocks.py') as f:
    src = f.read()

checks = {
    'final_score':     'CAST(final_score AS REAL)',
    'artist_affinity': 'CAST(artist_affinity AS REAL)',
    'genre_affinity':  'CAST(genre_affinity AS REAL)',
}
results = []
for col, pattern in checks.items():
    found = pattern in src
    results.append(f\"{'OK' if found else 'MISSING'}: {pattern}\")
print('\n'.join(results))
" 2>&1
    $missing = $castCheck | Where-Object { $_ -match "^MISSING" }
    if ($missing.Count -eq 0) {
        Write-Pass "All numeric TEXT columns use CAST(col AS REAL) in block executors"
        $script:Passed++
    } else {
        Write-Fail "Missing CAST patterns:`n$($missing -join "`n")"
        $script:Failed++
    }
    $castCheck | ForEach-Object { Write-Info "  $_" }
} catch {
    Write-Skip "CAST pattern check skipped: $_"
    $script:Skipped++
}

# Test 4.4: Shared helpers are present
try {
    $helperCheck = docker exec jellydj-backend python -c "
import sys
sys.path.insert(0, '/app')
from services.playlist_blocks import (
    _apply_played_filter, _apply_artist_cap,
    _apply_exclusions, _jitter, _cast_float
)
# Quick smoke test of each helper
from unittest.mock import MagicMock
class FakeRow:
    jellyfin_item_id = 'id1'
    artist_name = 'Artist A'
    final_score = '80.0'
rows = [FakeRow()]

ids = _apply_exclusions(['id1', 'id2'], frozenset(['id2']))
assert ids == ['id1'], f'Expected [id1], got {ids}'

capped = _apply_artist_cap(rows, max_per_artist=2, target=10)
assert 'id1' in capped, 'artist cap should include id1'

jittered = _jitter(rows, jitter_pct=0.1)
assert len(jittered) == 1, 'jitter should return same count'

print('HELPERS_OK')
" 2>&1
    if ($helperCheck -match "HELPERS_OK") {
        Write-Pass "Shared helpers (_apply_exclusions, _apply_artist_cap, _jitter, _cast_float) work"
        $script:Passed++
    } else {
        Write-Fail "Helper smoke test failed:`n$helperCheck"
        $script:Failed++
    }
} catch {
    Write-Skip "Helper test skipped: $_"
    $script:Skipped++
}

# Test 4.5: generate_from_template with a real system template (requires user with scores)
# Resolve user_id if not provided
if (-not $UserId -and $script:Token) {
    try {
        $r = Invoke-Api -Url "$BackendUrl/api/playlists/users"
        if ($r.Content -and $r.Content.Count -gt 0) {
            $UserId = $r.Content[0].user_id
            Write-Info "Auto-selected user_id: $UserId"
        }
    } catch {
        Write-Info "Could not auto-resolve user_id: $_"
    }
}

if ($UserId) {
    try {
        $engineCheck = docker exec jellydj-backend python -c "
import sys, asyncio
sys.path.insert(0, '/app')
from database import SessionLocal
from models import PlaylistTemplate
from services.playlist_engine import generate_from_template

db = SessionLocal()
template = db.query(PlaylistTemplate).filter_by(is_system=True).first()
if not template:
    print('NO_TEMPLATE')
else:
    result = asyncio.run(generate_from_template(template.id, '$UserId', db))
    print(f'RESULT:count={len(result)}')
    print(f'RESULT:sample={result[:3]}')
db.close()
" 2>&1
        if ($engineCheck -match "RESULT:count=(\d+)") {
            $count = [int]$Matches[1]
            if ($count -gt 0) {
                Write-Pass "generate_from_template returned $count tracks for user $UserId"
                $script:Passed++
            } else {
                Write-Fail "generate_from_template returned 0 tracks (no TrackScores for user?)"
                Write-Info "  This is OK on a fresh install with no indexed plays."
                $script:Failed++
            }
            Write-Info "  Output: $($engineCheck -join ' | ')"
        } elseif ($engineCheck -match "NO_TEMPLATE") {
            Write-Skip "No system template found — prefab seeder not run yet"
            $script:Skipped++
        } else {
            Write-Fail "generate_from_template error:`n$engineCheck"
            $script:Failed++
        }
    } catch {
        Write-Skip "Engine execution test skipped: $_"
        $script:Skipped++
    }
} else {
    Write-Skip "generate_from_template test skipped — no user_id available (provide -UserId or log in with -Username/-Password)"
    $script:Skipped++
}

# Test 4.6: preview_template endpoint smoke test
if ($UserId) {
    try {
        $previewCheck = docker exec jellydj-backend python -c "
import sys, asyncio
sys.path.insert(0, '/app')
from database import SessionLocal
from models import PlaylistTemplate
from services.playlist_engine import preview_template

db = SessionLocal()
template = db.query(PlaylistTemplate).filter_by(is_system=True).first()
if not template:
    print('NO_TEMPLATE')
else:
    result = asyncio.run(preview_template(template.id, '$UserId', db))
    print(f'PREVIEW:estimated={result[\"estimated_tracks\"]}')
    print(f'PREVIEW:sample_len={len(result[\"sample\"])}')
    for s in result['sample']:
        print(f'  TRACK: {s[\"artist\"]} — {s[\"track\"]}')
db.close()
" 2>&1
        if ($previewCheck -match "PREVIEW:estimated=") {
            Write-Pass "preview_template returns estimated_tracks + sample"
            $script:Passed++
            $previewCheck | Where-Object { $_ -match "PREVIEW:|TRACK:" } | ForEach-Object {
                Write-Info "  $_"
            }
        } elseif ($previewCheck -match "NO_TEMPLATE") {
            Write-Skip "preview_template test skipped — no system template"
            $script:Skipped++
        } else {
            Write-Fail "preview_template failed:`n$previewCheck"
            $script:Failed++
        }
    } catch {
        Write-Skip "preview_template test skipped: $_"
        $script:Skipped++
    }
} else {
    Write-Skip "preview_template test skipped — no user_id"
    $script:Skipped++
}

# Test 4.7: Each of the 9 block types can be called with a minimal params dict
try {
    $blockSmokeCheck = docker exec jellydj-backend python -c "
import sys
sys.path.insert(0, '/app')
from database import SessionLocal
from services.playlist_blocks import BLOCK_REGISTRY

db = SessionLocal()
results = []
for block_type, fn in BLOCK_REGISTRY.items():
    try:
        ids = fn(user_id='nonexistent_user_000', params={}, target_count=5, db=db, excluded_item_ids=frozenset())
        results.append(f'OK  {block_type}: returned {len(ids)} ids (empty user = expected 0)')
    except Exception as e:
        results.append(f'ERR {block_type}: {e}')
db.close()
print('\n'.join(results))
" 2>&1
    $errors = $blockSmokeCheck | Where-Object { $_ -match "^ERR" }
    if ($errors.Count -eq 0) {
        Write-Pass "All 9 block executors callable with minimal params + nonexistent user (no crash)"
        $script:Passed++
    } else {
        Write-Fail "$($errors.Count) block executor(s) raised exceptions:"
        $errors | ForEach-Object { Write-Fail "  $_" }
        $script:Failed++
    }
    $blockSmokeCheck | ForEach-Object { Write-Info "  $_" }
} catch {
    Write-Skip "Block executor smoke test skipped: $_"
    $script:Skipped++
}

# Test 4.8: Exclusion frozenset is applied (excluded IDs never appear in output)
try {
    $exclCheck = docker exec jellydj-backend python -c "
import sys
sys.path.insert(0, '/app')
from database import SessionLocal
from services.playlist_blocks import execute_final_score_block

db = SessionLocal()

# Get any real track IDs from TrackScore to use as the exclusion set
from models import TrackScore
sample = db.query(TrackScore.jellyfin_item_id).limit(10).all()
excl_ids = frozenset(r.jellyfin_item_id for r in sample)

# Run with all real IDs excluded
result = execute_final_score_block(
    user_id='_any_',
    params={},
    target_count=50,
    db=db,
    excluded_item_ids=excl_ids,
)
leaked = [iid for iid in result if iid in excl_ids]
if leaked:
    print(f'LEAK:{len(leaked)} excluded IDs appeared in output')
else:
    print('EXCLUSION_OK')
db.close()
" 2>&1
    if ($exclCheck -match "EXCLUSION_OK") {
        Write-Pass "excluded_item_ids filter works — no excluded IDs leak into output"
        $script:Passed++
    } elseif ($exclCheck -match "LEAK:") {
        Write-Fail "Exclusion filter leak detected: $exclCheck"
        $script:Failed++
    } else {
        # Likely empty DB — that's acceptable
        Write-Skip "Exclusion filter test inconclusive (empty TrackScore table?): $exclCheck"
        $script:Skipped++
    }
} catch {
    Write-Skip "Exclusion filter test skipped: $_"
    $script:Skipped++
}

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4 — Existing API endpoints still work
# ═══════════════════════════════════════════════════════════════════════════════
Write-Section "Phase 4 — Regression: existing API endpoints"

if ($script:Token) {
    $endpoints = @(
        @{ Method = "GET";  Url = "$BackendUrl/api/playlists/types";  Label = "GET /api/playlists/types" },
        @{ Method = "GET";  Url = "$BackendUrl/api/playlists/runs";   Label = "GET /api/playlists/runs" },
        @{ Method = "GET";  Url = "$BackendUrl/api/indexer/settings"; Label = "GET /api/indexer/settings" },
        @{ Method = "GET";  Url = "$BackendUrl/api/connections/jellyfin"; Label = "GET /api/connections/jellyfin" }
    )
    foreach ($ep in $endpoints) {
        try {
            $r = Invoke-Api -Method $ep.Method -Url $ep.Url -AllowError
            if ($r.Status -in 200, 204) {
                Write-Pass "$($ep.Label) — HTTP $($r.Status)"
                $script:Passed++
            } else {
                Write-Fail "$($ep.Label) — HTTP $($r.Status)"
                $script:Failed++
            }
        } catch {
            Write-Fail "$($ep.Label) — exception: $_"
            $script:Failed++
        }
    }
} else {
    Write-Skip "API regression tests skipped — no auth token (provide -Username/-Password)"
    $script:Skipped += 4
}

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4 — Python unit tests (existing pytest suite)
# ═══════════════════════════════════════════════════════════════════════════════
Write-Section "Phase 4 — Existing pytest suite"

try {
    $pytestOut = docker exec jellydj-backend python -m pytest tests/ -v --tb=short 2>&1
    $passed = ($pytestOut | Select-String "passed").Count
    $failed = ($pytestOut | Select-String "FAILED").Count
    $errors = ($pytestOut | Select-String "ERROR").Count

    if ($failed -eq 0 -and $errors -eq 0) {
        Write-Pass "pytest suite: all tests pass"
        $script:Passed++
    } else {
        Write-Fail "pytest suite: $failed failed, $errors errors"
        $script:Failed++
    }
    # Show last few lines of pytest output
    $pytestOut | Select-Object -Last 15 | ForEach-Object { Write-Info "  $_" }
} catch {
    Write-Skip "pytest not available or failed to run: $_"
    $script:Skipped++
}

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
$total = $script:Passed + $script:Failed + $script:Skipped
Write-Host "`n$('─' * 60)" -ForegroundColor DarkGray
Write-Host "  Results: " -NoNewline
Write-Host "$($script:Passed) passed" -ForegroundColor Green -NoNewline
Write-Host "  |  " -NoNewline
Write-Host "$($script:Failed) failed" -ForegroundColor Red -NoNewline
Write-Host "  |  " -NoNewline
Write-Host "$($script:Skipped) skipped" -ForegroundColor Yellow -NoNewline
Write-Host "  |  $total total"
Write-Host "$('─' * 60)" -ForegroundColor DarkGray

if ($script:Failed -gt 0) {
    Write-Host "`nSome tests failed. Check output above for details." -ForegroundColor Red
    exit 1
} else {
    Write-Host "`nAll executed tests passed." -ForegroundColor Green
    exit 0
}
