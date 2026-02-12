"""
External tracklist source fetchers for set79.com and 1001tracklists.com.

These fetchers query external websites to find existing tracklists for
comparison and merging with our own identification results.
"""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import quote, urljoin

import aiohttp
from bs4 import BeautifulSoup

from tracklistify.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ExternalTrack:
    """Represents a track from an external tracklist source."""

    title: str
    artist: str
    time_in_mix: str  # HH:MM:SS format
    source: str  # "set79", "1001tracklists", etc.
    label: Optional[str] = None
    position: Optional[int] = None

    def __str__(self) -> str:
        return f"[{self.time_in_mix}] {self.artist} - {self.title} ({self.source})"


@dataclass
class ExternalSourceResult:
    """Result from an external source fetch."""

    source: str
    tracks: List[ExternalTrack] = field(default_factory=list)
    url: Optional[str] = None
    error: Optional[str] = None
    success: bool = True


class ExternalSourceFetcher:
    """Fetches tracklists from external sources like set79 and 1001tracklists."""

    def __init__(self, timeout: int = 30):
        """Initialize the fetcher.

        Args:
            timeout: HTTP request timeout in seconds
        """
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

    async def search_set79(self, url: str) -> ExternalSourceResult:
        """Fetch tracklist from set79.com for a given source URL.

        set79 uses URL-encoded source URLs to look up tracklists.
        Example: set79.com/tracklist/soundcloud.com%2Fartist%2Fmix-name

        Args:
            url: The original mix URL (SoundCloud, YouTube, etc.)

        Returns:
            ExternalSourceResult with tracks or error information
        """
        result = ExternalSourceResult(source="set79")

        try:
            # Extract the domain and path from the URL for set79 lookup
            # set79 uses the URL path without protocol
            clean_url = re.sub(r"^https?://", "", url)
            encoded_url = quote(clean_url, safe="")
            set79_url = f"https://set79.com/tracklist/{encoded_url}"
            result.url = set79_url

            logger.info(f"Fetching set79 tracklist: {set79_url}")

            async with aiohttp.ClientSession(
                timeout=self.timeout, headers=self.headers
            ) as session:
                async with session.get(set79_url) as response:
                    if response.status == 404:
                        result.success = False
                        result.error = "Tracklist not found on set79"
                        logger.debug(f"set79: No tracklist found for {url}")
                        return result

                    if response.status != 200:
                        result.success = False
                        result.error = f"HTTP {response.status}"
                        logger.warning(f"set79: HTTP {response.status} for {url}")
                        return result

                    html = await response.text()
                    result.tracks = self._parse_set79_html(html)

                    if result.tracks:
                        logger.info(
                            f"set79: Found {len(result.tracks)} tracks for {url}"
                        )
                    else:
                        result.success = False
                        result.error = "No tracks parsed from page"
                        logger.debug(f"set79: Could not parse tracks from {url}")

        except asyncio.TimeoutError:
            result.success = False
            result.error = "Request timeout"
            logger.warning(f"set79: Timeout fetching {url}")
        except aiohttp.ClientError as e:
            result.success = False
            result.error = str(e)
            logger.warning(f"set79: Client error for {url}: {e}")
        except Exception as e:
            result.success = False
            result.error = str(e)
            logger.error(f"set79: Unexpected error for {url}: {e}")

        return result

    def _parse_set79_html(self, html: str) -> List[ExternalTrack]:
        """Parse set79 HTML page to extract tracks.

        Args:
            html: The HTML content from set79

        Returns:
            List of ExternalTrack objects
        """
        tracks = []
        soup = BeautifulSoup(html, "html.parser")

        # set79 uses a table with tr.track-row elements, each containing:
        #   td (position), td.track-name-cell (title-artist), td (play-button time), td (links)
        track_rows = soup.select("tr.track-row")

        if not track_rows:
            # Fallback: try broader selectors for older/different layouts
            track_rows = soup.select(
                "table.tracklist tr, .tracklist-item, [data-track-start]"
            )

        if not track_rows:
            # Last resort: look for any structured content
            track_rows = soup.select(
                "[data-track], .track, .tracklist li, .song, .track-entry"
            )

        for idx, row in enumerate(track_rows, 1):
            try:
                track = self._parse_set79_row(row, idx)
                if track:
                    tracks.append(track)
            except Exception as e:
                logger.debug(f"set79: Error parsing row {idx}: {e}")
                continue

        return tracks

    def _parse_set79_row(
        self, row: BeautifulSoup, position: int
    ) -> Optional[ExternalTrack]:
        """Parse a single row/item from set79 tracklist.

        set79 HTML structure per track row:
          <tr class="track-row" data-track-start="156.0">
            <td>1</td>                                         <!-- position -->
            <td class="track-name-cell">
              <a class="track-name-link">Title - Artist</a>   <!-- known track -->
              <span class="unknown-track">Unknown</span>       <!-- unknown track -->
            </td>
            <td><a class="play-button">▶ 00:02:36</a></td>    <!-- timestamp -->
            <td>...</td>                                       <!-- external links -->
          </tr>

        Note: set79 uses "Title - Artist" format (reversed from standard).

        Args:
            row: BeautifulSoup element representing a track row
            position: Track position in the list

        Returns:
            ExternalTrack or None if parsing fails
        """
        # Skip unknown/unidentified tracks
        unknown_elem = row.select_one(".unknown-track")
        if unknown_elem:
            return None

        # --- Extract track name (Title - Artist) ---
        track_name = None

        # Primary: look for the track name link (set79 known tracks)
        name_link = row.select_one("a.track-name-link")
        if name_link:
            track_name = name_link.get_text(strip=True)

        # Fallback: look for the track-name-cell td
        if not track_name:
            name_cell = row.select_one("td.track-name-cell, td[itemprop='name']")
            if name_cell:
                # Get direct text, ignoring nested link/button noise
                track_name = name_cell.get_text(strip=True)

        if not track_name:
            return None

        # Skip partially identified tracks (e.g. "Unknown track of I Wish -")
        if re.match(r"(?i)^unknown\b", track_name):
            return None

        # --- Extract timestamp ---
        time_str = "00:00:00"

        # Primary: use data-track-start attribute (seconds as float)
        data_start = row.get("data-track-start")
        if data_start:
            try:
                total_seconds = int(float(data_start))
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            except (ValueError, TypeError):
                pass

        # Fallback: parse from play-button text (▶ HH:MM:SS)
        if time_str == "00:00:00":
            play_btn = row.select_one("a.play-button")
            if play_btn:
                btn_text = play_btn.get_text(strip=True)
                # Remove ▶ prefix and extract time
                time_match = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)", btn_text)
                if time_match:
                    time_str = self._normalize_time(time_match.group(1))

        # --- Split "Title - Artist" into separate fields ---
        # set79 uses "Title - Artist" format; split on the LAST " - "
        # to handle titles that may contain " - "
        artist = ""
        title = track_name

        if " - " in track_name:
            parts = track_name.rsplit(" - ", 1)
            title = parts[0].strip()
            artist = parts[1].strip()
        elif " – " in track_name:  # en-dash variant
            parts = track_name.rsplit(" – ", 1)
            title = parts[0].strip()
            artist = parts[1].strip()

        # Clean up label if present in brackets at end of title
        label = None
        label_match = re.search(r"\[([^\]]+)\]\s*$", title)
        if label_match:
            label = label_match.group(1)
            title = title[: label_match.start()].strip()

        if title:
            return ExternalTrack(
                title=title,
                artist=artist,
                time_in_mix=time_str,
                source="set79",
                label=label,
                position=position,
            )

        return None

    async def search_1001tracklists(
        self, artist: str, title: str
    ) -> ExternalSourceResult:
        """Search 1001tracklists.com for a tracklist matching artist and title.

        Uses web scraping to search and parse results.

        Args:
            artist: Artist/DJ name
            title: Mix/set title or event name

        Returns:
            ExternalSourceResult with tracks or error information
        """
        result = ExternalSourceResult(source="1001tracklists")

        try:
            # Build search query - avoid duplicating artist if already in title
            artist_lower = artist.lower().strip()
            title_lower = title.lower().strip()

            if artist_lower and artist_lower in title_lower:
                # Artist is already part of the title, just use title
                query = title.strip()
            else:
                query = f"{artist} {title}".strip()

            # Clean up the query - remove special chars that might break search
            query = re.sub(r"[^\w\s\-]", " ", query)
            query = re.sub(r"\s+", " ", query).strip()

            search_url = f"https://www.1001tracklists.com/search/result.php?search_selection=1&main_search={quote(query)}"
            result.url = search_url

            logger.info(f"Searching 1001tracklists for: {query}")

            async with aiohttp.ClientSession(
                timeout=self.timeout, headers=self.headers
            ) as session:
                # First, search for the tracklist
                async with session.get(search_url) as response:
                    if response.status != 200:
                        result.success = False
                        result.error = f"Search HTTP {response.status}"
                        logger.warning(
                            f"1001tracklists: Search returned {response.status}"
                        )
                        return result

                    html = await response.text()
                    tracklist_url = self._find_1001_tracklist_url(html, artist)

                    if not tracklist_url:
                        result.success = False
                        result.error = "No matching tracklist found"
                        logger.info(
                            f"1001tracklists: No results found for '{query}'"
                        )
                        return result

                    # Fetch the actual tracklist page
                    result.url = tracklist_url
                    async with session.get(tracklist_url) as tl_response:
                        if tl_response.status != 200:
                            result.success = False
                            result.error = f"Tracklist HTTP {tl_response.status}"
                            return result

                        tl_html = await tl_response.text()
                        result.tracks = self._parse_1001_tracklist(tl_html)

                        if result.tracks:
                            logger.info(
                                f"1001tracklists: Found {len(result.tracks)} tracks"
                            )
                        else:
                            result.success = False
                            result.error = "No tracks parsed from page"

        except asyncio.TimeoutError:
            result.success = False
            result.error = "Request timeout"
            logger.warning("1001tracklists: Timeout")
        except aiohttp.ClientError as e:
            result.success = False
            result.error = str(e)
            logger.warning(f"1001tracklists: Client error: {e}")
        except Exception as e:
            result.success = False
            result.error = str(e)
            logger.error(f"1001tracklists: Unexpected error: {e}")

        return result

    def _find_1001_tracklist_url(
        self, html: str, artist: str
    ) -> Optional[str]:
        """Find the best matching tracklist URL from search results.

        Args:
            html: Search results HTML
            artist: Artist name to match

        Returns:
            Full URL to the tracklist page, or None
        """
        soup = BeautifulSoup(html, "html.parser")

        # Look for tracklist links in search results
        # 1001tracklists uses various selectors for search results
        links = soup.select("a[href*='/tracklist/']")

        logger.debug(f"1001tracklists: Found {len(links)} tracklist links in search results")

        if not links:
            # Try alternative selectors - the site might use different HTML structure
            links = soup.select(".tlLink a, .searchResultItem a, .result a")
            logger.debug(f"1001tracklists: Alternative search found {len(links)} links")

        if not links:
            # Log a sample of the HTML for debugging
            title_elem = soup.select_one("title")
            logger.debug(
                f"1001tracklists: Page title: {title_elem.get_text() if title_elem else 'N/A'}"
            )
            # Check if we got a "no results" page
            no_results = soup.select_one(".noResults, .no-results, .empty-results")
            if no_results:
                logger.info("1001tracklists: Search returned no results page")
            return None

        artist_lower = artist.lower()

        for link in links:
            href = link.get("href", "")
            text = link.get_text(strip=True).lower()

            # Check if artist name appears in the link text or URL
            if artist_lower in text or artist_lower.replace(" ", "-") in href.lower():
                # Make sure it's a full URL
                if href.startswith("/"):
                    found_url = urljoin("https://www.1001tracklists.com", href)
                    logger.info(f"1001tracklists: Found matching URL: {found_url}")
                    return found_url
                elif href.startswith("http"):
                    logger.info(f"1001tracklists: Found matching URL: {href}")
                    return href

        # If no artist match, return the first tracklist link
        if links:
            href = links[0].get("href", "")
            if href.startswith("/"):
                found_url = urljoin("https://www.1001tracklists.com", href)
                logger.info(f"1001tracklists: Using first result: {found_url}")
                return found_url
            elif href.startswith("http"):
                logger.info(f"1001tracklists: Using first result: {href}")
                return href

        return None

    def _parse_1001_tracklist(self, html: str) -> List[ExternalTrack]:
        """Parse 1001tracklists.com tracklist page.

        Args:
            html: Tracklist page HTML

        Returns:
            List of ExternalTrack objects
        """
        tracks = []
        soup = BeautifulSoup(html, "html.parser")

        # Log page info for debugging
        title_elem = soup.select_one("title")
        logger.debug(f"1001tracklists: Page title: {title_elem.get_text() if title_elem else 'N/A'}")

        # 1001tracklists track items have class .tlpItem and contain Schema.org data
        # They have IDs like "tlp_7890363" and data attributes
        track_items = soup.select(".tlpItem")

        if track_items:
            logger.debug(f"1001tracklists: Found {len(track_items)} .tlpItem elements")
        else:
            # Fallback selectors for different page versions
            fallback_selectors = [
                "[id^='tlp_'][class*='tlpItem']",  # ID + class combo
                "div[data-trackid]",               # Elements with track data
                "[itemtype='http://schema.org/MusicRecording']",  # Schema.org items
            ]
            for selector in fallback_selectors:
                track_items = soup.select(selector)
                if track_items:
                    logger.debug(f"1001tracklists: Found {len(track_items)} items with '{selector}'")
                    break

        if not track_items:
            logger.warning(f"1001tracklists: No track items found. HTML length: {len(html)}")
            # Check for bot protection page
            if "Please wait" in html and "forwarded" in html:
                logger.warning("1001tracklists: Page appears to be bot protection page")
            return []

        # Parse each track, using data-trno for position if available
        for item in track_items:
            try:
                # Get track number from data attribute if available
                trno = item.get("data-trno")
                if trno is not None:
                    try:
                        position = int(trno) + 1  # data-trno is 0-indexed
                    except ValueError:
                        position = len(tracks) + 1
                else:
                    position = len(tracks) + 1

                track = self._parse_1001_track_item(item, position)
                if track:
                    tracks.append(track)
            except Exception as e:
                logger.debug(f"1001tracklists: Error parsing item: {e}")
                continue

        return tracks

    def _parse_1001_track_item(
        self, item: BeautifulSoup, position: int
    ) -> Optional[ExternalTrack]:
        """Parse a single track item from 1001tracklists.

        1001tracklists uses Schema.org MusicRecording annotations:
        - <meta itemprop="name" content="Artist - Title"/>
        - <meta itemprop="byArtist" content="Artist"/>
        - <meta itemprop="publisher" content="Label HTML"/>
        - Hidden input with cue time in seconds

        Args:
            item: BeautifulSoup element for the track
            position: Track position

        Returns:
            ExternalTrack or None
        """
        artist = ""
        title = ""
        time_str = "00:00:00"
        label = None

        # === Method 1: Schema.org meta tags (most reliable) ===
        name_meta = item.select_one("meta[itemprop='name']")
        artist_meta = item.select_one("meta[itemprop='byArtist']")

        if name_meta and name_meta.get("content"):
            full_name = name_meta.get("content", "")
            # Format is "Artist - Title"
            if " - " in full_name:
                parts = full_name.split(" - ", 1)
                artist = parts[0].strip()
                title = parts[1].strip()
            else:
                title = full_name.strip()

        # Use byArtist if available (more accurate for multi-artist tracks)
        if artist_meta and artist_meta.get("content"):
            artist = artist_meta.get("content", "").strip()

        # === Get cue time from hidden input ===
        cue_input = item.select_one("input[id$='_cue_seconds']")
        if cue_input:
            try:
                cue_seconds = int(float(cue_input.get("value", "0")))
                hours = cue_seconds // 3600
                minutes = (cue_seconds % 3600) // 60
                seconds = cue_seconds % 60
                time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            except (ValueError, TypeError):
                pass

        # === Get label from trackLabel element ===
        label_elem = item.select_one(".trackLabel a, .trackLabel")
        if label_elem:
            label = label_elem.get_text(strip=True)

        # === Method 2: Fallback to visible text elements ===
        if not (artist and title):
            # Try trackValue span which contains artist and title
            track_value = item.select_one(".trackValue")
            if track_value:
                text = track_value.get_text(separator=" ", strip=True)
                # Clean up the text
                text = re.sub(r"\s+", " ", text).strip()
                if " - " in text:
                    parts = text.split(" - ", 1)
                    artist = parts[0].strip()
                    title = parts[1].strip()

        # Clean up any remaining noise in title
        if title:
            # Remove common suffixes like "(Original Mix)" variations
            # but keep remix info
            title = re.sub(r"\s+$", "", title)

        if artist and title:
            return ExternalTrack(
                title=title,
                artist=artist,
                time_in_mix=time_str,
                source="1001tracklists",
                label=label,
                position=position,
            )

        return None

    def _normalize_time(self, time_str: str) -> str:
        """Normalize time string to HH:MM:SS format.

        Args:
            time_str: Time string in various formats (M:SS, MM:SS, H:MM:SS, etc.)

        Returns:
            Time in HH:MM:SS format
        """
        if not time_str:
            return "00:00:00"

        # Remove any non-time characters
        time_str = re.sub(r"[^\d:]", "", time_str)

        parts = time_str.split(":")
        if len(parts) == 2:
            # MM:SS format
            minutes = int(parts[0])
            seconds = int(parts[1])
            hours = minutes // 60
            minutes = minutes % 60
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        elif len(parts) == 3:
            # HH:MM:SS format
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"
        else:
            return "00:00:00"

    def parse_1001tracklists_html_file(self, html_path: str) -> ExternalSourceResult:
        """Parse a locally saved 1001tracklists HTML file.

        Use this when the website has bot protection that prevents direct fetching.
        The user can save the page from their browser and provide the HTML file path.

        Args:
            html_path: Path to the saved HTML file

        Returns:
            ExternalSourceResult with tracks or error information
        """
        from pathlib import Path

        result = ExternalSourceResult(source="1001tracklists", url=f"file://{html_path}")

        try:
            path = Path(html_path)
            if not path.exists():
                result.success = False
                result.error = f"File not found: {html_path}"
                return result

            logger.info(f"Parsing 1001tracklists from local file: {html_path}")
            html = path.read_text(encoding="utf-8")
            logger.debug(f"1001tracklists: Read {len(html)} bytes from file")

            result.tracks = self._parse_1001_tracklist(html)

            if result.tracks:
                logger.info(
                    f"1001tracklists: Found {len(result.tracks)} tracks (from file)"
                )
            else:
                result.success = False
                result.error = "No tracks parsed from HTML file"

        except Exception as e:
            result.success = False
            result.error = str(e)
            logger.error(f"1001tracklists: Error reading file: {e}")

        return result

    async def fetch_1001tracklists_direct(
        self, tracklist_url: str
    ) -> ExternalSourceResult:
        """Directly fetch a tracklist from a known 1001tracklists URL.

        Use this when you already have the exact 1001tracklists URL.
        Note: 1001tracklists.com uses Cloudflare bot protection. If this fails,
        use parse_1001tracklists_html_file() with a locally saved HTML file instead.

        Args:
            tracklist_url: Full URL to the 1001tracklists page

        Returns:
            ExternalSourceResult with tracks or error information
        """
        result = ExternalSourceResult(source="1001tracklists", url=tracklist_url)

        try:
            logger.info(f"Fetching 1001tracklists directly: {tracklist_url}")

            # Use more complete browser headers for 1001tracklists
            headers_1001 = {
                **self.headers,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }

            async with aiohttp.ClientSession(
                timeout=self.timeout, headers=headers_1001
            ) as session:
                async with session.get(tracklist_url) as response:
                    # Accept 200 and 206 (Partial Content) as successful
                    if response.status not in (200, 206):
                        result.success = False
                        result.error = f"HTTP {response.status}"
                        logger.warning(
                            f"1001tracklists: Direct fetch returned {response.status}"
                        )
                        return result

                    html = await response.text()
                    logger.debug(f"1001tracklists: Received {len(html)} bytes")
                    result.tracks = self._parse_1001_tracklist(html)

                    if result.tracks:
                        logger.info(
                            f"1001tracklists: Found {len(result.tracks)} tracks (direct)"
                        )
                    else:
                        result.success = False
                        result.error = "No tracks parsed from page"

        except asyncio.TimeoutError:
            result.success = False
            result.error = "Request timeout"
            logger.warning("1001tracklists: Timeout on direct fetch")
        except aiohttp.ClientError as e:
            result.success = False
            result.error = str(e)
            logger.warning(f"1001tracklists: Client error: {e}")
        except Exception as e:
            result.success = False
            result.error = str(e)
            logger.error(f"1001tracklists: Unexpected error: {e}")

        return result

    async def fetch_all(
        self, url: str, artist: str, title: str,
        direct_1001_url: Optional[str] = None
    ) -> Dict[str, ExternalSourceResult]:
        """Fetch tracklists from all available external sources in parallel.

        Args:
            url: Original mix URL
            artist: Artist/DJ name
            title: Mix/set title
            direct_1001_url: Optional direct URL or local HTML file path for 1001tracklists
                             (skips search). If path ends with .html/.htm, reads from file.

        Returns:
            Dictionary mapping source name to ExternalSourceResult
        """
        logger.info(f"Fetching external tracklists for: {artist} - {title}")

        # Decide how to get 1001tracklists data
        tl_1001_result: Optional[ExternalSourceResult] = None

        if direct_1001_url:
            # Check if it's a local HTML file
            if direct_1001_url.endswith(('.html', '.htm')) and not direct_1001_url.startswith('http'):
                # Synchronously parse local file
                tl_1001_result = self.parse_1001tracklists_html_file(direct_1001_url)
                tl_1001_coro = None
            else:
                tl_1001_coro = self.fetch_1001tracklists_direct(direct_1001_url)
        else:
            tl_1001_coro = self.search_1001tracklists(artist, title)

        external_results: Dict[str, ExternalSourceResult] = {}

        # Run fetchers - 1001tracklists might already be resolved from file
        if tl_1001_coro is not None:
            # Run both fetchers in parallel
            results = await asyncio.gather(
                self.search_set79(url),
                tl_1001_coro,
                return_exceptions=True,
            )

            # Process set79 result
            if isinstance(results[0], ExternalSourceResult):
                external_results["set79"] = results[0]
            else:
                external_results["set79"] = ExternalSourceResult(
                    source="set79",
                    success=False,
                    error=str(results[0]),
                )

            # Process 1001tracklists result
            if isinstance(results[1], ExternalSourceResult):
                external_results["1001tracklists"] = results[1]
            else:
                external_results["1001tracklists"] = ExternalSourceResult(
                    source="1001tracklists",
                    success=False,
                    error=str(results[1]),
                )
        else:
            # 1001tracklists already loaded from file, just fetch set79
            set79_result = await self.search_set79(url)
            external_results["set79"] = set79_result
            external_results["1001tracklists"] = tl_1001_result  # type: ignore

        # Log summary
        for source, result in external_results.items():
            if result.success and result.tracks:
                logger.info(f"  {source}: {len(result.tracks)} tracks found")
            elif result.error:
                logger.debug(f"  {source}: {result.error}")
            else:
                logger.debug(f"  {source}: No tracks found")

        return external_results
