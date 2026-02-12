# Tracklistify - Complete Session Changelog

## Overview

Enhanced `tracklistify` for automatic DJ mix track identification with multi-source comparison.

---

## 1. Core Infrastructure

### CLI & Configuration
- Fixed argument parsing for subcommands (`run`, `clean`)
- Added `--progress` flag for detailed status
- Added `--provider` to select primary identification provider
- Added `--no-fallback` to disable fallback providers
- Graceful `Ctrl+C` shutdown with proper async cleanup

### Dependencies
- `audioop-lts` for Python 3.13 compatibility
- `pyacoustid` + `chromaprint` for AcoustID
- `musicbrainzngs` for MusicBrainz verification
- `librosa` for BPM analysis
- `cloudscraper` (attempted, Cloudflare Turnstile blocks it)

---

## 2. Audio Fingerprinting Providers

### Multi-Provider Support
| Provider | Method | Auth |
|----------|--------|------|
| Shazam | `shazamio` library | None |
| ACRCloud | REST API + HMAC-SHA1 | API key required |
| AcoustID | `pyacoustid` + chromaprint | API key required |

### Auto-Disable Failing Providers
- Providers with repeated auth failures auto-disabled
- Logged at INFO level, processing continues

### Edge Cases
- AcoustID rarely matches DJ mix segments (low recall expected)
- ACRCloud requires correct HMAC signature generation
- Provider rate limits respected via config

---

## 3. External Tracklist Sources

### set79.com
- URL-based lookup: `set79.com/tracklist/{encoded-url}`
- Parses `tr.track-row` elements
- Extracts: position, artist, title, timestamp, label
- Format: "Title - Artist" (reversed, handled)

### 1001tracklists.com
**Problem:** Cloudflare Turnstile blocks automated requests.

**Solution:** Local HTML file support.
```bash
uv run tracklistify --1001tracklists "saved-page.html" <url>
```

**Parsing:**
- Schema.org meta tags (`itemprop="name"`, `itemprop="byArtist"`)
- Hidden input `*_cue_seconds` for timestamps
- Fallback to `.tlpItem`, `.trackLabel` selectors

**Edge Cases:**
- HTTP 206 (Partial Content) accepted
- Tracks with `00:00:00` = cue time not set on source
- `data-trno` attribute for position (0-indexed)

---

## 4. Track Merging & Comparison

### Merge Logic
- 90-second time window for matching
- Fuzzy string matching for artist/title
- Confidence scoring from audio providers
- Multi-provider consensus increases confidence

### Track Model Extensions
```python
@dataclass
class Track:
    sources: List[str]      # ["own", "shazam", "set79", "1001tracklists"]
    external_only: bool     # True if only from external sources
    unconfirmed: bool       # True if only own identification
    verified_by_db: bool    # MusicBrainz verification
    bpm: Optional[float]    # Analyzed BPM
```

### Three-Tier Output
1. **Confirmed** - Own identification + external confirmation
2. **External Only** - Found in set79/1001tracklists, not by audio fingerprinting
3. **Unconfirmed** - Only audio fingerprinting, no external confirmation

---

## 5. Output Enhancements

### Consolidated Tracklist (NEW)
Best-guess tracklist at top of Markdown, merging all sources.

**Priority:** set79 → 1001tracklists → Shazam → ACRCloud

```markdown
## Konsolidierte Tracklist
1. **00:00:00** - Artist - Title [1001tracklists]
2. **00:03:20** - Artist - Title [set79, shazam] _(100%, 123 BPM)_
```

### Detailed Comparison Table
Side-by-side view of all sources per time slot.

### Metrics Table
Precision, Recall, F1-Score per source vs reference (1001tracklists or set79).

---

## 6. Caching

### URL-Based Download Cache
- Prevents re-downloading same audio
- Cache key: sanitized URL
- Location: `cache/downloads/`

### Identification Cache
- Caches fingerprinting results per segment
- TTL configurable via `CACHE_TTL`

---

## 7. Cleanup System

### Commands
```bash
# Interactive selection
uv run tracklistify clean -i

# Specific targets
uv run tracklistify clean --cache
uv run tracklistify clean --output
uv run tracklistify clean --logs
uv run tracklistify clean --segments

# All at once
uv run tracklistify clean --all

# Preview without deleting
uv run tracklistify clean --dry-run
```

### Interactive Mode
```
1. [ ] Cache (downloads & identifications)   203.1 MB
2. [-] Output (empty)
3. [-] Logs (empty)
4. [-] Segments (empty)
5. [ ] Alle   203.1 MB

Enter: 1-5 toggle, 'c' confirm, 'q' quit
```

### Edge Cases
- Empty directories: `[-]`, not selectable
- `Ctrl+C` = safe cancel
- `--force` skips confirmation

---

## 8. Audio Segments

### New Default Behavior
- **Segments kept after analysis** (was: deleted)
- Enables re-analysis, debugging, manual inspection

### Options
```bash
# Delete immediately (old behavior)
uv run tracklistify --delete-segments <url>

# Clean later
uv run tracklistify clean --segments
```

---

## 9. Files Changed

| File | Key Changes |
|------|-------------|
| `cli.py` | Subcommands, `--1001tracklists`, `--delete-segments`, `clean -i` |
| `config/base.py` | `keep_segments=True`, provider settings |
| `core/base.py` | Conditional cleanup, segment retention |
| `core/track.py` | Extended Track model with sources, BPM |
| `providers/shazam.py` | Shazam integration |
| `providers/acrcloud.py` | ACRCloud REST API + HMAC |
| `providers/acoustid.py` | AcoustID + chromaprint |
| `utils/identification.py` | Multi-provider orchestration, auto-disable |
| `utils/external_sources.py` | set79, 1001tracklists fetchers, HTML parsing |
| `utils/tracklist_merger.py` | Merge logic, fuzzy matching |
| `utils/track_comparison.py` | Comparison tables, metrics, consolidated list |
| `utils/cleanup.py` | CleanupManager, interactive mode |
| `utils/bpm.py` | BPM analysis with librosa |
| `utils/verification.py` | MusicBrainz verification |
| `exporters/tracklist.py` | Enhanced Markdown/JSON with all sections |

---

## 10. Known Limitations

| Issue | Workaround |
|-------|------------|
| 1001tracklists Cloudflare | Save page as HTML, use `--1001tracklists file.html` |
| AcoustID low recall on mixes | Expected behavior, DJ mixes rarely match |
| ACRCloud auth failures | Check API key, host, signature in `.env` |
| set79 not found | Only works if mix URL exists in their database |
