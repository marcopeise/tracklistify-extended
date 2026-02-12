"""
Track verification against external music databases.

Uses MusicBrainz API to verify that identified tracks actually exist.
No API key required — only a proper User-Agent header.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Optional

import aiohttp

from tracklistify.utils.logger import get_logger

logger = get_logger(__name__)

MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2/recording"
USER_AGENT = "Tracklistify/0.7.0 (https://github.com/betmoar/tracklistify)"

# MusicBrainz rate limit: 1 request per second
_last_request_time = 0.0


@dataclass
class VerificationResult:
    """Result of a track verification lookup."""

    found: bool
    musicbrainz_id: Optional[str] = None
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
    score: int = 0


def _normalize(s: str) -> str:
    """Normalize a string for comparison."""
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


class TrackVerifier:
    """Verifies tracks against MusicBrainz database."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session with proper headers."""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT}
            )
        return self._session

    async def _rate_limit(self) -> None:
        """Enforce MusicBrainz rate limit (1 req/sec)."""
        global _last_request_time
        import time

        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        _last_request_time = time.time()

    async def verify_track(
        self, artist: str, title: str
    ) -> VerificationResult:
        """Check if an artist+title combination exists in MusicBrainz.

        Args:
            artist: Artist name
            title: Track title

        Returns:
            VerificationResult with match details
        """
        try:
            await self._rate_limit()
            session = await self._get_session()

            # Clean up title — remove remix/edit suffixes for broader matching
            clean_title = re.sub(
                r"\s*[\(\[].*?[\)\]]", "", title
            ).strip()

            # Build Lucene query
            query = f'recording:"{clean_title}" AND artist:"{artist}"'

            params = {
                "query": query,
                "fmt": "json",
                "limit": "3",
            }

            async with session.get(
                MUSICBRAINZ_API_URL, params=params
            ) as response:
                if response.status == 503:
                    logger.warning("MusicBrainz rate limited, retrying...")
                    await asyncio.sleep(2.0)
                    return await self.verify_track(artist, title)

                if response.status != 200:
                    logger.warning(
                        f"MusicBrainz API error: {response.status}"
                    )
                    return VerificationResult(found=False)

                data = await response.json()
                recordings = data.get("recordings", [])

                if not recordings:
                    return VerificationResult(found=False)

                # Check if any result is a reasonable match
                norm_title = _normalize(clean_title)
                norm_artist = _normalize(artist)

                for rec in recordings:
                    rec_title = rec.get("title", "")
                    rec_score = rec.get("score", 0)
                    rec_artists = rec.get("artist-credit", [])

                    rec_artist_name = ""
                    if rec_artists:
                        rec_artist_name = rec_artists[0].get(
                            "name",
                            rec_artists[0].get("artist", {}).get("name", ""),
                        )

                    # Check for reasonable match
                    if (
                        _normalize(rec_title) == norm_title
                        and _normalize(rec_artist_name) == norm_artist
                        and rec_score >= 80
                    ):
                        return VerificationResult(
                            found=True,
                            musicbrainz_id=rec.get("id"),
                            matched_title=rec_title,
                            matched_artist=rec_artist_name,
                            score=rec_score,
                        )

                # No exact match but there are results — partial match
                best = recordings[0]
                if best.get("score", 0) >= 90:
                    best_artists = best.get("artist-credit", [])
                    best_artist_name = ""
                    if best_artists:
                        best_artist_name = best_artists[0].get(
                            "name",
                            best_artists[0].get("artist", {}).get("name", ""),
                        )
                    return VerificationResult(
                        found=True,
                        musicbrainz_id=best.get("id"),
                        matched_title=best.get("title", ""),
                        matched_artist=best_artist_name,
                        score=best.get("score", 0),
                    )

                return VerificationResult(found=False)

        except Exception as e:
            logger.warning(f"MusicBrainz verification failed: {e}")
            return VerificationResult(found=False)

    async def close(self) -> None:
        """Cleanup resources."""
        if self._session:
            await self._session.close()
            self._session = None
