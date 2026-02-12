"""
Output formatting and file handling for Tracklistify.
"""

# Standard library imports
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Local/package imports
from tracklistify.config import get_config
from tracklistify.core.exceptions import ExportError
from tracklistify.core.track import Track
from tracklistify.utils.logger import get_logger
from tracklistify.utils.track_comparison import TrackComparison

logger = get_logger(__name__)


class TracklistOutput:
    """Handles tracklist output in various formats."""

    def __init__(self, mix_info: dict, tracks: List[Track], merger=None):
        """
        Initialize with mix information and tracks.

        Args:
            mix_info: Dictionary containing mix metadata
            tracks: List of identified tracks
            merger: Optional TracklistMerger with raw data for comparison table

        Raises:
            ExportError: If tracks is None or empty
        """
        if not tracks:
            raise ExportError("No tracks provided for output")

        self.mix_info = mix_info or {}
        self.tracks = tracks
        self.merger = merger
        self._config = get_config()
        self.output_dir = Path(self._config.output_dir)
        self.output_dir.mkdir(exist_ok=True)

    def _format_filename(self, extension: str) -> str:
        """
        Generate filename in format: [YYYYMMDD] Artist - Description.extension

        Args:
            extension: File extension without dot

        Returns:
            Formatted filename
        """
        # Get date in YYYYMMDD format
        mix_date = self.mix_info.get("date", datetime.now().strftime("%Y-%m-%d"))
        if isinstance(mix_date, str):
            try:
                mix_date = datetime.strptime(mix_date, "%Y-%m-%d").strftime("%Y%m%d")
            except ValueError:
                mix_date = datetime.now().strftime("%Y%m%d")

        # Get artist and description
        artist = self.mix_info.get("artist", "")
        title = self.mix_info.get("title", "")
        venue = self.mix_info.get("venue", "")

        # Clean up special characters but preserve spaces and basic punctuation
        def clean_string(s: str) -> str:
            # Replace invalid filename characters with spaces
            s = re.sub(r'[<>:"/\\|?*@]', " ", s)
            # Replace multiple spaces with single space
            s = re.sub(r"\s+", " ", s)
            # Strip leading/trailing spaces
            return s.strip()

        # Clean and format parts
        artist = clean_string(artist)
        title = clean_string(title)
        venue = clean_string(venue)

        # Use artist from title if no artist provided
        if not artist and " - " in title:
            artist, title = title.split(" - ", 1)
        elif not artist:
            artist = "Unknown Artist"

        # Format description with venue
        description = title
        if venue:
            description = f"{title} | {venue}"

        # Format filename
        return f"[{mix_date}] {artist} - {description}.{extension}"

    def save(self, format_type: str) -> Optional[Path]:
        """
        Save tracks in specified format.

        Args:
            format_type: Output format ('json', 'markdown', or 'm3u')

        Returns:
            Path to saved file, or None if format is invalid
        """
        if format_type == "json":
            return self._save_json()
        elif format_type == "markdown":
            return self._save_markdown()
        elif format_type == "m3u":
            return self._save_m3u()
        else:
            logger.error(f"Invalid format type: {format_type}")
            return None

    def _save_json(self) -> Path:
        """Save tracks as JSON file with verification metadata."""
        output_file = self.output_dir / self._format_filename("json")

        # Ensure tracks is not None and is a list
        if not isinstance(self.tracks, list):
            logger.error("No valid tracks list available")
            raise ExportError("Cannot save JSON: tracks is not a valid list")

        # Calculate statistics safely with null checks
        track_count = len(self.tracks)
        avg_confidence = 0
        min_confidence = 0
        max_confidence = 0
        verified_count = 0
        unverified_count = 0

        if track_count > 0:
            confidences = [
                t.confidence for t in self.tracks if hasattr(t, "confidence")
            ]
            if confidences:
                avg_confidence = sum(confidences) / len(confidences)
                min_confidence = min(confidences)
                max_confidence = max(confidences)

            verified_count = sum(
                1 for t in self.tracks if t.confidence >= 80
            )
            unverified_count = track_count - verified_count

        # Count tracks by source type
        external_only_count = sum(
            1 for t in self.tracks if getattr(t, "external_only", False)
        )
        unconfirmed_count = sum(
            1 for t in self.tracks if getattr(t, "unconfirmed", False)
        )
        confirmed_count = track_count - external_only_count - unconfirmed_count

        data = {
            "mix_info": self.mix_info or {},
            "track_count": track_count,
            "analysis_info": {
                "timestamp": datetime.now().isoformat(),
                "track_count": track_count,
                "verified_count": verified_count,
                "unverified_count": unverified_count,
                "confirmed_by_external": confirmed_count,
                "external_only": external_only_count,
                "unconfirmed": unconfirmed_count,
                "average_confidence": avg_confidence,
                "min_confidence": min_confidence,
                "max_confidence": max_confidence,
            },
            "tracks": [
                {
                    "song_name": track.song_name,
                    "artist": track.artist,
                    "time_in_mix": track.time_in_mix,
                    "confidence": track.confidence,
                    "verified": track.confidence >= 80,
                    "providers": getattr(track, "providers", []),
                    "sources": getattr(track, "sources", []),
                    "external_only": getattr(track, "external_only", False),
                    "unconfirmed": getattr(track, "unconfirmed", False),
                    "bpm": getattr(track, "bpm", None),
                    "bpm_outlier": getattr(track, "bpm_outlier", False),
                    "verified_by_db": getattr(track, "verified_by_db", False),
                    "duration": getattr(track, "duration", None),
                }
                for track in self.tracks
            ],
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        logger.info("Analysis Summary:")
        logger.info(f"- Total tracks: {track_count}")
        logger.info(f"- Verified (>=80%): {verified_count}")
        logger.info(f"- Unverified (<80%): {unverified_count}")
        logger.info(
            f"- Average confidence: {avg_confidence:.1f}%"
        )
        logger.info(
            f"- Confidence range: {min_confidence:.1f}%"
            f" - {max_confidence:.1f}%"
        )
        logger.info(f"Saved JSON tracklist to: {output_file}")
        return output_file

    def _save_markdown(self) -> Path:
        """Save tracks as Markdown file with three-tier output based on sources."""
        output_file = self.output_dir / self._format_filename("md")

        # Split tracks into three categories based on source confirmation
        # 1. Confirmed: own identification + external sources
        # 2. External only: only found in external sources (not by our identification)
        # 3. Unconfirmed: only our own identification (no external confirmation)
        confirmed = [
            t for t in self.tracks
            if not getattr(t, "external_only", False)
            and not getattr(t, "unconfirmed", False)
        ]
        external_only = [
            t for t in self.tracks
            if getattr(t, "external_only", False)
        ]
        unconfirmed = [
            t for t in self.tracks
            if getattr(t, "unconfirmed", False)
        ]

        def format_sources(track) -> str:
            """Format source annotations for a track."""
            sources = getattr(track, "sources", [])
            if sources:
                return f" [{' + '.join(sources)}]"
            return ""

        with open(output_file, "w", encoding="utf-8") as f:
            # Write header
            f.write(f"# {self.mix_info.get('title', 'Unknown Mix')}\n\n")

            if self.mix_info.get("artist"):
                f.write(f"**Artist:** {self.mix_info['artist']}\n")
            if self.mix_info.get("date"):
                f.write(f"**Date:** {self.mix_info['date']}\n")

            # ─────────────────────────────────────────────────────────────────
            # Consolidated Tracklist (Best-Of from all sources)
            # ─────────────────────────────────────────────────────────────────
            if self.merger:
                try:
                    consolidated_md = self._build_consolidated_tracklist()
                    if consolidated_md:
                        f.write("\n## Konsolidierte Tracklist\n\n")
                        f.write("_Beste Schätzung aus allen Quellen (Priorität: set79 → 1001tracklists → Shazam → ACRCloud)_\n\n")
                        f.write(consolidated_md)
                        f.write("\n")
                except Exception as e:
                    logger.warning(f"Could not build consolidated tracklist: {e}")

            # ─────────────────────────────────────────────────────────────────
            # Quellen-Vergleich (Three-Source Comparison Table)
            # ─────────────────────────────────────────────────────────────────
            total = len(self.tracks)
            n_confirmed = len(confirmed)
            n_external = len(external_only)
            n_unconfirmed = len(unconfirmed)

            # Calculate percentages
            def pct(n: int) -> str:
                return f"{n / total * 100:.0f}%" if total > 0 else "0%"

            # Aligned summary table
            w_cat = 30
            w_num = 8
            w_pct = 8

            def pad(text: str, width: int, align: str = "left") -> str:
                text = str(text)[:width]
                if align == "right":
                    return text.rjust(width)
                return text.ljust(width)

            f.write("\n## Quellen-Vergleich\n\n")
            f.write(f"| {pad('Kategorie', w_cat)} | {pad('Anzahl', w_num, 'right')} | {pad('Anteil', w_pct, 'right')} |\n")
            f.write(f"|:{'-' * (w_cat - 1)}|{'-' * (w_num - 1)}:|{'-' * (w_pct - 1)}:|\n")
            f.write(f"| {pad('Bestätigt (Eigene + Externe)', w_cat)} | {pad(str(n_confirmed), w_num, 'right')} | {pad(pct(n_confirmed), w_pct, 'right')} |\n")
            f.write(f"| {pad('Nur extern erkannt', w_cat)} | {pad(str(n_external), w_num, 'right')} | {pad(pct(n_external), w_pct, 'right')} |\n")
            f.write(f"| {pad('Nur eigene Erkennung', w_cat)} | {pad(str(n_unconfirmed), w_num, 'right')} | {pad(pct(n_unconfirmed), w_pct, 'right')} |\n")
            f.write(f"| {pad('**Gesamt**', w_cat)} | {pad(f'**{total}**', w_num, 'right')} | {pad('**100%**', w_pct, 'right')} |\n")

            # Show which external sources were found
            external_sources_used = set()
            for t in self.tracks:
                for s in getattr(t, "sources", []):
                    if s not in ["own", "Shazam", "ACRCloud", "AcoustID",
                                 "shazam", "acrcloud", "acoustid"]:
                        external_sources_used.add(s)

            if external_sources_used:
                f.write(f"\n**Externe Quellen:** {', '.join(sorted(external_sources_used))}\n")

            # ─────────────────────────────────────────────────────────────────
            # Detailed comparison table (if merger data available)
            # ─────────────────────────────────────────────────────────────────
            if self.merger:
                try:
                    comparison_md = self._build_comparison_table()
                    if comparison_md:
                        f.write("\n## Detaillierter Quellen-Vergleich\n\n")
                        f.write(comparison_md)
                        f.write("\n")

                        # Add metrics table
                        metrics_md = self._build_metrics_table()
                        if metrics_md:
                            f.write("\n### Ergebnis-Vergleich\n\n")
                            f.write(metrics_md)
                            f.write("\n")
                except Exception as e:
                    logger.warning(f"Could not build comparison table: {e}")

            # ─────────────────────────────────────────────────────────────────
            # Detailed Tracklist sections
            # ─────────────────────────────────────────────────────────────────

            # Confirmed tracks section (main tracklist)
            f.write("\n## Tracklist\n\n")
            if confirmed:
                for i, track in enumerate(confirmed, 1):
                    f.write(
                        f"{i}. **{track.time_in_mix}** - "
                        f"{track.artist} - {track.song_name}"
                    )
                    # Show sources
                    f.write(format_sources(track))
                    # Show confidence and BPM
                    meta = []
                    meta.append(f"{track.confidence:.0f}%")
                    bpm = getattr(track, "bpm", None)
                    if bpm:
                        meta.append(f"{bpm:.0f} BPM")
                    f.write(f" ({', '.join(meta)})")
                    f.write("\n")
            else:
                f.write("_Keine bestatigten Tracks gefunden._\n")

            # External-only tracks section
            if external_only:
                f.write(
                    "\n## Nur extern erkannt (nicht eigene Erkennung)\n\n"
                )
                for i, track in enumerate(external_only, 1):
                    f.write(
                        f"{i}. **{track.time_in_mix}** - "
                        f"{track.artist} - {track.song_name}"
                    )
                    f.write(format_sources(track))
                    f.write("\n")

            # Unconfirmed tracks section
            if unconfirmed:
                f.write(
                    "\n## Nur eigene Erkennung (keine externe Bestaetigung)\n\n"
                )
                for i, track in enumerate(unconfirmed, 1):
                    f.write(
                        f"{i}. **{track.time_in_mix}** - "
                        f"{track.artist} - {track.song_name}"
                    )
                    providers = getattr(track, "providers", [])
                    if providers:
                        f.write(f" [{', '.join(providers)}]")
                    # Show confidence and BPM
                    meta = [f"{track.confidence:.0f}%"]
                    bpm = getattr(track, "bpm", None)
                    if bpm:
                        meta.append(f"{bpm:.0f} BPM")
                    f.write(f" _({', '.join(meta)})_")
                    if getattr(track, "bpm_outlier", False):
                        f.write(" **BPM outlier**")
                    f.write("\n")

            # Statistics section (technical details only, summary is in Quellen-Vergleich)
            f.write("\n## Technische Details\n\n")

            if self.tracks:
                # Only show own-identified tracks for confidence stats
                own_tracks = [t for t in self.tracks if not getattr(t, "external_only", False)]
                if own_tracks:
                    avg_conf = sum(t.confidence for t in own_tracks) / len(own_tracks)
                    f.write(
                        f"- Durchschnittliche Confidence (eigene): {avg_conf:.0f}%\n"
                    )

                db_verified = sum(
                    1
                    for t in self.tracks
                    if getattr(t, "verified_by_db", False)
                )
                if db_verified > 0:
                    f.write(
                        f"- MusicBrainz verifiziert: {db_verified}/{total}\n"
                    )

                bpms = [
                    t.bpm
                    for t in self.tracks
                    if getattr(t, "bpm", None)
                ]
                if bpms:
                    import statistics

                    median_bpm = statistics.median(bpms)
                    f.write(f"- Median BPM: {median_bpm:.0f}\n")

                # Show provider usage
                provider_counts: dict = {}
                for t in self.tracks:
                    for p in getattr(t, "providers", []):
                        provider_counts[p] = provider_counts.get(p, 0) + 1
                if provider_counts:
                    providers_str = ", ".join(
                        f"{p}: {c}" for p, c in sorted(provider_counts.items())
                    )
                    f.write(f"- Provider-Treffer: {providers_str}\n")

        logger.info(f"Saved Markdown tracklist to: {output_file}")
        return output_file

    def _build_comparison_table(self) -> Optional[str]:
        """Build the detailed comparison table from merger raw data.

        Returns:
            Markdown table string or None if no data
        """
        if not self.merger:
            return None

        try:
            own_tracks, external_results = self.merger.get_raw_data()

            if not own_tracks and not external_results:
                return None

            comparison = TrackComparison()

            # Add own tracks (separated by provider)
            comparison.add_own_tracks(own_tracks)

            # Add external tracks
            for source, result in external_results.items():
                if result.success and result.tracks:
                    comparison.add_external_tracks(source, result.tracks)

            # Build and return the table
            return comparison.to_markdown_table()

        except Exception as e:
            logger.warning(f"Failed to build comparison table: {e}")
            return None

    def _build_metrics_table(self) -> Optional[str]:
        """Build the metrics comparison table.

        Returns:
            Markdown table string or None if no data
        """
        if not self.merger:
            return None

        try:
            own_tracks, external_results = self.merger.get_raw_data()

            if not own_tracks and not external_results:
                return None

            comparison = TrackComparison()

            # Add own tracks (separated by provider)
            comparison.add_own_tracks(own_tracks)

            # Add external tracks
            for source, result in external_results.items():
                if result.success and result.tracks:
                    comparison.add_external_tracks(source, result.tracks)

            # Check if we have 1001tracklists as reference
            has_1001 = (
                "1001tracklists" in external_results
                and external_results["1001tracklists"].success
                and external_results["1001tracklists"].tracks
            )

            if has_1001:
                return comparison.to_markdown_metrics(reference_source="1001tracklists")
            elif "set79" in external_results and external_results["set79"].success:
                # Fall back to set79 as reference
                return comparison.to_markdown_metrics(reference_source="set79")
            else:
                return None

        except Exception as e:
            logger.warning(f"Failed to build metrics table: {e}")
            return None

    def _build_consolidated_tracklist(self) -> Optional[str]:
        """Build a consolidated tracklist merging all sources.

        Priority: set79 > 1001tracklists > Shazam > ACRCloud

        Returns:
            Markdown tracklist string or None if no data
        """
        if not self.merger:
            return None

        try:
            own_tracks, external_results = self.merger.get_raw_data()

            if not own_tracks and not external_results:
                return None

            comparison = TrackComparison()

            # Add own tracks (separated by provider)
            comparison.add_own_tracks(own_tracks)

            # Add external tracks
            for source, result in external_results.items():
                if result.success and result.tracks:
                    comparison.add_external_tracks(source, result.tracks)

            # Build consolidated tracklist with priority
            return comparison.to_consolidated_tracklist(
                priority=["set79", "1001tracklists", "shazam", "acrcloud"]
            )

        except Exception as e:
            logger.warning(f"Failed to build consolidated tracklist: {e}")
            return None

    def _save_m3u(self) -> Path:
        """Save tracks as M3U playlist."""
        output_file = self.output_dir / self._format_filename("m3u")

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")

            for track in self.tracks:
                duration = getattr(track, "duration", -1)
                f.write(f"#EXTINF:{duration},{track.artist} - {track.song_name}\n")
                # Note: Since we don't have actual file paths,
                # we add a comment with the time in mix
                f.write(f"# Time in mix: {track.time_in_mix}\n")

        logger.info(f"Saved M3U playlist to: {output_file}")
        return output_file

    def save_all(self) -> List[Path]:
        """
        Save tracks in all available formats.

        Returns:
            List of paths to saved files
        """
        formats = ["json", "markdown", "m3u"]
        saved_files = []

        try:
            for format_type in formats:
                try:
                    if path := self.save(format_type):
                        saved_files.append(path)
                    else:
                        logger.error(f"Failed to save {format_type} format")
                except Exception as e:
                    logger.error(f"Error saving {format_type} format: {e}")
                    continue

            if not saved_files:
                raise ExportError("Failed to save tracks in any format")

            logger.info(f"Successfully saved tracklist in {len(saved_files)} formats")
            return saved_files

        except Exception as e:
            logger.error(f"Error in save_all: {e}")
            return []
