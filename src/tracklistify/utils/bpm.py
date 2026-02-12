"""
BPM analysis and consistency checking for audio segments.

Uses librosa for beat detection and flags BPM outliers
that might indicate incorrect track identification.
"""

import statistics
from typing import List, Optional

from tracklistify.utils.logger import get_logger

logger = get_logger(__name__)

# Try to import librosa — it's an optional dependency
try:
    import librosa

    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False
    logger.info(
        "librosa not installed. BPM analysis disabled. "
        "Install with: uv add librosa"
    )


class BPMAnalyzer:
    """Analyzes BPM of audio segments and detects outliers."""

    # Percentage deviation from median BPM to flag as outlier
    OUTLIER_THRESHOLD = 0.15  # 15%
    # Confidence penalty for BPM outliers
    OUTLIER_PENALTY = 20.0

    def __init__(self):
        self._available = LIBROSA_AVAILABLE

    @property
    def available(self) -> bool:
        """Check if BPM analysis is available."""
        return self._available

    def analyze_segment(self, file_path: str) -> Optional[float]:
        """Analyze BPM of a single audio segment.

        Args:
            file_path: Path to audio file

        Returns:
            Detected BPM as float, or None if analysis fails
        """
        if not self._available:
            return None

        try:
            # Load audio file (mono, 22050 Hz is sufficient for beat detection)
            y, sr = librosa.load(file_path, sr=22050, mono=True)

            # Estimate tempo
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)

            # librosa may return an array; extract scalar
            if hasattr(tempo, "__len__"):
                bpm = float(tempo[0]) if len(tempo) > 0 else None
            else:
                bpm = float(tempo)

            if bpm and bpm > 0:
                logger.debug(f"BPM for {file_path}: {bpm:.1f}")
                return round(bpm, 1)
            return None

        except Exception as e:
            logger.debug(f"BPM analysis failed for {file_path}: {e}")
            return None

    def detect_outliers(
        self, bpm_values: List[Optional[float]]
    ) -> List[bool]:
        """Detect BPM outliers in a list of BPM values.

        Uses median BPM of the set and flags values that deviate
        more than OUTLIER_THRESHOLD (15%) from the median.

        Args:
            bpm_values: List of BPM values (None for unknown)

        Returns:
            List of booleans — True means the track is a BPM outlier
        """
        # Filter out None values for median calculation
        valid_bpms = [b for b in bpm_values if b is not None and b > 0]

        if len(valid_bpms) < 3:
            # Not enough data to determine outliers
            return [False] * len(bpm_values)

        median_bpm = statistics.median(valid_bpms)
        logger.info(f"Median BPM of set: {median_bpm:.1f}")

        outliers = []
        for bpm in bpm_values:
            if bpm is None or bpm <= 0:
                outliers.append(False)  # Can't determine
            else:
                deviation = abs(bpm - median_bpm) / median_bpm
                is_outlier = deviation > self.OUTLIER_THRESHOLD
                if is_outlier:
                    logger.debug(
                        f"BPM outlier: {bpm:.1f} "
                        f"(median: {median_bpm:.1f}, "
                        f"deviation: {deviation:.1%})"
                    )
                outliers.append(is_outlier)

        return outliers
