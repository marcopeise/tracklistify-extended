"""
Track comparison utilities for creating side-by-side source comparison tables.

This module aligns tracks from multiple sources (Shazam, ACRCloud, set79, 1001tracklists)
by time position and creates comparison views with metrics.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from tracklistify.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SourceTrack:
    """A track from a specific source."""

    artist: str
    title: str
    time_in_mix: str  # HH:MM:SS
    source: str  # "shazam", "acrcloud", "set79", "1001tracklists"
    confidence: Optional[float] = None
    bpm: Optional[float] = None

    def time_to_seconds(self) -> int:
        """Convert time_in_mix to seconds."""
        try:
            parts = self.time_in_mix.split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return 0
        except (ValueError, IndexError):
            return 0

    def short_name(
        self,
        max_len: int = 40,
        show_confidence: bool = True,
        show_bpm: bool = True,
    ) -> str:
        """Get a shortened display name with optional confidence and BPM.

        Args:
            max_len: Maximum length for the name
            show_confidence: Whether to append confidence percentage
            show_bpm: Whether to append BPM
        """
        name = f"{self.artist} - {self.title}"
        if len(name) > max_len:
            name = name[: max_len - 3] + "..."

        # Add metadata in parentheses
        meta = []
        if show_confidence and self.confidence is not None:
            meta.append(f"{self.confidence:.0f}%")
        if show_bpm and self.bpm is not None:
            meta.append(f"{self.bpm:.0f} BPM")

        if meta:
            name += f" ({', '.join(meta)})"

        return name

    def __str__(self) -> str:
        return f"{self.artist} - {self.title}"


@dataclass
class ComparisonRow:
    """A row in the comparison table representing a time slot."""

    position: int  # Row number
    time_in_mix: str  # Representative time for this slot
    time_seconds: int
    tracks: Dict[str, Optional[SourceTrack]] = field(default_factory=dict)

    def get_track(self, source: str) -> Optional[SourceTrack]:
        """Get the track from a specific source."""
        return self.tracks.get(source)

    def has_any_track(self) -> bool:
        """Check if any source has a track for this slot."""
        return any(t is not None for t in self.tracks.values())

    def sources_with_tracks(self) -> List[str]:
        """Get list of sources that have tracks in this slot."""
        return [s for s, t in self.tracks.items() if t is not None]


@dataclass
class ComparisonMetrics:
    """Metrics for a source compared to a reference."""

    source: str
    total_tracks: int
    correct: int  # Matches reference
    false_positives: int  # Not in reference
    missed: int  # In reference but not in source
    precision: float
    recall: float
    f1_score: float


class TrackComparison:
    """Creates side-by-side comparison of tracks from multiple sources."""

    # Sources in display order
    SOURCES = ["shazam", "acrcloud", "set79", "1001tracklists"]

    # Time window for grouping tracks (seconds)
    TIME_WINDOW = 90

    def __init__(self, time_window: int = 90):
        """Initialize the comparison.

        Args:
            time_window: Time window in seconds for grouping tracks
        """
        self.time_window = time_window
        self._source_tracks: Dict[str, List[SourceTrack]] = {
            s: [] for s in self.SOURCES
        }
        self._rows: List[ComparisonRow] = []
        self._built = False

    def add_tracks(self, source: str, tracks: List[SourceTrack]) -> None:
        """Add tracks from a source.

        Args:
            source: Source name (shazam, acrcloud, set79, 1001tracklists)
            tracks: List of tracks from that source
        """
        source_lower = source.lower()
        if source_lower not in self._source_tracks:
            logger.warning(f"Unknown source: {source}")
            return

        self._source_tracks[source_lower].extend(tracks)
        self._built = False

    def add_own_tracks(
        self,
        tracks: list,
        shazam_indices: Optional[Set[int]] = None,
        acrcloud_indices: Optional[Set[int]] = None,
    ) -> None:
        """Add tracks from own identification, separated by provider.

        Args:
            tracks: List of Track objects from own identification
            shazam_indices: Indices of tracks identified by Shazam
            acrcloud_indices: Indices of tracks identified by ACRCloud
        """
        for i, track in enumerate(tracks):
            providers = getattr(track, "providers", [])

            # Create SourceTrack for each provider that identified this track
            track_bpm = getattr(track, "bpm", None)

            if "shazam" in [p.lower() for p in providers]:
                self._source_tracks["shazam"].append(
                    SourceTrack(
                        artist=track.artist,
                        title=track.song_name,
                        time_in_mix=track.time_in_mix,
                        source="shazam",
                        confidence=track.confidence,
                        bpm=track_bpm,
                    )
                )

            if "acrcloud" in [p.lower() for p in providers]:
                self._source_tracks["acrcloud"].append(
                    SourceTrack(
                        artist=track.artist,
                        title=track.song_name,
                        time_in_mix=track.time_in_mix,
                        source="acrcloud",
                        confidence=track.confidence,
                        bpm=track_bpm,
                    )
                )

        self._built = False

    def add_external_tracks(
        self, source: str, external_tracks: list
    ) -> None:
        """Add tracks from external sources (set79, 1001tracklists).

        Args:
            source: Source name
            external_tracks: List of ExternalTrack objects
        """
        source_lower = source.lower()
        for ext in external_tracks:
            self._source_tracks[source_lower].append(
                SourceTrack(
                    artist=ext.artist,
                    title=ext.title,
                    time_in_mix=ext.time_in_mix,
                    source=source_lower,
                    confidence=None,
                )
            )
        self._built = False

    def build(self) -> List[ComparisonRow]:
        """Build the comparison table by aligning tracks from all sources.

        Returns:
            List of ComparisonRow objects
        """
        if self._built:
            return self._rows

        # Collect all unique time slots
        all_times: List[Tuple[int, str, str]] = []  # (seconds, time_str, source)

        for source, tracks in self._source_tracks.items():
            for track in tracks:
                seconds = track.time_to_seconds()
                all_times.append((seconds, track.time_in_mix, source))

        if not all_times:
            self._rows = []
            self._built = True
            return self._rows

        # Sort by time
        all_times.sort(key=lambda x: x[0])

        # Group into time slots
        slots: List[Tuple[int, str]] = []  # (representative_seconds, representative_time)
        used_times: Set[int] = set()

        for seconds, time_str, _ in all_times:
            # Check if this time belongs to an existing slot
            found_slot = False
            for slot_seconds, _ in slots:
                if abs(seconds - slot_seconds) <= self.time_window:
                    found_slot = True
                    break

            if not found_slot:
                slots.append((seconds, time_str))

        # Sort slots
        slots.sort(key=lambda x: x[0])

        # Build comparison rows
        self._rows = []
        for i, (slot_seconds, slot_time) in enumerate(slots, 1):
            row = ComparisonRow(
                position=i,
                time_in_mix=slot_time,
                time_seconds=slot_seconds,
                tracks={s: None for s in self.SOURCES},
            )

            # Find tracks from each source that belong to this slot
            for source in self.SOURCES:
                best_track = None
                best_distance = float("inf")

                for track in self._source_tracks[source]:
                    distance = abs(track.time_to_seconds() - slot_seconds)
                    if distance <= self.time_window and distance < best_distance:
                        best_track = track
                        best_distance = distance

                row.tracks[source] = best_track

            if row.has_any_track():
                self._rows.append(row)

        self._built = True
        return self._rows

    def get_rows(self) -> List[ComparisonRow]:
        """Get the comparison rows."""
        if not self._built:
            self.build()
        return self._rows

    def calculate_metrics(
        self, reference_source: str = "1001tracklists"
    ) -> Dict[str, ComparisonMetrics]:
        """Calculate precision, recall, F1 for each source against a reference.

        Args:
            reference_source: The source to use as ground truth

        Returns:
            Dict mapping source name to ComparisonMetrics
        """
        if not self._built:
            self.build()

        metrics = {}
        ref_source = reference_source.lower()

        for source in self.SOURCES:
            if source == ref_source:
                continue

            correct = 0
            false_positives = 0
            missed = 0
            total = 0

            for row in self._rows:
                ref_track = row.tracks.get(ref_source)
                src_track = row.tracks.get(source)

                if ref_track is not None:
                    if src_track is not None:
                        # Both have a track - check if they match
                        if self._tracks_match(src_track, ref_track):
                            correct += 1
                        else:
                            # Source has different track
                            false_positives += 1
                            missed += 1
                    else:
                        # Reference has track, source doesn't
                        missed += 1
                elif src_track is not None:
                    # Source has track, reference doesn't
                    false_positives += 1

                if src_track is not None:
                    total += 1

            # Calculate metrics
            precision = correct / (correct + false_positives) if (correct + false_positives) > 0 else 0
            ref_total = sum(1 for r in self._rows if r.tracks.get(ref_source) is not None)
            recall = correct / ref_total if ref_total > 0 else 0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

            metrics[source] = ComparisonMetrics(
                source=source,
                total_tracks=total,
                correct=correct,
                false_positives=false_positives,
                missed=missed,
                precision=precision,
                recall=recall,
                f1_score=f1,
            )

        return metrics

    def _tracks_match(self, track1: SourceTrack, track2: SourceTrack) -> bool:
        """Check if two tracks are the same (fuzzy match).

        Args:
            track1: First track
            track2: Second track

        Returns:
            True if tracks are considered the same
        """

        def normalize(s: str) -> str:
            s = s.lower()
            s = re.sub(r"[^\w\s]", "", s)
            s = re.sub(r"\s+", " ", s).strip()
            return s

        def base_title(s: str) -> str:
            s = re.sub(
                r"\s*\(.*?(remix|edit|mix|version|dub|remaster|extended).*?\)\s*$",
                "",
                s,
                flags=re.IGNORECASE,
            )
            s = re.sub(
                r"\s*\[.*?(remix|edit|mix|version|dub|remaster|extended).*?\]\s*$",
                "",
                s,
                flags=re.IGNORECASE,
            )
            return normalize(s)

        # Compare artists
        a1 = normalize(track1.artist)
        a2 = normalize(track2.artist)

        # Compare titles
        t1 = normalize(track1.title)
        t2 = normalize(track2.title)

        # Exact match
        if a1 == a2 and t1 == t2:
            return True

        # Base title match (ignore remix info)
        if base_title(track1.title) == base_title(track2.title):
            # Check if artists are similar
            if a1 == a2 or a1 in a2 or a2 in a1:
                return True

        # Title contains match
        if t1 in t2 or t2 in t1:
            if a1 == a2 or a1 in a2 or a2 in a1:
                return True

        return False

    def to_markdown_table(self, max_cell_width: int = 32) -> str:
        """Generate a Markdown table of the comparison with aligned columns.

        Args:
            max_cell_width: Maximum width for track name cells

        Returns:
            Markdown table string
        """
        if not self._built:
            self.build()

        if not self._rows:
            return "_Keine Tracks zum Vergleichen._"

        # Define column widths
        col_widths = {
            "#": 3,
            "Zeit": 8,
            "shazam": max_cell_width,
            "acrcloud": max_cell_width,
            "set79": max_cell_width,
            "1001tracklists": max_cell_width,
        }

        def pad(text: str, width: int, align: str = "left") -> str:
            """Pad text to fixed width."""
            text = text[:width]  # Truncate if too long
            if align == "right":
                return text.rjust(width)
            elif align == "center":
                return text.center(width)
            else:
                return text.ljust(width)

        # Build header
        lines = []
        header_cells = [
            pad("#", col_widths["#"], "right"),
            pad("Zeit", col_widths["Zeit"]),
            pad("Shazam", col_widths["shazam"]),
            pad("ACRCloud", col_widths["acrcloud"]),
            pad("set79", col_widths["set79"]),
            pad("1001tracklists", col_widths["1001tracklists"]),
        ]
        lines.append("| " + " | ".join(header_cells) + " |")

        # Separator line with proper alignment
        sep_cells = [
            "-" * col_widths["#"] + ":",
            ":" + "-" * (col_widths["Zeit"] - 1),
            ":" + "-" * (col_widths["shazam"] - 1),
            ":" + "-" * (col_widths["acrcloud"] - 1),
            ":" + "-" * (col_widths["set79"] - 1),
            ":" + "-" * (col_widths["1001tracklists"] - 1),
        ]
        lines.append("|" + "|".join(sep_cells) + "|")

        # Data rows
        for row in self._rows:
            cells = [
                pad(str(row.position), col_widths["#"], "right"),
                pad(row.time_in_mix, col_widths["Zeit"]),
            ]

            for source in self.SOURCES:
                track = row.tracks.get(source)
                width = col_widths[source]

                if track:
                    # Show confidence and BPM for own sources (shazam, acrcloud)
                    is_own = source in ("shazam", "acrcloud")
                    name = track.short_name(
                        width - 2,  # Leave room for padding
                        show_confidence=is_own,
                        show_bpm=is_own,
                    )
                    cells.append(pad(name, width))
                else:
                    cells.append(pad("--", width))

            lines.append("| " + " | ".join(cells) + " |")

        return "\n".join(lines)

    def to_markdown_metrics(
        self, reference_source: str = "1001tracklists"
    ) -> str:
        """Generate a Markdown table of metrics with aligned columns.

        Args:
            reference_source: The source to use as ground truth

        Returns:
            Markdown table string
        """
        metrics = self.calculate_metrics(reference_source)

        if not metrics:
            return "_Keine Metriken verfügbar._"

        # Column widths
        w_metric = 18
        w_val = 10

        def pad(text: str, width: int, align: str = "left") -> str:
            text = str(text)[:width]
            if align == "right":
                return text.rjust(width)
            return text.ljust(width)

        def val(m: Optional[ComparisonMetrics], attr: str) -> str:
            if m is None:
                return "--"
            v = getattr(m, attr, None)
            if v is None:
                return "--"
            if isinstance(v, float):
                if attr in ("precision", "recall"):
                    return f"{v * 100:.0f}%"
                elif attr == "f1_score":
                    return f"{v:.2f}"
            return str(v)

        # Get metrics for each source
        shazam = metrics.get("shazam")
        acrcloud = metrics.get("acrcloud")
        set79 = metrics.get("set79")

        lines = []
        lines.append(f"_Referenz: {reference_source}_\n")

        # Header
        lines.append(
            "| " + pad("Metrik", w_metric) +
            " | " + pad("Shazam", w_val, "right") +
            " | " + pad("ACRCloud", w_val, "right") +
            " | " + pad("set79", w_val, "right") + " |"
        )

        # Separator
        lines.append(
            "|:" + "-" * (w_metric - 1) +
            "|" + "-" * (w_val - 1) + ":" +
            "|" + "-" * (w_val - 1) + ":" +
            "|" + "-" * (w_val - 1) + ":|"
        )

        # Data rows
        rows_data = [
            ("Erkannte Tracks", "total_tracks"),
            ("Korrekt", "correct"),
            ("Falsch-Positive", "false_positives"),
            ("Nicht erkannt", "missed"),
            ("Precision", "precision"),
            ("Recall", "recall"),
            ("F1-Score", "f1_score"),
        ]

        for label, attr in rows_data:
            lines.append(
                "| " + pad(label, w_metric) +
                " | " + pad(val(shazam, attr), w_val, "right") +
                " | " + pad(val(acrcloud, attr), w_val, "right") +
                " | " + pad(val(set79, attr), w_val, "right") + " |"
            )

        return "\n".join(lines)

    def to_consolidated_tracklist(
        self,
        priority: Optional[List[str]] = None,
    ) -> str:
        """Build a consolidated tracklist merging all sources with priority.

        Creates a single "best guess" tracklist by selecting the best track
        for each time slot based on source priority. Consecutive duplicate
        tracks (same artist/title) are merged into a single entry.

        Args:
            priority: List of sources in priority order (highest first).
                      Defaults to ["set79", "1001tracklists", "shazam", "acrcloud"]

        Returns:
            Markdown formatted tracklist
        """
        if not self._built:
            self.build()

        if priority is None:
            # External sources are more reliable than audio fingerprinting
            priority = ["set79", "1001tracklists", "shazam", "acrcloud"]

        # Collect entries with deduplication
        # Each entry: (time_in_mix, track, accumulated_sources, best_confidence, best_bpm)
        entries: List[Tuple[str, SourceTrack, Set[str], Optional[float], Optional[float]]] = []

        for row in self._rows:
            # Find best track according to priority
            best_track: Optional[SourceTrack] = None

            for source in priority:
                track = row.get_track(source)
                if track:
                    best_track = track
                    break

            if best_track:
                # Get all confirming sources for this row
                confirming = set(row.sources_with_tracks())

                # Check if this is a duplicate of the previous entry
                if entries:
                    prev_time, prev_track, prev_sources, prev_conf, prev_bpm = entries[-1]
                    if self._tracks_match(best_track, prev_track):
                        # Merge sources into previous entry
                        merged_sources = prev_sources | confirming
                        # Keep best confidence and BPM (prefer non-None, then higher)
                        merged_conf = best_track.confidence if best_track.confidence is not None else prev_conf
                        if prev_conf is not None and best_track.confidence is not None:
                            merged_conf = max(prev_conf, best_track.confidence)
                        merged_bpm = best_track.bpm if best_track.bpm is not None else prev_bpm
                        # Update previous entry
                        entries[-1] = (prev_time, prev_track, merged_sources, merged_conf, merged_bpm)
                        continue

                # New unique track
                entries.append((
                    row.time_in_mix,
                    best_track,
                    confirming,
                    best_track.confidence,
                    best_track.bpm,
                ))

        # Format output lines
        lines = []
        for track_num, (time_in_mix, track, sources, confidence, bpm) in enumerate(entries, 1):
            # Sort sources by priority
            sources_sorted = sorted(
                sources,
                key=lambda s: priority.index(s) if s in priority else 999
            )

            # Format confidence and BPM
            meta_parts = []
            if confidence is not None:
                meta_parts.append(f"{confidence:.0f}%")
            if bpm is not None:
                meta_parts.append(f"{bpm:.0f} BPM")
            meta_str = f" _({', '.join(meta_parts)})_" if meta_parts else ""

            # Format sources
            source_str = f" [{', '.join(sources_sorted)}]"

            lines.append(
                f"{track_num}. **{time_in_mix}** - "
                f"{track.artist} - {track.title}"
                f"{source_str}{meta_str}"
            )

        if not lines:
            return "_Keine Tracks gefunden._"

        return "\n".join(lines)
