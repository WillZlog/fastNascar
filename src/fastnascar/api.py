"""Public NASCAR feed transport, with bounded retries and expiring cache."""
import hashlib
import json
import os
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .cache import Cache
from .series import resolve_series
from .exceptions import DataNotAvailableError, InvalidDataError

BASE_URL = "https://cf.nascar.com"


class NASCARClient:
    """Fetch NASCAR JSON feeds using the process-wide :class:`Cache` settings."""

    def __init__(self, *, timeout=20, retries=2):
        """Create a client with a per-request timeout and bounded retry count.

        Args:
            timeout (float): Socket timeout in seconds. Must be positive.
            retries (int): Additional attempts after a transient failure.

        Raises:
            ValueError: If timeout is nonpositive or retries is negative.
        """
        if timeout <= 0 or retries < 0:
            raise ValueError("timeout must be positive and retries nonnegative")
        self.timeout, self.retries = timeout, retries

    def get_json(self, path, *, ttl=3600, refresh=False, cache=True):
        """Fetch and decode a path relative to ``https://cf.nascar.com``.

        Args:
            path (str): Relative feed path, with or without a leading slash.
            ttl (float): Maximum cached response age in seconds.
            refresh (bool): Fetch again even when a cache entry is fresh.
            cache (bool): Allow reads and writes to the configured disk cache.

        Returns:
            dict or list: Decoded JSON from the feed or cache.

        Raises:
            DataNotAvailableError: HTTP/network failure or an offline cache miss.
            InvalidDataError: Invalid response JSON or corrupt offline cache data.
            OSError: The response could not be written to the configured cache.

        Offline mode ignores ``ttl`` and ``refresh``. Requests with ``cache=False``
        cannot run offline. Retries apply to network errors and HTTP 429/5xx
        responses, with a short exponential delay between attempts.
        """
        url = BASE_URL + "/" + path.lstrip("/")
        filename = None
        if cache and Cache._path:
            filename = Cache._path / ("fastnascar-" + hashlib.sha256(url.encode()).hexdigest() + ".json")
            if filename.exists() and (Cache._offline or (not refresh and time.time() - filename.stat().st_mtime < ttl)):
                try:
                    return json.loads(filename.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    if Cache._offline:
                        raise InvalidDataError(f"Invalid cached JSON: {filename}") from exc
        if Cache._offline:
            raise DataNotAvailableError(f"Offline cache miss: {url}")
        for attempt in range(self.retries + 1):
            try:
                request = Request(url, headers={"User-Agent": "fastnascar/0.1", "Accept": "application/json"})
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                break
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise DataNotAvailableError(f"NASCAR feed returned HTTP {exc.code}: {url}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt == self.retries:
                    raise DataNotAvailableError(f"Could not fetch NASCAR feed: {url}") from exc
            except ValueError as exc:
                raise InvalidDataError(f"NASCAR feed returned invalid JSON: {url}") from exc
            time.sleep(min(0.5 * 2**attempt, 4))
        if filename:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=filename.parent, delete=False) as file:
                    temporary = file.name
                    json.dump(payload, file)
                os.replace(temporary, filename)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        return payload

    def schedule(self, year, *, refresh=False):
        """Return a season's raw schedule JSON, including all available series.

        Args:
            year (int): Season year.
            refresh (bool): Bypass a fresh cache entry.

        Returns:
            dict: Upstream schedule object, usually keyed by ``series_1`` etc.

        Raises:
            DataNotAvailableError: The feed is unavailable.
            InvalidDataError: The response is not valid JSON.
        """
        return self.get_json(f"cacher/{year}/race_list_basic.json", refresh=refresh)

    def weekend(self, year, race_id, *, series=1, refresh=False):
        """Return raw race and practice/qualifying results for a weekend.

        Args:
            year (int): Season year.
            race_id (int): NASCAR race ID, not the championship round.
            series (int or str): Cup/1, Xfinity/2, or Trucks/3.
            refresh (bool): Bypass a fresh cache entry.

        Returns:
            dict: Feed containing ``weekend_race`` and ``weekend_runs``.

        Raises:
            DataNotAvailableError: The feed is unavailable.
            InvalidDataError: The response is not valid JSON.
        """
        return self.get_json(f"cacher/{year}/{resolve_series(series)}/{race_id}/weekend-feed.json", refresh=refresh)

    def laps(self, year, race_id, *, series=1, refresh=False):
        """Return raw race laps and leader-lap flags.

        Args:
            year (int): Season year.
            race_id (int): NASCAR race ID.
            series (int or str): Cup/1, Xfinity/2, or Trucks/3.
            refresh (bool): Bypass a fresh cache entry.

        Returns:
            dict: Feed containing car records under ``laps`` and optional ``flags``.

        Raises:
            DataNotAvailableError: The feed is unavailable.
            InvalidDataError: The response is not valid JSON.
        """
        return self.get_json(f"cacher/{year}/{resolve_series(series)}/{race_id}/lap-times.json", refresh=refresh)

    def pit_stops(self, year, race_id, *, series=1, refresh=False):
        """Fetch raw pit timing records for a race.

        Args:
            year (int): Season year.
            race_id (int): NASCAR race ID.
            series (int or str): Cup/1, Xfinity/2, or Trucks/3.
            refresh (bool): Bypass a fresh cache entry.

        Returns:
            list of dict: Source pit-road and service timing records.

        Raises:
            DataNotAvailableError: The feed is unavailable.
            InvalidDataError: The response is not valid JSON.
        """
        return self.get_json(f"cacher/{year}/{resolve_series(series)}/{race_id}/live-pit-data.json", refresh=refresh)

    def notes(self, year, race_id, *, series=1, refresh=False):
        """Fetch raw race commentary keyed by leader lap.

        Args:
            year (int): Season year.
            race_id (int): NASCAR race ID.
            series (int or str): Cup/1, Xfinity/2, or Trucks/3.
            refresh (bool): Bypass a fresh cache entry.

        Returns:
            dict: Source commentary with a ``laps`` mapping.

        Raises:
            DataNotAvailableError: The feed is unavailable.
            InvalidDataError: The response is not valid JSON.
        """
        return self.get_json(f"cacher/{year}/{resolve_series(series)}/{race_id}/lap-notes.json", refresh=refresh)

    def live(self):
        """Return the current raw live feed without caching.

        Returns:
            dict: Latest upstream payload; it may belong to any NASCAR series.

        Raises:
            DataNotAvailableError: A request fails or offline mode is enabled.
            InvalidDataError: The response is not valid JSON.

        Use ``get_live_session`` to validate that the feed serves the required series.
        """
        return self.get_json("live/feeds/live-feed.json", cache=False)
