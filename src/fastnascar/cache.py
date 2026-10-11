"""Opt-in disk cache of JSON responses; no executable pickle data."""
from pathlib import Path


class Cache:
    """Configure the shared disk cache used by all NASCAR clients."""
    _path: Path | None = None
    _offline = False

    @classmethod
    def enable_cache(cls, cache_dir, *, offline=False):
        """Use a directory for cached JSON responses, creating it if needed.

        Args:
            cache_dir (str or pathlib.Path): Cache directory. ``~`` is expanded.
            offline (bool): Read cached data only, regardless of its age. Cache
                misses and live requests raise ``DataNotAvailableError``.

        Returns:
            None

        Raises:
            OSError: The directory cannot be created or accessed.

        Settings apply to every client in the current Python process. Existing
        files are retained when switching directories or leaving offline mode.
        """
        path = Path(cache_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        cls._path, cls._offline = path, offline

    @classmethod
    def disable_cache(cls):
        """Disable disk caching and offline mode without deleting cached files.

        Returns:
            None
        """
        cls._path, cls._offline = None, False

    @classmethod
    def clear_cache(cls):
        """Delete ``fastnascar-*.json`` files from the configured cache directory.

        Returns:
            None: Does nothing if disk caching is disabled.

        Raises:
            OSError: A matching file cannot be removed.

        Other files are retained. Avoid clearing while another process writes
        responses to the same directory.
        """
        if cls._path:
            for path in cls._path.glob("fastnascar-*.json"):
                path.unlink()
