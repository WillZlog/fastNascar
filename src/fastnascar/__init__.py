"""FastNASCAR: NASCAR race data and pace analysis in pandas."""
from .api import NASCARClient
from .cache import Cache
from .core import Laps, Session, get_event, get_event_schedule, get_session
from .exceptions import (AmbiguousEventError, DataNotAvailableError, EventNotFoundError,
                         FastNASCARException, InvalidDataError, SessionNotLoadedError)
from .live import LiveSnapshot, get_live_session, iter_live, record_live, replay_live
from .live import LiveHistory
from .analysis import rolling_pace, best_lap_average, compare_drivers, summarize_stints, flag_periods
from .season import get_season_results, driver_standings

__version__ = "0.2.0"
__all__ = ["Cache", "NASCARClient", "Laps", "Session", "get_event", "get_event_schedule",
           "get_session", "LiveSnapshot", "get_live_session", "iter_live", "record_live", "replay_live",
           "FastNASCARException", "DataNotAvailableError", "InvalidDataError", "SessionNotLoadedError",
           "EventNotFoundError", "AmbiguousEventError", "LiveHistory", "rolling_pace",
           "best_lap_average", "compare_drivers", "summarize_stints", "flag_periods",
           "get_season_results", "driver_standings"]
