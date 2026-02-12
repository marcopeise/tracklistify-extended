"""ACRCloud track identification provider."""

# Standard library imports
import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

# Third-party imports
import aiohttp
from aiohttp import ClientTimeout, FormData

from tracklistify.config.factory import get_config
from tracklistify.providers.base import (
    AuthenticationError,
    IdentificationError,
    ProviderError,
    RateLimitError,
    TrackIdentificationProvider,
)
from tracklistify.utils.logger import get_logger

# Local/package imports

logger = get_logger(__name__)


class ACRCloudProvider(TrackIdentificationProvider):
    """ACRCloud implementation of track identification provider."""

    def __init__(
        self,
        access_key: Optional[str] = None,
        access_secret: Optional[str] = None,
        host: Optional[str] = None,
        timeout: int = 10,
    ):
        """Initialize ACRCloud provider.

        Args:
            access_key: ACRCloud access key (or from env TRACKLISTIFY_ACR_ACCESS_KEY)
            access_secret: ACRCloud access secret (or from env TRACKLISTIFY_ACR_ACCESS_SECRET)
            host: ACRCloud API host
            timeout: Request timeout in seconds
        """
        config = get_config()
        def _clean_env(val: str) -> str:
            """Strip whitespace and inline comments from env values."""
            return val.split("#")[0].strip()

        self.access_key = _clean_env(
            access_key or os.getenv("TRACKLISTIFY_ACR_ACCESS_KEY", "")
        )
        secret = _clean_env(
            access_secret or os.getenv("TRACKLISTIFY_ACR_ACCESS_SECRET", "")
        )
        self.access_secret = secret.encode()
        self.host = _clean_env(
            host
            or os.getenv(
                "TRACKLISTIFY_ACRCLOUD_HOST",
                "identify-eu-west-1.acrcloud.com",
            )
        )
        self.endpoint = f"https://{self.host}/v1/identify"
        self.timeout = ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        logger.debug(
            f"ACRCloud initialized: host={self.host}, "
            f"key={self.access_key[:8]}..., endpoint={self.endpoint}"
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        """Close the provider's resources."""
        if self._session:
            await self._session.close()
            self._session = None

    def _sign_string(self, string_to_sign: str) -> str:
        """Sign a string using HMAC-SHA1."""
        hmac_obj = hmac.HMAC(self.access_secret, string_to_sign.encode(), hashlib.sha1)
        return base64.b64encode(hmac_obj.digest()).decode("ascii")

    def _prepare_request_data(self, audio_data: bytes, start_time: float) -> Dict:
        """Prepare request data for ACRCloud API."""
        current_time = time.time()
        string_to_sign = "\n".join(
            [
                "POST",
                "/v1/identify",
                self.access_key,
                "audio",
                "1",
                str(int(current_time)),
            ]
        )

        signature = self._sign_string(string_to_sign)

        data = {
            "access_key": self.access_key,
            "sample_bytes": len(audio_data),
            "timestamp": str(int(current_time)),
            "signature": signature,
            "data_type": "audio",
            "signature_version": "1",
        }

        return {"data": data}

    async def identify_track(
        self, audio_segment, start_time: float = 0
    ) -> Optional[Dict[str, Any]]:
        """
        Identify a track from an audio segment.

        Args:
            audio_segment: Audio segment object with file_path attribute,
                          or raw bytes for backward compatibility
            start_time: Start time in seconds for the audio segment

        Returns:
            Dict containing track information, or None if no match

        Raises:
            AuthenticationError: If authentication fails
            RateLimitError: If rate limit is exceeded
            IdentificationError: If identification fails
            ProviderError: For other provider-related errors
        """
        # Support both audio segment objects and raw bytes
        if isinstance(audio_segment, bytes):
            audio_data = audio_segment
        elif hasattr(audio_segment, "file_path"):
            try:
                with open(audio_segment.file_path, "rb") as f:
                    audio_data = f.read()
            except Exception as e:
                logger.error(f"Failed to read audio segment: {e}")
                return None
        else:
            logger.error("Invalid audio_segment: expected bytes or object with file_path")
            return None

        try:
            session = await self._get_session()
            request_data = self._prepare_request_data(audio_data, start_time)

            form = FormData()
            # Add the regular form fields
            for key, value in request_data["data"].items():
                form.add_field(key, str(value))
            # Add the audio file
            form.add_field("sample", audio_data, filename="sample.wav")

            async with session.post(self.endpoint, data=form) as response:
                if response.status == 401:
                    raise AuthenticationError("Invalid ACRCloud credentials")
                elif response.status == 429:
                    raise RateLimitError("ACRCloud rate limit exceeded")
                elif response.status != 200:
                    raise ProviderError(f"ACRCloud API error: {response.status}")

                try:
                    text_response = await response.text()
                    try:
                        result = json.loads(text_response)
                    except json.JSONDecodeError:
                        raise ProviderError(
                            "Failed to parse ACRCloud response. "
                            f"Response text: {text_response[:200]}"
                        ) from None
                except Exception as err:
                    raise ProviderError(
                        f"Failed to parse ACRCloud response. "
                        f"Response text: {text_response[:200]}"
                    ) from err

                if result["status"]["code"] != 0:
                    if result["status"]["code"] in (2000, 3001, 3003):
                        # 2000 = invalid access key
                        # 3001 = missing/invalid access key
                        # 3003 = limit exceeded (account level)
                        logger.error(
                            f"ACRCloud auth/key error (code {result['status']['code']}). "
                            f"Key: {self.access_key[:8]}..., "
                            f"Host: {self.host}. "
                            f"Message: {result['status']['msg']}"
                        )
                        raise AuthenticationError(result["status"]["msg"])
                    elif result["status"]["code"] == 3015:
                        raise RateLimitError(result["status"]["msg"])
                    elif result["status"]["code"] == 1001:  # No result found
                        return {
                            "status": {"code": 1, "msg": "No music detected"},
                            "metadata": {"music": []},
                        }
                    else:
                        raise IdentificationError(
                            f"ACRCloud error: {result['status']['msg']}"
                        )

                # Standardize response format
                if not result.get("metadata", {}).get("music"):
                    return {
                        "status": {"code": 1, "msg": "No music detected"},
                        "metadata": {"music": []},
                    }

                music_list = []
                for music in result["metadata"]["music"]:
                    track = {
                        "title": music.get("title", ""),
                        "artists": music.get("artists", [{"name": "Unknown"}]),
                        "album": music.get("album", {}).get("name", ""),
                        "release_date": music.get("release_date", ""),
                        "score": float(music.get("score", 0)),
                        "genres": music.get("genres", []),
                        "external_ids": {
                            "isrc": music.get("external_ids", {}).get("isrc"),
                            "upc": music.get("external_ids", {}).get("upc"),
                        },
                    }
                    music_list.append(track)

                return {
                    "status": {"code": 0, "msg": "Success"},
                    "metadata": {"music": music_list},
                }

        except (AuthenticationError, RateLimitError, IdentificationError) as err:
            raise err from None
        except Exception as err:
            raise ProviderError(f"ACRCloud provider error: {str(err)}") from err

    async def enrich_metadata(self, track_info: Dict) -> Dict:
        """
        Enrich track metadata with additional information.

        Args:
            track_info: Basic track information

        Returns:
            Dict containing enriched track information
        """
        # ACRCloud doesn't support additional metadata enrichment
        return track_info
