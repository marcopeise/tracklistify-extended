"""
Track identification helper functions and utilities.
"""

# Standard library imports
import re
from typing import Any, Dict, List, Optional, Tuple

# Third-party imports
from mutagen._file import File, FileType

from tracklistify.config.factory import get_config

# Local/package imports
from tracklistify.core.track import Track, TrackMatcher
from tracklistify.providers.factory import create_provider_factory
from .logger import get_logger
from .time_formatter import format_seconds_to_hhmmss

logger = get_logger(__name__)


def get_audio_info(audio_path: str) -> Optional[FileType]:
    """Get audio file metadata."""
    return File(audio_path)


def format_duration(duration: float) -> str:
    """Format duration in seconds to HH:MM:SS."""
    ...


def create_progress_bar(progress: float, width: int = 30) -> str:
    """Create a progress bar string."""
    ...


class ProgressDisplay:
    """Handles the progress display for track identification."""

    ...


def _normalize_for_comparison(s: str) -> str:
    """Normalize a string for fuzzy comparison."""
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def _extract_track_info(
    result: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, str, float]]:
    """Extract (title, artist, score) from a provider result dict.

    Returns None if the result doesn't contain valid track info.
    """
    if not result:
        return None
    metadata = result.get("metadata", {}).get("music", [{}])
    if not metadata or not metadata[0]:
        return None
    entry = metadata[0]
    title = entry.get("title", "")
    artists = entry.get("artists", [{}])
    artist = artists[0].get("name", "") if artists else ""
    score = float(entry.get("score", 0))
    if not title or not artist:
        return None
    return (title, artist, score)


class IdentificationManager:
    """Manages track identification using configured providers."""

    def __init__(self, config=None, provider_factory=None):
        self.config = config or get_config()
        self.provider_factory = provider_factory or create_provider_factory()
        self.track_matcher = TrackMatcher()

    async def _identify_with_provider(
        self, provider, segment
    ) -> Optional[Dict[str, Any]]:
        """Identify a segment using a single provider, with error handling.

        Returns the result dict on success, None on no match.
        Raises the original exception on errors (auth, network, etc.)
        so the caller can track failures vs. no-matches.
        """
        return await provider.identify_track(segment)

    def _correlate_results(
        self, results: Dict[str, Optional[Dict[str, Any]]]
    ) -> Optional[Tuple[str, str, float, List[str]]]:
        """Correlate results from multiple providers.

        Returns (title, artist, confidence, provider_names) or None.

        Correlation logic:
        - Both providers agree on same track -> avg confidence + 10% bonus (max 100)
        - Only one provider matched -> confidence * 0.7
        - Both matched different tracks -> keep both with lowered confidence
        """
        extracted = {}
        for name, result in results.items():
            info = _extract_track_info(result)
            if info:
                extracted[name] = info

        if not extracted:
            return None

        provider_names = list(extracted.keys())

        if len(extracted) == 1:
            # Single provider result — only lightly penalize since
            # the other provider may simply not cover this track
            # (e.g., AcoustID rarely matches DJ mix segments)
            name = provider_names[0]
            title, artist, score = extracted[name]
            adjusted_score = min(score * 0.9, 100.0)
            return (title, artist, adjusted_score, [name])

        # Multiple providers — check if they agree
        items = list(extracted.items())
        first_name, (first_title, first_artist, first_score) = items[0]
        second_name, (second_title, second_artist, second_score) = items[1]

        first_norm_title = _normalize_for_comparison(first_title)
        first_norm_artist = _normalize_for_comparison(first_artist)
        second_norm_title = _normalize_for_comparison(second_title)
        second_norm_artist = _normalize_for_comparison(second_artist)

        if first_norm_title == second_norm_title and first_norm_artist == second_norm_artist:
            # Both providers agree — high confidence
            avg_score = (first_score + second_score) / 2
            boosted = min(avg_score + 10, 100.0)
            return (first_title, first_artist, boosted, provider_names)

        # Providers disagree — return the one with higher score, moderately penalized
        if first_score >= second_score:
            return (first_title, first_artist, min(first_score * 0.8, 100.0), [first_name])
        else:
            return (second_title, second_artist, min(second_score * 0.8, 100.0), [second_name])

    async def identify_tracks(self, audio_segments):
        """Identify tracks using all available providers with correlation."""
        import asyncio

        # Get all available providers
        available_providers = self.provider_factory.get_available_providers()
        provider_names = list(available_providers.keys())

        if not available_providers:
            logger.error("No providers available for identification")
            return []

        multi_provider = len(available_providers) > 1
        if multi_provider:
            logger.info(
                f"Multi-provider mode: using {', '.join(provider_names)}"
            )
        else:
            logger.info(f"Single-provider mode: using {provider_names[0]}")

        identified_tracks = []
        total = len(audio_segments)

        # Track consecutive failures per provider to auto-disable broken ones
        provider_failures = {name: 0 for name in provider_names}
        disabled_providers = set()
        max_consecutive_failures = 3

        for idx, segment in enumerate(audio_segments):
            # Check for cancellation
            if asyncio.current_task() and asyncio.current_task().cancelled():
                logger.info("Identification cancelled by user")
                break

            try:
                if multi_provider:
                    # Query all active providers for this segment
                    results = {}
                    for name, provider in available_providers.items():
                        if name in disabled_providers:
                            continue
                        try:
                            result = await self._identify_with_provider(
                                provider, segment
                            )
                            if result:
                                results[name] = result
                                provider_failures[name] = 0
                            # None = no match (normal), don't count as failure
                        except Exception as e:
                            # Actual error — count toward auto-disable
                            logger.warning(
                                f"Provider '{name}' error at "
                                f"{segment.start_time}s: {e}"
                            )
                            provider_failures[name] += 1
                            if provider_failures[name] >= max_consecutive_failures:
                                logger.warning(
                                    f"Disabling provider '{name}' after "
                                    f"{max_consecutive_failures} "
                                    f"consecutive errors"
                                )
                                disabled_providers.add(name)

                    # Correlate results
                    correlated = self._correlate_results(results)
                    if correlated is None:
                        logger.debug(
                            f"No providers matched segment at {segment.start_time}s"
                        )
                        continue

                    title, artist, confidence, matched_providers = correlated
                    time_in_mix = format_seconds_to_hhmmss(
                        int(segment.start_time)
                    )

                    try:
                        track = Track(
                            song_name=title,
                            artist=artist,
                            time_in_mix=time_in_mix,
                            confidence=confidence,
                            providers=matched_providers,
                        )
                        self.track_matcher.add_track(track)
                        identified_tracks.append(track)
                    except ValueError as e:
                        logger.error(f"Failed to create track: {e}")
                        continue

                else:
                    # Single provider — original behavior
                    provider = list(available_providers.values())[0]
                    track_info = await self._identify_with_provider(
                        provider, segment
                    )
                    if track_info is None:
                        logger.debug(
                            "Provider returned None for track identification"
                        )
                        continue

                    metadata = track_info.get("metadata", {}).get(
                        "music", [{}]
                    )[0]
                    if not metadata:
                        logger.error(
                            "No track metadata found in provider response"
                        )
                        continue

                    time_in_mix = format_seconds_to_hhmmss(
                        int(segment.start_time)
                    )

                    try:
                        track = Track(
                            song_name=metadata.get("title", "Unknown Title"),
                            artist=metadata.get("artists", [{}])[0].get(
                                "name", "Unknown Artist"
                            ),
                            time_in_mix=time_in_mix,
                            confidence=float(metadata.get("score", 100.0)),
                            providers=[provider_names[0]],
                        )
                        self.track_matcher.add_track(track)
                        identified_tracks.append(track)
                    except ValueError as e:
                        logger.error(f"Failed to create track: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"Unexpected error creating track: {e}")
                        continue

            except Exception as e:
                logger.error(f"Identification failed for segment: {e}")
                continue

        # Get unique tracks sorted by time in mix
        unique_tracks = self.track_matcher.get_unique_tracks()
        logger.info(
            f"Identified {len(unique_tracks)} unique tracks from "
            f"{len(identified_tracks)} total matches"
        )

        # Post-processing: BPM analysis
        unique_tracks = await self._analyze_bpm(unique_tracks, audio_segments)

        # Post-processing: MusicBrainz verification
        unique_tracks = await self._verify_tracks(unique_tracks)

        return unique_tracks

    async def _analyze_bpm(
        self, tracks: List[Track], audio_segments
    ) -> List[Track]:
        """Analyze BPM for each track and flag outliers."""
        from tracklistify.utils.bpm import BPMAnalyzer

        analyzer = BPMAnalyzer()
        if not analyzer.available:
            logger.info("BPM analysis skipped (librosa not installed)")
            return tracks

        logger.info("Analyzing BPM consistency...")

        # Build a map from start_time -> segment for quick lookup
        segment_map = {}
        for seg in audio_segments:
            segment_map[int(seg.start_time)] = seg

        # Analyze BPM for each track's corresponding segment
        bpm_values = []
        for track in tracks:
            seg = segment_map.get(track.time_to_seconds())
            if seg:
                bpm = analyzer.analyze_segment(seg.file_path)
                track.bpm = bpm
                bpm_values.append(bpm)
            else:
                bpm_values.append(None)

        # Detect outliers
        outlier_flags = analyzer.detect_outliers(bpm_values)
        for track, is_outlier in zip(tracks, outlier_flags):
            if is_outlier:
                track.bpm_outlier = True
                # Apply confidence penalty
                track.confidence = max(
                    0, track.confidence - analyzer.OUTLIER_PENALTY
                )
                logger.info(
                    f"BPM outlier: {track.song_name} "
                    f"(BPM: {track.bpm}, confidence reduced to "
                    f"{track.confidence:.0f}%)"
                )

        return tracks

    async def _verify_tracks(self, tracks: List[Track]) -> List[Track]:
        """Verify tracks against MusicBrainz database."""
        from tracklistify.utils.verification import TrackVerifier

        verifier = TrackVerifier()
        try:
            logger.info("Verifying tracks against MusicBrainz...")
            verified_count = 0
            for track in tracks:
                result = await verifier.verify_track(
                    track.artist, track.song_name
                )
                track.verified_by_db = result.found
                if result.found:
                    verified_count += 1
            logger.info(
                f"MusicBrainz verification: {verified_count}/{len(tracks)} "
                f"tracks verified"
            )
        except Exception as e:
            logger.warning(f"MusicBrainz verification failed: {e}")
        finally:
            await verifier.close()

        return tracks

    async def close(self):
        """Cleanup resources."""
        if self.provider_factory:
            await self.provider_factory.close_all()


async def identify_tracks(audio_path: str) -> Optional[List[Track]]:
    """
    Identify tracks in an audio file.

    Args:
        audio_path: Path to audio file

    Returns:
        List[Track]: List of identified tracks, or None if identification failed
    """
    try:
        manager = IdentificationManager()
        return await manager.identify_tracks(audio_path)
    except Exception as e:
        logger.error(f"Track identification failed: {e}")
        return None
