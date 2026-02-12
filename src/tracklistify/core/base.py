# tracklistify/core/app.py

# Standard library imports
import asyncio
import concurrent.futures
import hashlib
import json
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

# Local/package imports
from tracklistify.config.factory import get_config
from tracklistify.core.track import Track
from tracklistify.core.types import AudioSegment
from tracklistify.downloaders import DownloaderFactory
from tracklistify.exporters import TracklistOutput
from tracklistify.providers.factory import create_provider_factory
from tracklistify.utils.external_sources import ExternalSourceFetcher
from tracklistify.utils.identification import IdentificationManager
from tracklistify.utils.logger import get_logger
from tracklistify.utils.strings import sanitizer
from tracklistify.utils.tracklist_merger import TracklistMerger
from tracklistify.utils.validation import validate_input

logger = get_logger(__name__)


class AsyncApp:
    """Main application logic container"""

    def __init__(self, config=None):
        # Always refresh config
        self.config = config or get_config(force_refresh=True)
        self.provider_factory = create_provider_factory()
        self.downloader_factory = DownloaderFactory()
        self.logger = get_logger(__name__)
        self.shutdown_event = asyncio.Event()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        # Always recreate identification_manager with fresh config
        self.identification_manager = IdentificationManager(
            config=self.config, provider_factory=self.provider_factory
        )

        # External source integration
        self.external_fetcher = ExternalSourceFetcher()
        self.tracklist_merger = TracklistMerger()

        # Store the original URL for external source lookup
        self.original_url: Optional[str] = None

    def shutdown(self) -> None:
        """Shutdown the application gracefully."""
        self.logger.info("Shutting down...")
        self.shutdown_event.set()
        self.executor.shutdown(wait=True)

    def _get_cache_path(self, url: str) -> Path:
        """Get the cache base path for a URL (without extension)."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        cache_downloads = Path(self.config.cache_dir) / "downloads"
        cache_downloads.mkdir(parents=True, exist_ok=True)
        return cache_downloads / url_hash

    def _check_download_cache(self, url: str) -> Optional[Tuple[str, dict]]:
        """Check if a URL has been previously downloaded and cached.

        Returns (audio_file_path, metadata_dict) if cached, None otherwise.
        """
        cache_base = self._get_cache_path(url)
        audio_file = cache_base.with_suffix(".mp3")
        meta_file = cache_base.with_suffix(".json")
        if audio_file.exists() and meta_file.exists():
            try:
                metadata = json.loads(meta_file.read_text(encoding="utf-8"))
                self.logger.info(f"Found cached download: {audio_file}")
                return str(audio_file), metadata
            except Exception as e:
                self.logger.warning(f"Failed to read cache metadata: {e}")
        return None

    def _save_to_download_cache(
        self, url: str, audio_path: str, metadata: dict
    ) -> None:
        """Save a downloaded file and its metadata to the cache."""
        try:
            cache_base = self._get_cache_path(url)
            shutil.copy2(audio_path, cache_base.with_suffix(".mp3"))
            cache_base.with_suffix(".json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.logger.info(f"Cached download to: {cache_base.with_suffix('.mp3')}")
        except Exception as e:
            self.logger.warning(f"Failed to cache download: {e}")

    async def process_input(
        self, input_path: str, *, direct_1001_url: Optional[str] = None
    ):
        """Process input URL or file path.

        Args:
            input_path: URL or path to audio file
            direct_1001_url: Optional direct URL to 1001tracklists page
        """
        self._direct_1001_url = direct_1001_url
        try:
            # Validate input (URL or local file path)
            validated_result = validate_input(input_path)
            if validated_result is None:
                raise ValueError("Invalid URL or file path provided")

            validated_path, is_local_file = validated_result
            self.logger.info(f"Validated input: {validated_path}")

            # Store original URL for external source lookup
            if not is_local_file:
                self.original_url = validated_path

            if is_local_file:
                # Local file processing
                if not Path(validated_path).exists():
                    raise FileNotFoundError(f"Local file not found: {validated_path}")

                local_path = validated_path
                self.logger.info(f"Processing local file: {local_path}")

                # Set metadata from file name
                file_stem = Path(local_path).stem
                self.original_title = sanitizer(file_stem)
                self.uploader = "Unknown artist"
                self.duration = 0
            else:
                # URL processing — check cache first, then download
                cached = self._check_download_cache(validated_path)
                if cached:
                    local_path, metadata = cached
                    self.logger.info(
                        f"Using cached download (skipping download): {local_path}"
                    )
                    self.original_title = sanitizer(
                        metadata.get("title", Path(local_path).stem)
                    )
                    self.uploader = sanitizer(
                        metadata.get("uploader", "Unknown artist")
                    )
                    try:
                        self.duration = float(metadata.get("duration", 0))
                    except (TypeError, ValueError):
                        self.duration = 0
                else:
                    # Not cached — download the file
                    downloader = self.downloader_factory.create_downloader(
                        validated_path
                    )
                    if downloader is None:
                        raise ValueError("Failed to create downloader")
                    self.logger.info("Downloading audio...")
                    local_path = await downloader.download(validated_path)
                    if local_path is None:
                        raise ValueError("local_path cannot be None")
                    self.logger.info(f"Downloaded audio to: {local_path}")

                    # Store metadata for output
                    metadata = getattr(
                        downloader, "get_last_metadata", lambda: None
                    )()
                    if metadata:
                        self.logger.debug(
                            f"yt-dlp metadata keys: {list(metadata.keys())}"
                        )
                        self.original_title = sanitizer(
                            metadata.get("title", "")
                        )
                        self.uploader = sanitizer(
                            metadata.get("uploader", "")
                        )
                        try:
                            self.duration = float(
                                metadata.get("duration", 0)
                            )
                        except (TypeError, ValueError):
                            self.duration = 0
                    else:
                        self.logger.debug(
                            "No metadata available, using fallback values"
                        )
                        metadata = {}
                        self.original_title = sanitizer(
                            getattr(
                                downloader, "title", Path(local_path).stem
                            )
                        )
                        self.uploader = sanitizer(
                            getattr(
                                downloader, "uploader", "Unknown artist"
                            )
                        )
                        try:
                            self.duration = float(
                                getattr(downloader, "duration", 0)
                            )
                        except (TypeError, ValueError):
                            self.duration = 0
                        metadata = {
                            "title": self.original_title,
                            "uploader": self.uploader,
                            "duration": self.duration,
                        }

                    # Save to cache for future runs
                    cache_metadata = {
                        "title": metadata.get(
                            "title", self.original_title
                        ),
                        "uploader": metadata.get(
                            "uploader", self.uploader
                        ),
                        "duration": metadata.get(
                            "duration", self.duration
                        ),
                        "url": validated_path,
                    }
                    self._save_to_download_cache(
                        validated_path, local_path, cache_metadata
                    )

            self.logger.info("Processing audio...")

            # Process the downloaded file
            audio_segments = self.split_audio(local_path)
            if not audio_segments:
                raise ValueError("No audio segments were created")

            self.logger.info(f"Created {len(audio_segments)} audio segments")

            # Identify tracks in audio segments
            self.logger.info("Identifying tracks...")
            tracks = await self.identification_manager.identify_tracks(audio_segments)
            if not tracks:
                context = {
                    "segments_created": len(audio_segments),
                    "input_path": validated_path,
                    "file_duration": getattr(self, "duration", "unknown"),
                }
                raise TrackIdentificationError(
                    f"No tracks were identified in the audio file. "
                    f"Created {len(audio_segments)} segments but no matches found. "
                    f"This could be due to poor audio quality, instrumental music, "
                    f"or unsupported audio content.",
                    context=context,
                )

            self.logger.info(f"Identified {len(tracks)} tracks")
            self.logger.debug(f"Tracks: {tracks}")

            # Fetch external tracklists and merge with own results
            if self.original_url:
                self.logger.info("Fetching external tracklists for comparison...")
                try:
                    external_results = await self.external_fetcher.fetch_all(
                        url=self.original_url,
                        artist=self.uploader or "Unknown",
                        title=self.original_title or "Unknown",
                        direct_1001_url=getattr(self, "_direct_1001_url", None),
                    )

                    # Merge own tracks with external sources
                    merged_tracks = self.tracklist_merger.merge(tracks, external_results)

                    # Convert merged tracks back to Track objects for output
                    tracks = [mt.to_track() for mt in merged_tracks]

                    self.logger.info(
                        f"After merge: {len(tracks)} tracks "
                        f"(external sources: {', '.join(external_results.keys())})"
                    )
                except Exception as e:
                    self.logger.warning(
                        f"External source fetch/merge failed, using own results: {e}"
                    )
                    # Continue with own tracks only
            else:
                self.logger.debug(
                    "Skipping external sources (local file or no URL available)"
                )

            # Only save if we have identified tracks
            self.logger.info("Saving output...")
            if len(tracks) > 0:
                await self.save_output(
                    tracks,
                    self.config.output_format,
                    merger=self.tracklist_merger if self.original_url else None,
                )
            else:
                raise ValueError(
                    "No tracks were successfully identified with sufficient confidence"
                )

        except Exception as e:
            self.logger.error(f"Failed to process input: {e}")
            if self.config.debug:
                self.logger.error(traceback.format_exc())
            raise
        finally:
            # Always clean up temporary files
            await self.cleanup()

    def _get_input_hash(self, file_path: str) -> str:
        """Generate a short hash from the input file path or URL.

        Uses the original URL if available, otherwise the file path.
        This ensures segments from different inputs are stored separately.
        """
        source = self.original_url or file_path
        return hashlib.sha256(source.encode()).hexdigest()[:16]

    def split_audio(self, file_path: str) -> List[AudioSegment]:
        """Split audio file into overlapping segments for analysis."""
        self.logger.info(f"Splitting audio file: {file_path}")
        self.logger.debug(
            f"Config values: segment_length={self.config.segment_length}, "
            f"overlap_duration={self.config.overlap_duration}"
        )

        import os
        import subprocess
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path

        from mutagen._file import File

        audio = File(file_path)
        if audio is None:
            self.logger.error(f"Could not read audio file: {file_path}")
            return []

        try:
            duration = audio.info.length  # Duration in seconds

        except AttributeError:
            self.logger.error("Could not determine audio duration")
            return []

        # Get configuration for segmentation from instance
        segment_duration = self.config.segment_length
        overlap_duration = self.config.overlap_duration
        step = segment_duration - overlap_duration

        # Create input-specific temp directory so segments from different
        # inputs don't collide (segment filenames are generic like
        # segment_0_90.mp3 and would be reused from a previous run)
        input_hash = self._get_input_hash(file_path)
        temp_dir = Path(self.config.temp_dir) / input_hash
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._current_temp_dir = temp_dir
        self.logger.debug(f"Segment temp dir: {temp_dir} (input hash: {input_hash})")

        # Optimize ffmpeg settings for faster processing
        base_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",  # Force non-interactive mode
            "-loglevel",
            "error",
            "-i",
            file_path,
            "-vn",  # Skip video processing
            "-ar",
            "44100",  # Standard sample rate
            "-ac",
            "2",  # Stereo
            "-c:a",
            "libmp3lame",  # Use MP3 encoder
            "-q:a",
            "5",  # Variable bitrate quality (0-9, lower is better)
            "-map",
            "0:a",  # Only process audio stream
            "-threads",
            str(os.cpu_count()),  # Use all CPU cores
        ]

        # Generate segment parameters
        segment_params = []
        current_time = 0

        while current_time < duration:
            segment_length = min(segment_duration, duration - current_time)
            segment_file = (
                temp_dir / f"segment_{current_time:.0f}_{segment_length:.0f}.mp3"
            )

            # Add small padding to improve recognition
            start_time = max(0, current_time - 0.5)
            end_time = min(duration, current_time + segment_length + 0.5)
            actual_length = end_time - start_time

            segment_params.append(
                {
                    "start_time": current_time,
                    "length": segment_length,
                    "file": segment_file,
                    "cmd": base_cmd
                    + [
                        "-ss",
                        str(start_time),
                        "-t",
                        str(actual_length),
                        "-y",
                        str(segment_file),
                    ],
                }
            )

            current_time += step

        def create_segment(params):
            """Create a single audio segment using ffmpeg."""
            try:
                if params["file"].exists():
                    # Skip if file already exists and has content
                    if params["file"].stat().st_size > 1000:
                        return AudioSegment(
                            file_path=str(params["file"]),
                            start_time=int(params["start_time"]),
                            duration=int(params["length"]),
                        )

                result = subprocess.run(
                    params["cmd"], capture_output=True, text=True, check=True
                )
                self.logger.debug(f"FFmpeg output: {result.stdout}")

                if params["file"].exists() and params["file"].stat().st_size > 1000:
                    return AudioSegment(
                        file_path=str(params["file"]),
                        start_time=int(params["start_time"]),
                        duration=int(params["length"]),
                    )
                else:
                    self.logger.error(
                        f"Failed to create segment at {params['start_time']}s: "
                        f"Output file is missing or too small"
                    )
                    return None

            except subprocess.CalledProcessError as e:
                self.logger.error(
                    f"Failed to create segment at {params['start_time']}s: {e.stderr}"
                )
                return None

            except Exception as e:
                self.logger.error(
                    f"Error creating segment at {params['start_time']}s: {e}"
                )
                return None

        # Process segments in parallel using ThreadPoolExecutor
        segments = []
        try:
            # Ensure os.cpu_count() does not return None
            cpu_count = os.cpu_count()
            if cpu_count is None:
                raise ValueError("os.cpu_count() returned None")

            # Use more workers for better parallelization
            max_workers = min(cpu_count * 2, len(segment_params))
            self.logger.debug(f"Processing segments with {max_workers} workers")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks and gather results
                future_segments = list(executor.map(create_segment, segment_params))

                # Filter out failed segments
                segments = [seg for seg in future_segments if seg is not None]

            self.logger.info(f"Split audio into {len(segments)} segments")
            return segments

        except Exception as e:
            self.logger.error(f"Failed to process segments: {e}")
            return []

    async def save_output(
        self, tracks: List["Track"], format: str, merger=None
    ):
        """Save identified tracks to output files.

        Args:
            tracks: List of identified tracks
            format: Output format (json, markdown, m3u)
            merger: Optional TracklistMerger with raw data for comparison table

        Raises:
            ValueError: If tracks list is empty
        """
        if len(tracks) == 0:
            self.logger.error("Cannot save output: No tracks provided")
            return

        # Get title from the original title or use a default
        title = getattr(self, "original_title", None)
        if not title:
            # Try to construct a title from the first track
            if tracks and tracks[0].artist and tracks[0].song_name:
                title = f"{tracks[0].artist} - {tracks[0].song_name}"
            else:
                title = "Identified Mix"

        # Prepare mix info using the downloaded title
        mix_info = {
            "title": title,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "track_count": len(tracks),
        }

        try:
            # Create output handler
            output = TracklistOutput(mix_info=mix_info, tracks=tracks, merger=merger)

            # Save in specified format
            if format == "all":
                saved_files = output.save_all()
                if saved_files:
                    for file in saved_files:
                        self.logger.debug(f"Saved tracklist to: {file}")
                else:
                    self.logger.error("Failed to save tracklist in any format")
            else:
                if saved_file := output.save(format):
                    self.logger.info(f"Saved {format} tracklist to: {saved_file}")
                else:
                    self.logger.error(f"Failed to save tracklist in format: {format}")

        except Exception as e:
            self.logger.error(f"Error saving tracklist: {e}")
            if self.config.debug:
                self.logger.error(traceback.format_exc())

    async def cleanup(self):
        """Cleanup resources"""
        try:
            # Check if we should keep segments
            keep_segments = getattr(self.config, "keep_segments", False)
            # Use the input-specific temp dir if available, else the base temp dir
            temp_dir = getattr(self, "_current_temp_dir", None) or Path(self.config.temp_dir)

            if keep_segments and temp_dir.exists():
                self.logger.info(f"Keeping audio segments in: {temp_dir}")
            elif temp_dir.exists():
                # Clean up temp directory
                # First try to remove all files
                for file in temp_dir.glob("*"):
                    try:
                        if file.is_file():
                            file.unlink()
                            self.logger.debug(f"Removed temporary file: {file}")
                        elif file.is_dir():
                            import shutil

                            shutil.rmtree(file)
                            self.logger.debug(f"Removed temporary directory: {file}")
                    except Exception as e:
                        self.logger.warning(f"Failed to remove {file}: {e}")

                # Try to remove the directory itself
                try:
                    # Use rmtree instead of rmdir to handle any remaining files
                    import shutil

                    shutil.rmtree(temp_dir)
                    self.logger.debug("Removed temporary directory")
                except Exception as e:
                    self.logger.debug(f"Could not remove temp directory: {e}")
                    # If rmtree fails, try to at least remove empty directory
                    try:
                        temp_dir.rmdir()
                    except Exception:
                        pass
        except Exception as e:
            self.logger.warning(f"Error during cleanup: {e}")

        # Clean up other resources
        try:
            if hasattr(self.identification_manager, "close"):
                await self.identification_manager.close()
        except Exception as e:
            self.logger.warning(f"Error cleaning up identification manager: {e}")

    async def close(self):
        """Cleanup resources."""
        await self.cleanup()


class ApplicationError(Exception):
    """Base application error."""

    pass


class TrackIdentificationError(ApplicationError):
    """Raised when track identification fails or produces no results."""

    def __init__(self, message: str, context: dict = None):
        super().__init__(message)
        self.context = context or {}
