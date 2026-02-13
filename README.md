![Tracklistify banner](docs/assets/banner.png)

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](docs/CONTRIBUTING.md)

### [Changelog](docs/CHANGELOG.md) · [Issues](https://github.com/marcopeise/tracklistify-extended/issues) · [Contributing](docs/CONTRIBUTING.md)

</div>

# Tracklistify Extended

> Extended fork of [betmoar/tracklistify](https://github.com/betmoar/tracklistify) with multi-source comparison, consolidated tracklists, and enhanced deduplication.

A powerful automatic tracklist generator for DJ mixes. Identifies tracks using multiple audio fingerprinting providers and cross-references with external tracklist sources for maximum accuracy.

## What's New in Extended

- **Multi-Source Comparison** - Cross-reference with set79.com and 1001tracklists.com
- **Consolidated Tracklist** - Best-guess tracklist merging all sources with deduplication
- **Side-by-Side Comparison** - See what each source detected at each timestamp
- **Precision/Recall Metrics** - Evaluate detection accuracy against reference sources
- **BPM Analysis** - Automatic BPM detection via librosa
- **MusicBrainz Verification** - Verify track metadata against MusicBrainz database
- **Interactive Cleanup** - Manage cache, segments, and output files

## Key Features

### Multi-Provider Track Identification

| Provider | Method | Auth Required |
|----------|--------|---------------|
| Shazam | shazamio library | No |
| ACRCloud | REST API + HMAC-SHA1 | Yes |
| AcoustID | pyacoustid + chromaprint | Yes |

- Smart provider fallback system
- Auto-disable failing providers
- Confidence scoring per track

### External Source Integration

| Source | Method |
|--------|--------|
| set79.com | Automatic URL lookup |
| 1001tracklists.com | Local HTML file (Cloudflare protected) |

When a 1001tracklists page has no cue times set, tracks are matched by artist/title instead of timestamp.

### Output Formats

- Markdown with consolidated tracklist and comparison tables
- JSON with detailed metadata
- M3U playlists
- CSV and XML exports
- Rekordbox compatible format

## Requirements

- Python 3.11 or higher
- ffmpeg
- uv (package manager) - [Installation guide](https://docs.astral.sh/uv/getting-started/installation/)

## Quick Start

### 1. Installation

```bash
git clone https://github.com/marcopeise/tracklistify-extended.git
cd tracklistify-extended
uv sync
```

### 2. Configuration

```bash
cp .env.example .env
# Edit .env with your API keys (ACRCloud, AcoustID)
```

### 3. Basic Usage

```bash
# Identify tracks from YouTube
uv run tracklistify "https://youtube.com/watch?v=example"

# Identify from local file
uv run tracklistify path/to/mix.mp3
```

## Advanced Usage

### External Sources

```bash
# With 1001tracklists (save page as HTML first due to Cloudflare)
uv run tracklistify --1001tracklists "saved-page.html" "https://youtube.com/watch?v=example"

# set79 is checked automatically if the URL exists in their database
```

### Provider Selection

```bash
# Use specific provider
uv run tracklistify --provider shazam input.mp3

# Disable fallback to other providers
uv run tracklistify --provider shazam --no-fallback input.mp3
```

### Segment Handling

Segments are stored per input (isolated by URL/file hash) so consecutive runs on different mixes never interfere with each other.

```bash
# Keep segments after analysis (default)
uv run tracklistify input.mp3

# Delete segments immediately after analysis
uv run tracklistify --delete-segments input.mp3
```

### Output Formats

```bash
uv run tracklistify -f json input.mp3      # JSON output
uv run tracklistify -f markdown input.mp3  # Markdown output
uv run tracklistify -f m3u input.mp3       # M3U playlist
uv run tracklistify -f csv input.mp3       # CSV export
uv run tracklistify -f all input.mp3       # All formats
```

### Cleanup Command

Running `clean` without flags opens an interactive menu to select what to delete.

```bash
# Interactive (default when no flags given)
uv run tracklistify clean

# Clean specific targets
uv run tracklistify clean --cache      # Download and identification cache
uv run tracklistify clean --segments   # Audio segments
uv run tracklistify clean --output     # Output files
uv run tracklistify clean --logs       # Log files
uv run tracklistify clean --all        # Everything

# Preview without deleting
uv run tracklistify clean --dry-run --all

# Skip confirmation
uv run tracklistify clean --force --cache
```

## Output Example

The Markdown output includes:

### Consolidated Tracklist

Best-guess tracklist with source attribution and deduplication:

```
1. **00:00:00** - Artist - Track Title [set79, 1001tracklists]
2. **00:03:20** - Artist - Track Title [shazam, acrcloud] _(100%, 123 BPM)_
```

### Comparison Table

Side-by-side view of what each source detected:

| # | Time | Shazam | ACRCloud | set79 | 1001tracklists |
|---|------|--------|----------|-------|----------------|
| 1 | 00:00:00 | -- | -- | Track A | Track A |
| 2 | 00:03:20 | Track B | Track B | -- | -- |

### Metrics

Precision, Recall, and F1-Score per source against a reference.

## Known Limitations

| Issue | Workaround |
|-------|------------|
| 1001tracklists Cloudflare | Save page as HTML, use `--1001tracklists file.html` |
| 1001tracklists no cue times | Handled automatically via name-based matching |
| AcoustID low recall | Expected for DJ mixes - fingerprints rarely match |
| set79 not found | Only works if mix URL exists in their database |

## Contributing

Contributions are welcome! Please read our [Contributing Guide](docs/CONTRIBUTING.md) for details.

## License

MIT License - see [LICENSE](LICENSE) for details.

Original work by [betmoar](https://github.com/betmoar/tracklistify).
