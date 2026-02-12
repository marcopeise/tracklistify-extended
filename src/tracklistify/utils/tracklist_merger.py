"""
Tracklist merger for combining own identification results with external sources.

This module handles the logic for merging tracks from multiple sources
(own identification + set79 + 1001tracklists) into a single consolidated tracklist.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from tracklistify.core.track import Track
from tracklistify.utils.external_sources import ExternalSourceResult, ExternalTrack
from tracklistify.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MergedTrack:
    """A track with merged information from multiple sources."""

    song_name: str
    artist: str
    time_in_mix: str
    confidence: float
    sources: List[str] = field(default_factory=list)
    providers: List[str] = field(default_factory=list)
    label: Optional[str] = None
    bpm: Optional[float] = None
    bpm_outlier: bool = False
    verified_by_db: bool = False
    external_only: bool = False  # True if only found in external sources
    unconfirmed: bool = False  # True if only found in own identification

    def __str__(self) -> str:
        sources_str = " + ".join(self.sources) if self.sources else "unknown"
        return f"[{self.time_in_mix}] {self.artist} - {self.song_name} [{sources_str}]"

    def time_to_seconds(self) -> int:
        """Convert time_in_mix to seconds."""
        try:
            time = datetime.strptime(self.time_in_mix, "%H:%M:%S")
            return time.hour * 3600 + time.minute * 60 + time.second
        except ValueError:
            return 0

    def to_track(self) -> Track:
        """Convert back to a Track object for output compatibility."""
        track = Track(
            song_name=self.song_name,
            artist=self.artist,
            time_in_mix=self.time_in_mix,
            confidence=self.confidence,
            providers=self.providers,
            bpm=self.bpm,
            bpm_outlier=self.bpm_outlier,
            verified_by_db=self.verified_by_db,
        )
        # Add sources as a custom attribute
        track.sources = self.sources
        track.external_only = self.external_only
        track.unconfirmed = self.unconfirmed
        return track


class TracklistMerger:
    """Merges tracks from own identification with external sources."""

    # Time window in seconds for considering tracks as the same
    TIME_WINDOW = 60

    # Confidence adjustments
    MULTI_SOURCE_BONUS = 10.0  # Bonus when confirmed by external source
    EXTERNAL_ONLY_CONFIDENCE = 70.0  # Default confidence for external-only tracks

    def __init__(self, time_window: int = 60):
        """Initialize the merger.

        Args:
            time_window: Time window in seconds for matching tracks
        """
        self.time_window = time_window

        # Store raw data for comparison table
        self._own_tracks: List[Track] = []
        self._external_results: Dict[str, ExternalSourceResult] = {}

    def get_raw_data(self) -> Tuple[List[Track], Dict[str, ExternalSourceResult]]:
        """Get the raw data from the last merge for comparison table.

        Returns:
            Tuple of (own_tracks, external_results)
        """
        return self._own_tracks, self._external_results

    def merge(
        self,
        own_tracks: List[Track],
        external_results: Dict[str, ExternalSourceResult],
    ) -> List[MergedTrack]:
        """Merge own identification results with external sources.

        Merge rules:
        - Own + set79 + 1001TL agree: highest priority, confidence boost
        - Own + one external source: high priority, small boost
        - Only external sources: marked as "external", added to list
        - Only own identification: marked as "unconfirmed", lower priority

        Args:
            own_tracks: Tracks from our own identification
            external_results: Results from external source fetchers

        Returns:
            List of MergedTrack objects, sorted by time
        """
        logger.info(
            f"Merging {len(own_tracks)} own tracks with external sources..."
        )

        # Store raw data for comparison table
        self._own_tracks = own_tracks
        self._external_results = external_results

        # Convert external results to flat list of external tracks
        external_tracks: List[ExternalTrack] = []
        for source, result in external_results.items():
            if result.success and result.tracks:
                external_tracks.extend(result.tracks)
                logger.debug(
                    f"  {source}: {len(result.tracks)} tracks to merge"
                )

        if not external_tracks:
            logger.info("No external tracks to merge, returning own tracks only")
            return self._convert_own_tracks(own_tracks)

        # Build merged list
        merged: List[MergedTrack] = []
        used_own_indices: Set[int] = set()
        used_external_indices: Set[int] = set()

        # First pass: match own tracks with external tracks
        for own_idx, own_track in enumerate(own_tracks):
            matching_external = self._find_matching_external(
                own_track, external_tracks, used_external_indices
            )

            if matching_external:
                # Found matches - merge them
                merged_track = self._create_merged_track(
                    own_track, matching_external
                )
                merged.append(merged_track)
                used_own_indices.add(own_idx)
                for ext_idx, _ in matching_external:
                    used_external_indices.add(ext_idx)
            else:
                # No external match - mark as unconfirmed
                merged_track = self._create_merged_track(own_track, [])
                merged_track.unconfirmed = True
                merged.append(merged_track)
                used_own_indices.add(own_idx)

        # Second pass: add external-only tracks
        for ext_idx, ext_track in enumerate(external_tracks):
            if ext_idx not in used_external_indices:
                # Check if this external track matches any already merged track
                if not self._matches_any_merged(ext_track, merged):
                    merged_track = self._create_external_only_track(ext_track)

                    # Check if other external sources have the same track
                    other_sources = self._find_matching_external_by_track(
                        ext_track, external_tracks, {ext_idx}
                    )
                    for other_idx, other_ext in other_sources:
                        if other_ext.source not in merged_track.sources:
                            merged_track.sources.append(other_ext.source)
                        used_external_indices.add(other_idx)

                    merged.append(merged_track)
                    used_external_indices.add(ext_idx)

        # Sort by time
        merged.sort(key=lambda t: t.time_to_seconds())

        # Log summary
        own_confirmed = sum(1 for t in merged if not t.external_only and not t.unconfirmed)
        own_unconfirmed = sum(1 for t in merged if t.unconfirmed)
        external_only = sum(1 for t in merged if t.external_only)

        logger.info(
            f"Merge complete: {len(merged)} total tracks "
            f"(confirmed: {own_confirmed}, unconfirmed: {own_unconfirmed}, "
            f"external-only: {external_only})"
        )

        return merged

    def _find_matching_external(
        self,
        own_track: Track,
        external_tracks: List[ExternalTrack],
        used_indices: Set[int],
    ) -> List[Tuple[int, ExternalTrack]]:
        """Find external tracks that match an own track.

        Args:
            own_track: Track from own identification
            external_tracks: All external tracks
            used_indices: Indices of already used external tracks

        Returns:
            List of (index, ExternalTrack) tuples that match
        """
        matches = []
        own_time = own_track.time_to_seconds()

        for idx, ext_track in enumerate(external_tracks):
            if idx in used_indices:
                continue

            ext_time = self._time_str_to_seconds(ext_track.time_in_mix)

            # Skip time check if external track has no cue time (00:00:00)
            # In that case, match by artist/title only
            if ext_time != 0:
                if abs(own_time - ext_time) > self.time_window:
                    continue

            # Check artist/title similarity
            if self._tracks_are_similar(own_track, ext_track):
                matches.append((idx, ext_track))

        return matches

    def _find_matching_external_by_track(
        self,
        target: ExternalTrack,
        external_tracks: List[ExternalTrack],
        exclude_indices: Set[int],
    ) -> List[Tuple[int, ExternalTrack]]:
        """Find other external tracks that match a given external track.

        Args:
            target: The external track to match
            external_tracks: All external tracks
            exclude_indices: Indices to exclude from matching

        Returns:
            List of (index, ExternalTrack) tuples that match
        """
        matches = []
        target_time = self._time_str_to_seconds(target.time_in_mix)

        for idx, ext_track in enumerate(external_tracks):
            if idx in exclude_indices:
                continue

            ext_time = self._time_str_to_seconds(ext_track.time_in_mix)

            # Skip time check if either track has no cue time (00:00:00)
            if target_time != 0 and ext_time != 0:
                if abs(target_time - ext_time) > self.time_window:
                    continue

            # Check artist/title similarity
            if self._external_tracks_are_similar(target, ext_track):
                matches.append((idx, ext_track))

        return matches

    def _matches_any_merged(
        self, ext_track: ExternalTrack, merged: List[MergedTrack]
    ) -> bool:
        """Check if an external track matches any already merged track.

        Args:
            ext_track: External track to check
            merged: List of already merged tracks

        Returns:
            True if the track matches any merged track
        """
        ext_time = self._time_str_to_seconds(ext_track.time_in_mix)

        for merged_track in merged:
            merged_time = merged_track.time_to_seconds()

            # Skip time check if external track has no cue time (00:00:00)
            if ext_time != 0:
                if abs(ext_time - merged_time) > self.time_window:
                    continue

            if self._strings_are_similar(
                ext_track.artist, merged_track.artist
            ) and self._strings_are_similar(
                ext_track.title, merged_track.song_name
            ):
                return True

        return False

    def _tracks_are_similar(self, own: Track, ext: ExternalTrack) -> bool:
        """Check if an own track and external track are similar.

        Args:
            own: Track from own identification
            ext: External track

        Returns:
            True if tracks are considered the same
        """
        return self._strings_are_similar(
            own.artist, ext.artist
        ) and self._strings_are_similar(own.song_name, ext.title)

    def _external_tracks_are_similar(
        self, track1: ExternalTrack, track2: ExternalTrack
    ) -> bool:
        """Check if two external tracks are similar.

        Args:
            track1: First external track
            track2: Second external track

        Returns:
            True if tracks are considered the same
        """
        return self._strings_are_similar(
            track1.artist, track2.artist
        ) and self._strings_are_similar(track1.title, track2.title)

    def _strings_are_similar(self, s1: str, s2: str) -> bool:
        """Check if two strings are similar (fuzzy match).

        Handles variations like remixes, different punctuation, etc.

        Args:
            s1: First string
            s2: Second string

        Returns:
            True if strings are considered similar
        """
        if not s1 or not s2:
            return False

        # Normalize strings
        def normalize(s: str) -> str:
            s = s.lower()
            # Remove punctuation except spaces
            s = re.sub(r"[^\w\s]", "", s)
            # Collapse whitespace
            s = re.sub(r"\s+", " ", s).strip()
            return s

        def base_title(s: str) -> str:
            """Remove remix/edit suffixes."""
            s = re.sub(
                r"\s*\(.*?(remix|edit|mix|version|dub|remaster|bootleg).*?\)\s*$",
                "",
                s,
                flags=re.IGNORECASE,
            )
            s = re.sub(
                r"\s*\[.*?(remix|edit|mix|version|dub|remaster|bootleg).*?\]\s*$",
                "",
                s,
                flags=re.IGNORECASE,
            )
            return normalize(s)

        n1 = normalize(s1)
        n2 = normalize(s2)

        # Exact match after normalization
        if n1 == n2:
            return True

        # One contains the other (for partial matches)
        if n1 in n2 or n2 in n1:
            return True

        # Base title match (ignoring remix info)
        if base_title(s1) == base_title(s2):
            return True

        return False

    def _time_str_to_seconds(self, time_str: str) -> int:
        """Convert time string to seconds.

        Args:
            time_str: Time in HH:MM:SS format

        Returns:
            Time in seconds
        """
        try:
            parts = time_str.split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return 0
        except (ValueError, IndexError):
            return 0

    def _create_merged_track(
        self,
        own_track: Track,
        matching_external: List[Tuple[int, ExternalTrack]],
    ) -> MergedTrack:
        """Create a merged track from own and external tracks.

        Args:
            own_track: Track from own identification
            matching_external: List of matching external tracks

        Returns:
            MergedTrack combining all sources
        """
        # Build sources list
        sources = []

        # Add own providers as sources
        own_providers = getattr(own_track, "providers", []) or []
        for provider in own_providers:
            if provider not in sources:
                sources.append(provider)

        # Add "own" as a source marker if we have providers
        if own_providers:
            sources = ["own"] + sources

        # Add external sources
        for _, ext_track in matching_external:
            if ext_track.source not in sources:
                sources.append(ext_track.source)

        # Calculate confidence adjustment
        confidence = own_track.confidence
        if matching_external:
            # Boost confidence when confirmed by external sources
            num_external = len(set(ext.source for _, ext in matching_external))
            confidence = min(100.0, confidence + num_external * self.MULTI_SOURCE_BONUS)

        # Get label from external if available
        label = None
        for _, ext_track in matching_external:
            if ext_track.label:
                label = ext_track.label
                break

        return MergedTrack(
            song_name=own_track.song_name,
            artist=own_track.artist,
            time_in_mix=own_track.time_in_mix,
            confidence=confidence,
            sources=sources,
            providers=own_providers,
            label=label,
            bpm=getattr(own_track, "bpm", None),
            bpm_outlier=getattr(own_track, "bpm_outlier", False),
            verified_by_db=getattr(own_track, "verified_by_db", False),
            external_only=False,
            unconfirmed=False,
        )

    def _create_external_only_track(self, ext_track: ExternalTrack) -> MergedTrack:
        """Create a merged track from an external-only track.

        Args:
            ext_track: External track with no own match

        Returns:
            MergedTrack marked as external-only
        """
        return MergedTrack(
            song_name=ext_track.title,
            artist=ext_track.artist,
            time_in_mix=ext_track.time_in_mix,
            confidence=self.EXTERNAL_ONLY_CONFIDENCE,
            sources=[ext_track.source],
            providers=[],
            label=ext_track.label,
            bpm=None,
            bpm_outlier=False,
            verified_by_db=False,
            external_only=True,
            unconfirmed=False,
        )

    def _convert_own_tracks(self, own_tracks: List[Track]) -> List[MergedTrack]:
        """Convert own tracks to merged tracks when no external sources.

        Args:
            own_tracks: Tracks from own identification

        Returns:
            List of MergedTrack objects
        """
        merged = []
        for track in own_tracks:
            providers = getattr(track, "providers", []) or []
            merged.append(
                MergedTrack(
                    song_name=track.song_name,
                    artist=track.artist,
                    time_in_mix=track.time_in_mix,
                    confidence=track.confidence,
                    sources=["own"] + providers if providers else ["own"],
                    providers=providers,
                    label=None,
                    bpm=getattr(track, "bpm", None),
                    bpm_outlier=getattr(track, "bpm_outlier", False),
                    verified_by_db=getattr(track, "verified_by_db", False),
                    external_only=False,
                    unconfirmed=True,  # Mark as unconfirmed when no external sources
                )
            )
        return sorted(merged, key=lambda t: t.time_to_seconds())
