"""FastNASCAR session objects and pandas lap selectors."""
from numbers import Integral

import pandas as pd

from .api import NASCARClient
from ._time import seconds_to_timedelta
from .series import resolve_series
from .exceptions import (AmbiguousEventError, DataNotAvailableError,
                         EventNotFoundError, InvalidDataError, SessionNotLoadedError)

LAP_COLUMNS = ["Driver", "DriverNumber", "DriverId", "LapNumber", "LapTime", "Speed", "Position"]
RESULT_COLUMNS = ["DriverNumber", "DriverId", "FullName", "TeamName", "Manufacturer",
                  "Position", "GridPosition", "Laps", "LapsLed", "Points", "Status", "BestLapTime"]


class Laps(pd.DataFrame):
    """One row per completed car lap. Speed is lap-average mph."""
    @property
    def _constructor(self):
        """Preserve the Laps subclass when pandas constructs a filtered table."""
        return Laps

    def pick_drivers(self, identifiers):
        """Select one or more drivers by exact full name or car number.

        Args:
            identifiers (str, int, or iterable): Names or car numbers. Names are
                matched without regard to case; number strings retain leading zeros.

        Returns:
            Laps: Matching rows in their original order. Unknown identifiers
            produce an empty selection.
        """
        if isinstance(identifiers, (str, Integral)):
            identifiers = [identifiers]
        values = {str(value).casefold() for value in identifiers}
        return self.loc[self.DriverNumber.astype(str).str.casefold().isin(values)
                        | self.Driver.str.casefold().isin(values)]

    def pick_driver(self, identifier):
        """Select a single driver by exact full name or car number.

        Args:
            identifier (str or int): Full name or car number; for example ``"77"``.

        Returns:
            Laps: Matching rows, or an empty selection if the driver is absent.

        Lap-feed names may include a playoff suffix such as ``(C)``. Select by
        number when the full name differs from the results feed.
        """
        return self.pick_drivers(identifier)

    def pick_laps(self, numbers):
        """Select completed car laps by lap number.

        Args:
            numbers (int or iterable of int): One or more car lap numbers.

        Returns:
            Laps: Matching rows across all cars in the current selection.
        """
        if isinstance(numbers, Integral):
            numbers = [numbers]
        return self.loc[self.LapNumber.isin(numbers)]

    def pick_fastest(self):
        """Return the row with the shortest positive, nonmissing lap time.

        Returns:
            pandas.Series: Fastest lap, taking the first row when times tie.

        Raises:
            DataNotAvailableError: No positive lap time exists in this selection.
        """
        valid = self.loc[self.LapTime.notna() & (self.LapTime > pd.Timedelta(0))]
        if valid.empty:
            raise DataNotAvailableError("No positive lap times are available")
        return valid.sort_values("LapTime", kind="stable").iloc[0]

    def pick_quicklaps(self, threshold=1.07):
        """Keep positive lap times below a multiple of this selection's fastest.

        Args:
            threshold (float): Multiplier greater than one; ``1.07`` keeps laps
                strictly below 107% of the fastest lap time.

        Returns:
            Laps: Rows passing the threshold. Select a driver first to use that
            driver's fastest lap as the reference.

        Raises:
            ValueError: The threshold is at most one.
            DataNotAvailableError: There are no positive lap times.

        The threshold measures pace; it does not identify green flags or pit stops.
        """
        if threshold <= 1:
            raise ValueError("threshold must be greater than 1")
        return self.loc[(self.LapTime > pd.Timedelta(0)) &
                        (self.LapTime < self.pick_fastest().LapTime * threshold)]


def parse_laps(payload):
    """Normalize the NASCAR race lap feed into one row per car and completed lap.

    Args:
        payload (dict): Decoded feed with a ``laps`` list of driver records.

    Returns:
        Laps: Columns listed in ``LAP_COLUMNS``. LapTime is a timedelta, Speed
        is average mph, and integer fields use nullable ``Int64``. Lap zero is
        excluded and duplicate car/lap records keep the last occurrence.

    Raises:
        InvalidDataError: Required driver or lap fields are missing.

    Missing or nonpositive lap times become ``NaT``. Flags remain separate
    because they refer to leader laps rather than individual cars' laps.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("laps"), list):
        raise InvalidDataError("Expected a lap feed with a 'laps' list")
    rows = []
    for driver in payload["laps"]:
        if not isinstance(driver, dict) or "Number" not in driver or not isinstance(driver.get("Laps"), list):
            raise InvalidDataError("Expected Number and Laps in each lap-feed driver")
        for lap in driver["Laps"]:
            if not isinstance(lap, dict) or "Lap" not in lap:
                raise InvalidDataError("Expected Lap in each lap record")
            rows.append([driver.get("FullName"), str(driver["Number"]), driver.get("NASCARDriverID"),
                         lap["Lap"], lap.get("LapTime"), lap.get("LapSpeed"), lap.get("RunningPos")])
    frame = Laps(rows, columns=LAP_COLUMNS)
    for column in ("DriverId", "LapNumber", "Position"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    if frame.LapNumber.isna().any():
        raise InvalidDataError("Lap numbers must be integers")
    # Lap zero is the starting order, not a completed lap.
    frame = frame.loc[frame.LapNumber > 0].copy()
    frame["LapTime"] = seconds_to_timedelta(frame.LapTime)
    frame.loc[frame.LapTime <= pd.Timedelta(0), "LapTime"] = pd.NaT
    frame["Speed"] = pd.to_numeric(frame.Speed, errors="coerce")
    return frame.drop_duplicates(["DriverNumber", "LapNumber"], keep="last").reset_index(drop=True)


def parse_results(rows):
    """Normalize race, practice, or qualifying results and sort by position.

    Args:
        rows (list of dict): A race or run's raw ``results`` entries.

    Returns:
        pandas.DataFrame: Columns listed in ``RESULT_COLUMNS``. Car numbers are
        strings, counts and positions use nullable integers, and BestLapTime is
        a timedelta. Fields absent from the source remain missing.

    Raises:
        InvalidDataError: Input is not a list of result dictionaries.
    """
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise InvalidDataError("Expected a results list")
    records = []
    for row in rows:
        records.append({
            "DriverNumber": str(row.get("car_number", row.get("vehicle_number", ""))),
            "DriverId": row.get("driver_id"),
            "FullName": row.get("driver_fullname", row.get("driver_name")),
            "TeamName": row.get("team_name"),
            "Manufacturer": row.get("car_make", row.get("manufacturer")),
            "Position": row.get("finishing_position"), "GridPosition": row.get("starting_position"),
            "Laps": row.get("laps_completed"), "LapsLed": row.get("laps_led"),
            "Points": row.get("points_earned"), "Status": row.get("finishing_status", row.get("comment")),
            "BestLapTime": row.get("best_lap_time"),
        })
    frame = pd.DataFrame(records, columns=RESULT_COLUMNS)
    for column in ("DriverId", "Position", "GridPosition", "Laps", "LapsLed", "Points"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    frame["BestLapTime"] = seconds_to_timedelta(frame.BestLapTime)
    frame.loc[frame.BestLapTime <= pd.Timedelta(0), "BestLapTime"] = pd.NaT
    return frame.sort_values("Position", kind="stable").reset_index(drop=True)


def parse_track_status(payload):
    """Extract raw flag codes by leader lap from a race lap feed.

    Args:
        payload (dict): Decoded lap feed; ``flags`` may be absent.

    Returns:
        pandas.DataFrame: Nullable integer LeaderLapNumber and FlagState columns.
        An absent flags list produces an empty table.

    Raises:
        InvalidDataError: Flags are not a list of dictionaries.

    Do not join this table to car laps by lap number: lapped cars are misaligned.
    """
    flags = payload.get("flags", [])
    if not isinstance(flags, list) or any(not isinstance(flag, dict) for flag in flags):
        raise InvalidDataError("Expected a flags list")
    frame = pd.DataFrame([{"LeaderLapNumber": flag.get("LapsCompleted"),
                           "FlagState": flag.get("FlagState")} for flag in flags],
                         columns=["LeaderLapNumber", "FlagState"])
    for column in frame:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    return frame


def get_event_schedule(year, *, series=1, include_exhibition=False, refresh=False, client=None):
    """Fetch a season schedule, assigning rounds to points races.

    Args:
        year (int): Season year, between 1949 and 2100. Feed coverage varies.
        series (int or str): Cup/1, Xfinity/2, or Trucks/3.
        include_exhibition (bool): Include exhibitions with RoundNumber zero.
        refresh (bool): Bypass a fresh schedule cache entry.
        client (NASCARClient, optional): Client to use instead of a new instance.

    Returns:
        pandas.DataFrame: RoundNumber, RaceId, EventName, TrackName, EventDate,
        Year, SeriesId, and IsExhibition. Rows follow scheduled dates; EventDate retains
        the source's naive local race timestamp, including postponements.

    Raises:
        ValueError: The year is outside the accepted integer range.
        DataNotAvailableError: The season feed cannot be fetched.
        InvalidDataError: The feed lacks the selected series schedule schema.
    """
    if not isinstance(year, Integral) or isinstance(year, bool) or not 1949 <= year <= 2100:
        raise ValueError("year must be an integer between 1949 and 2100")
    series_id = resolve_series(series)
    payload = (client or NASCARClient()).schedule(int(year), refresh=refresh)
    key = f"series_{series_id}"
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise InvalidDataError(f"Expected {key} in the schedule")
    rows, round_number = [], 0
    events = payload[key]
    if any(not isinstance(r, dict) or "race_id" not in r for r in events):
        raise InvalidDataError("Expected race_id in each schedule event")
    for race in sorted(events, key=lambda r: r.get("date_scheduled") or r.get("race_date") or ""):
        points_race = race.get("race_type_id") == 1
        if points_race:
            round_number += 1
        if not points_race and not include_exhibition:
            continue
        rows.append({"RoundNumber": round_number if points_race else 0,
                     "RaceId": race["race_id"], "EventName": race.get("race_name"),
                     "TrackName": race.get("track_name"), "EventDate": race.get("race_date"),
                     "Year": int(year), "SeriesId": series_id, "IsExhibition": not points_race})
    schedule = pd.DataFrame(rows, columns=["RoundNumber", "RaceId", "EventName", "TrackName", "EventDate", "Year", "SeriesId", "IsExhibition"])
    schedule["EventDate"] = pd.to_datetime(schedule.EventDate, errors="coerce")
    return schedule


def get_event(year, event=None, *, race_id=None, series=1, client=None):
    """Find one event by round, name, track, or NASCAR race ID.

    Args:
        year (int): Season year.
        event (int or str, optional): Positive points-race round, exact event
            name, or case-insensitive substring of an event or track name.
        race_id (int, optional): Explicit NASCAR ID; takes precedence over event.
        series (int or str): Cup/1, Xfinity/2, or Trucks/3.
        client (NASCARClient, optional): Client used to fetch the schedule.

    Returns:
        pandas.Series: Copy of the matching schedule row.

    Raises:
        ValueError: No valid event selector is supplied.
        EventNotFoundError: No event matches.
        AmbiguousEventError: Multiple events match; the message lists race IDs.
        DataNotAvailableError: The schedule cannot be fetched.
        InvalidDataError: The schedule schema is unsupported.

    Exact event-name matches take priority over substring matches. Select
    exhibitions by name or race ID rather than round zero.
    """
    schedule = get_event_schedule(year, series=series, include_exhibition=True, client=client)
    if race_id is not None:
        matches = schedule.loc[schedule.RaceId == race_id]
    elif isinstance(event, Integral) and not isinstance(event, bool):
        if event <= 0:
            raise ValueError("round numbers start at 1; select exhibitions by race_id")
        matches = schedule.loc[schedule.RoundNumber == event]
    elif isinstance(event, str) and event.strip():
        name = event.strip().casefold()
        exact = schedule.EventName.str.casefold().eq(name)
        matches = schedule.loc[exact] if exact.any() else schedule.loc[
            schedule.EventName.str.casefold().str.contains(name, regex=False, na=False)
            | schedule.TrackName.str.casefold().str.contains(name, regex=False, na=False)]
    else:
        raise ValueError("Provide a round, event/track name, or race_id")
    if matches.empty:
        raise EventNotFoundError(f"No event in series {resolve_series(series)}, {year} matches {event or race_id!r}")
    if len(matches) > 1:
        choices = ", ".join(f"{r.EventName} (race_id={r.RaceId})" for r in matches.itertuples())
        raise AmbiguousEventError(f"Multiple events match: {choices}")
    return matches.iloc[0].copy()


class Session:
    """A race or weekend run with lazily loaded pandas tables.

    Obtain a session with ``get_session`` and call ``load`` before accessing
    its tables. ``event`` holds the schedule row; ``raw_data`` keeps loaded feeds.
    """

    def __init__(self, event, identifier="R", *, client=None):
        """Create an unloaded session from a schedule row.

        Args:
            event (pandas.Series): Row returned by ``get_event``.
            identifier (str): R/Race, P/Practice, Q/Qualifying, or an exact
                upstream run name. Aliases are case-insensitive.
            client (NASCARClient, optional): Client used by subsequent loads.
        """
        self.event = event.copy()
        self._client = client or NASCARClient()
        aliases = {"r": "Race", "race": "Race", "q": "Qualifying", "qualifying": "Qualifying",
                   "p": "Practice", "practice": "Practice"}
        self.name = aliases.get(str(identifier).casefold(), str(identifier))
        self._laps = self._results = None
        self._track_status = None
        self._pit_stops = self._race_control_messages = None
        self.raw_data = {}

    @property
    def laps(self):
        """Laps: Loaded completed car laps; raises SessionNotLoadedError if absent."""
        if self._laps is None:
            raise SessionNotLoadedError("Lap data is not loaded; call load(laps=True)")
        return self._laps

    @property
    def results(self):
        """pandas.DataFrame: Loaded results; raises SessionNotLoadedError if absent."""
        if self._results is None:
            raise SessionNotLoadedError("Results are not loaded; call load(results=True)")
        return self._results

    @property
    def track_status(self):
        """pandas.DataFrame: Leader-lap flags; raises SessionNotLoadedError if absent."""
        if self._track_status is None:
            raise SessionNotLoadedError("Track status is not loaded; call load(laps=True)")
        return self._track_status

    @property
    def pit_stops(self):
        """pandas.DataFrame: Pit timing; load with ``pit_stops=True`` first."""
        if self._pit_stops is None:
            raise SessionNotLoadedError("Call load(pit_stops=True) to load pit timing")
        return self._pit_stops

    @property
    def race_control_messages(self):
        """pandas.DataFrame: Race notes; load with ``messages=True`` first."""
        if self._race_control_messages is None:
            raise SessionNotLoadedError("Call load(messages=True) to load race notes")
        return self._race_control_messages

    @property
    def drivers(self):
        """list of str: Car numbers in result order; requires loaded results."""
        return self.results.DriverNumber.tolist()

    def get_driver(self, identifier):
        """Look up one driver's loaded result by full name or car number.

        Args:
            identifier (str or int): Car number or case-insensitive exact full name.

        Returns:
            pandas.Series: The matching result row.

        Raises:
            KeyError: No unique driver matches the identifier.
            SessionNotLoadedError: Results have not been loaded.
        """
        rows = self.results.loc[(self.results.DriverNumber == str(identifier)) |
                                (self.results.FullName.str.casefold() == str(identifier).casefold())]
        if len(rows) != 1:
            raise KeyError(identifier)
        return rows.iloc[0]

    def load(self, *, laps=True, results=True, pit_stops=False, messages=False, refresh=False):
        """Fetch requested datasets and replace session tables after parsing succeeds.

        Args:
            laps (bool): Load race laps and leader-lap flags. Set False for
                practice or qualifying, whose historical lap feeds are unsupported.
            results (bool): Load the race or run's results.
            pit_stops (bool): Load race pit-road and stationary service timing.
            messages (bool): Load race commentary by leader lap.
            refresh (bool): Bypass fresh cache entries for requested feeds.

        Returns:
            Session: This instance, allowing ``get_session(...).load()`` chaining.

        Raises:
            DataNotAvailableError: A feed/run is unavailable, a run selection is
                ambiguous, or historical non-race laps are requested.
            InvalidDataError: A feed has an unsupported schema.

        Unrequested tables are retained. A failed load leaves previously loaded
        tables unchanged. Use an exact run name if a weekend has multiple runs
        of the same type. The source qualifying run may cover only its final round.
        """
        if (laps or pit_stops or messages) and self.name != "Race":
            raise DataNotAvailableError("Historical practice/qualifying lap feeds are not supported; use load(laps=False) for results")
        year, race_id = int(self.event.Year), int(self.event.RaceId)
        series_id = int(self.event.get("SeriesId", 1))
        new_laps, new_results, raw = self._laps, self._results, self.raw_data.copy()
        new_status = self._track_status
        new_pits, new_messages = self._pit_stops, self._race_control_messages
        if results:
            weekend = self._client.weekend(year, race_id, series=series_id, refresh=refresh)
            if not isinstance(weekend, dict):
                raise InvalidDataError("Expected a weekend-feed object")
            if self.name == "Race":
                candidates = [r for r in weekend.get("weekend_race", []) if r.get("race_id") == race_id]
            else:
                run_type = {"Practice": 1, "Qualifying": 2}.get(self.name)
                candidates = [r for r in weekend.get("weekend_runs", [])
                              if (run_type is not None and r.get("run_type") == run_type)
                              or r.get("run_name", "").casefold() == self.name.casefold()]
            if len(candidates) != 1:
                raise DataNotAvailableError("Session unavailable or ambiguous; use an exact run_name from weekend_runs")
            new_results = parse_results(candidates[0].get("results", []))
            raw["weekend"] = weekend
        if laps:
            payload = self._client.laps(year, race_id, series=series_id, refresh=refresh)
            new_laps = parse_laps(payload)
            new_status = parse_track_status(payload)
            raw["laps"] = payload
        if pit_stops or messages:
            from .datasets import parse_pit_stops, parse_messages
            if pit_stops:
                payload = self._client.pit_stops(year, race_id, series=series_id, refresh=refresh)
                new_pits = parse_pit_stops(payload)
                raw["pit_stops"] = payload
            if messages:
                payload = self._client.notes(year, race_id, series=series_id, refresh=refresh)
                new_messages = parse_messages(payload)
                raw["messages"] = payload
        self._laps, self._results, self.raw_data = new_laps, new_results, raw
        self._track_status = new_status
        self._pit_stops, self._race_control_messages = new_pits, new_messages
        return self


def get_session(year, event=None, identifier="R", *, race_id=None, series=1, client=None):
    """Select an event and return an unloaded session.

    Args:
        year (int): Season year.
        event (int or str, optional): Points-race round or event/track selector.
        identifier (str): R/Race, P/Practice, Q/Qualifying, or exact run name.
        race_id (int, optional): NASCAR race ID, taking precedence over event.
        series (int or str): Cup/1, Xfinity/2, or Trucks/3.
        client (NASCARClient, optional): Shared client for selection and loading.

    Returns:
        Session: Call ``load()`` to fetch laps/results, or ``load(laps=False)``
        for practice/qualifying results.

    Raises:
        ValueError: Invalid year or event selector.
        EventNotFoundError: No event matches.
        AmbiguousEventError: Multiple events match.
        DataNotAvailableError: The schedule cannot be fetched.
        InvalidDataError: The schedule schema is unsupported.
    """
    client = client or NASCARClient()
    return Session(get_event(year, event, race_id=race_id, series=series, client=client), identifier, client=client)
