"""AcoustID/Chromaprint track identification provider."""

# Standard library imports
import os
from typing import Any, Dict, Optional

from tracklistify.providers.base import (
    TrackIdentificationProvider,
)
from tracklistify.utils.logger import get_logger

logger = get_logger(__name__)

# Try importing the acoustid library
try:
    import acoustid

    ACOUSTID_AVAILABLE = True
except ImportError:
    ACOUSTID_AVAILABLE = False
    logger.warning(
        "pyacoustid not installed. AcoustID provider unavailable. "
        "Install with: uv add pyacoustid"
    )


class AcoustIDProvider(TrackIdentificationProvider):
    """AcoustID track identification provider using pyacoustid library."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize AcoustID provider."""
        self.api_key = (
            api_key or os.getenv("TRACKLISTIFY_ACOUSTID_API_KEY", "")
        ).strip()
        logger.debug(f"AcoustID initialized with key: {self.api_key[:4]}...")

    async def identify_track(
        self, audio_segment
    ) -> Optional[Dict[str, Any]]:
        """Identify track from an audio segment using AcoustID."""
        if not ACOUSTID_AVAILABLE:
            logger.warning("pyacoustid library not available")
            return None

        if not self.api_key:
            logger.warning("AcoustID API key not configured")
            return None

        # Get file path from segment
        if hasattr(audio_segment, "file_path"):
            file_path = audio_segment.file_path
        else:
            logger.error("Audio segment missing file_path attribute")
            return None

        try:
            # acoustid.match() yields (score, recording_id, title, artist)
            matches = list(acoustid.match(self.api_key, file_path))

            if not matches:
                logger.debug(f"AcoustID: no matches for segment {file_path}")
                return None

            # Get best match by score
            best = max(matches, key=lambda m: m[0])
            score = best[0] * 100  # Convert 0-1 to 0-100

            # Unpack — match tuples are (score, rid, title, artist)
            recording_id = best[1] if len(best) > 1 else None
            title = best[2] if len(best) > 2 else None
            artist = best[3] if len(best) > 3 else None

            if not title or not artist:
                logger.debug(
                    f"AcoustID: match found but missing metadata "
                    f"(score={score:.0f}%, rid={recording_id})"
                )
                return None

            logger.info(
                f"AcoustID match: {artist} - {title} ({score:.0f}%)"
            )

            return {
                "metadata": {
                    "music": [
                        {
                            "title": title,
                            "artists": [{"name": artist}],
                            "score": score,
                        }
                    ]
                }
            }

        except acoustid.NoBackendError:
            logger.error(
                "Chromaprint/fpcalc not found. "
                "Install with: brew install chromaprint"
            )
            return None
        except acoustid.FingerprintGenerationError as e:
            logger.warning(f"AcoustID fingerprint generation failed: {e}")
            return None
        except acoustid.WebServiceError as e:
            logger.warning(f"AcoustID API error: {e}. Key: {self.api_key}")
            return None
        except Exception as e:
            logger.warning(f"AcoustID error: {type(e).__name__}: {e}")
            return None

    async def enrich_metadata(
        self, track_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Enrich track metadata (not supported)."""
        return track_info

    async def close(self):
        """Cleanup resources (nothing to close)."""
        pass
