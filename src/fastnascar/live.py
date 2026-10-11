"""Uncached live snapshots, bounded polling, and replayable JSONL recordings."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import time

import pandas as pd

from .api import NASCARClient
from ._time import seconds_to_timedelta
from .exceptions import DataNotAvailableError, InvalidDataError
from .series import resolve_series


@dataclass
class LiveSnapshot:
    """One timing update, with timing and pit-stop tables.

    Attributes:
        race_id (int): NASCAR race ID.
        run_id (int or None): Timing run ID, which can change within a weekend.
        lap_number (int or None): Leader's current lap.
        flag_state (int or None): Raw upstream flag code.
        timestamp (pandas.Timestamp): Source timestamp in UTC, or NaT if absent.
        timing (pandas.DataFrame): Car order, completed laps, last-lap times,
            lap-average mph, raw gaps/status, and elapsed timedeltas.
        pit_stops (pandas.DataFrame): Observed stops with elapsed pit-in/out
            timedeltas. Zero placeholders are excluded; unfinished exits are NaT.
        raw_data (dict): Original decoded response for inspection or recording.
        series_id (int): NASCAR series ID; Cup=1, Xfinity=2, Trucks=3.
    """
    race_id: int
    run_id: int | None
    lap_number: int | None
    flag_state: int | None
    timestamp: pd.Timestamp
    timing: pd.DataFrame
    pit_stops: pd.DataFrame
    raw_data: dict
    series_id: int = 1


def parse_snapshot(payload, *, series=1):
    """Normalize a live feed into timing and pit-stop tables for a selected series.

    Args:
        payload (dict): Decoded current live-feed response.
        series (int or str): Required series, Cup/1, Xfinity/2, or Trucks/3.

    Returns:
        LiveSnapshot: Cars sorted by running position with a UTC source timestamp.
        LastLapTime and elapsed times are timedeltas; Speed is lap-average mph.
        GapToLeader and Status retain upstream representations.

    Raises:
        DataNotAvailableError: The feed is serving another series.
        InvalidDataError: Required top-level or vehicle fields are absent.

    A successful parse does not establish freshness. Inspect the source timestamp;
    snapshots can repeat or remain available after a session ends.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("vehicles"), list):
        raise InvalidDataError("Expected vehicles in the live feed")
    series_id = resolve_series(series)
    if payload.get("series_id") != series_id:
        raise DataNotAvailableError(f"The live feed is not currently serving series {series_id}")
    if not isinstance(payload.get("race_id"), int):
        raise InvalidDataError("Expected an integer race_id in the live feed")
    rows, pits = [], []
    for vehicle in payload["vehicles"]:
        if not isinstance(vehicle, dict) or "vehicle_number" not in vehicle:
            raise InvalidDataError("Expected vehicle_number in each live vehicle")
        driver = vehicle.get("driver") or {}
        number = str(vehicle["vehicle_number"])
        rows.append({"DriverNumber": number, "Driver": driver.get("full_name"),
                     "DriverId": driver.get("driver_id"), "Position": vehicle.get("running_position"),
                     "Laps": vehicle.get("laps_completed"), "LastLapTime": vehicle.get("last_lap_time"),
                     "Speed": vehicle.get("last_lap_speed"), "GapToLeader": vehicle.get("delta"),
                     "ElapsedTime": vehicle.get("vehicle_elapsed_time"), "Status": vehicle.get("status"),
                     "IsOnTrack": vehicle.get("is_on_track")})
        for pit in vehicle.get("pit_stops", []):
            # Feed contains zero-filled placeholders: retain only observed stops.
            if pit.get("pit_in_elapsed_time", 0) > 0:
                pits.append({"DriverNumber": number, "LapNumber": pit.get("pit_in_lap_count"),
                             "LeaderLapNumber": pit.get("pit_in_leader_lap"),
                             "PitInTime": pit.get("pit_in_elapsed_time"),
                             "PitOutTime": pit.get("pit_out_elapsed_time") or None,
                             "PositionsGained": pit.get("positions_gained_lossed")})
    timing = pd.DataFrame(rows, columns=["DriverNumber", "Driver", "DriverId", "Position", "Laps",
                                       "LastLapTime", "Speed", "GapToLeader", "ElapsedTime", "Status", "IsOnTrack"])
    for column in ("DriverId", "Position", "Laps", "Status"):
        timing[column] = pd.to_numeric(timing[column], errors="coerce").astype("Int64")
    for column in ("LastLapTime", "ElapsedTime"):
        timing[column] = seconds_to_timedelta(timing[column])
    timing.loc[timing.LastLapTime <= pd.Timedelta(0), "LastLapTime"] = pd.NaT
    # delta remains raw: upstream gap representations may vary for lapped cars.
    timing["Speed"] = pd.to_numeric(timing.Speed, errors="coerce")
    pit_stops = pd.DataFrame(pits, columns=["DriverNumber", "LapNumber", "LeaderLapNumber", "PitInTime", "PitOutTime", "PositionsGained"])
    for column in ("PitInTime", "PitOutTime"):
        pit_stops[column] = seconds_to_timedelta(pit_stops[column])
    timestamp = pd.to_datetime(payload.get("time_of_day_os"), utc=True, errors="coerce")
    return LiveSnapshot(payload["race_id"], payload.get("run_id"), payload.get("lap_number"),
                        payload.get("flag_state"), timestamp,
                        timing.sort_values("Position", kind="stable").reset_index(drop=True), pit_stops, payload, series_id)


def get_live_session(*, race_id=None, series=1, client=None):
    """Fetch the current timing snapshot for a selected series without caching.

    Args:
        race_id (int, optional): Require this race ID.
        series (int or str): Required series, Cup/1, Xfinity/2, or Trucks/3.
        client (NASCARClient, optional): Client to use for the request.

    Returns:
        LiveSnapshot: Current timing, observed stops, and source metadata.

    Raises:
        DataNotAvailableError: Network/offline failure, wrong series, or a different race.
        InvalidDataError: Unsupported JSON or live-feed schema.
    """
    snapshot = parse_snapshot((client or NASCARClient()).live(), series=series)
    if race_id is not None and snapshot.race_id != race_id:
        raise DataNotAvailableError(f"Live feed is serving race {snapshot.race_id}, not {race_id}")
    return snapshot


def iter_live(*, interval=2.0, max_updates=None, race_id=None, series=1, client=None):
    """Poll the current session for a selected series, yielding fetched snapshots.

    Args:
        interval (float): Delay in seconds between updates, at least one second.
            Request and consumer processing time are additional to this delay.
        max_updates (int, optional): Positive snapshot limit. None polls until
            the iterator is closed, interrupted, or encounters an error.
        race_id (int, optional): Require this NASCAR race ID.
        series (int or str): Required series, Cup/1, Xfinity/2, or Trucks/3.
        client (NASCARClient, optional): Client reused across requests.

    Yields:
        LiveSnapshot: Each fetched update, including repeated snapshots.

    Raises:
        ValueError: Invalid interval or snapshot limit, upon first iteration.
        DataNotAvailableError: Fetch failure or a race/run/series switch.
        InvalidDataError: A response cannot be parsed.

    Polling cannot recover laps missed between requests. The first snapshot
    fixes the race and run identity for the rest of this iterator.
    """
    if not math.isfinite(interval) or interval < 1:
        raise ValueError("interval must be finite and at least one second")
    if max_updates is not None and (isinstance(max_updates, bool) or not isinstance(max_updates, int) or max_updates < 1):
        raise ValueError("max_updates must be a positive integer or None")
    client = client or NASCARClient()
    count, identity = 0, None
    while max_updates is None or count < max_updates:
        snapshot = get_live_session(race_id=race_id, series=series, client=client)
        current = snapshot.race_id, snapshot.run_id
        if identity is not None and current != identity:
            raise DataNotAvailableError("Live feed switched race or timing run")
        identity = current
        yield snapshot
        count += 1
        if max_updates is None or count < max_updates:
            time.sleep(interval)


def record_live(path, *, interval=2.0, max_updates=None, race_id=None, series=1, deduplicate=False, client=None):
    """Append raw live snapshots to a UTF-8 JSONL file, flushing each update.

    Args:
        path (str or pathlib.Path): Recording file; its parent must already exist.
        interval (float): Delay between updates in seconds, at least one second.
        max_updates (int, optional): Positive limit, or None to record indefinitely.
        race_id (int, optional): Require a specific NASCAR race ID.
        series (int or str): Required series, Cup/1, Xfinity/2, or Trucks/3.
        deduplicate (bool): Skip identical consecutive payloads. max_updates
            limits requests, while the returned count measures records written.
        client (NASCARClient, optional): Client used for polling.

    Returns:
        int: Number of updates written when the recording finishes normally.

    Raises:
        ValueError: Invalid polling options.
        DataNotAvailableError: Fetch failure or a race/run/series switch.
        InvalidDataError: A response cannot be parsed.
        OSError: The recording cannot be opened or written.
        KeyboardInterrupt: Ctrl+C stops recording; flushed updates remain on disk.
    """
    count = 0
    expected, previous = None, None
    recording = Path(path)
    if recording.exists():
        for old in replay_live(recording, series=series):
            identity = old.series_id, old.race_id, old.run_id
            if expected is not None and expected != identity:
                raise DataNotAvailableError("Existing recording mixes races or timing runs")
            expected, previous = identity, old.raw_data
    with Path(path).open("a", encoding="utf-8") as file:
        for snapshot in iter_live(interval=interval, max_updates=max_updates, race_id=race_id, series=series, client=client):
            identity = snapshot.series_id, snapshot.race_id, snapshot.run_id
            if expected is not None and expected != identity:
                raise DataNotAvailableError("Cannot append a different race/run to this recording")
            expected = identity
            if deduplicate and previous == snapshot.raw_data:
                continue
            file.write(json.dumps(snapshot.raw_data) + "\n")
            file.flush()
            count += 1
            previous = snapshot.raw_data
    return count


def replay_live(path, *, series=1):
    """Yield snapshots from a JSONL recording without requests or timed delays.

    Args:
        path (str or pathlib.Path): Recording produced by ``record_live``.
        series (int or str): Expected recorded series, Cup/1, Xfinity/2, or Trucks/3.

    Yields:
        LiveSnapshot: Parsed snapshots in file order. Blank lines are skipped.

    Raises:
        OSError: The recording cannot be opened or read.
        InvalidDataError: Invalid JSON (with its line number) or unsupported schema.
        DataNotAvailableError: A recorded payload belongs to another series.
    """
    with Path(path).open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            if line.strip():
                try:
                    yield parse_snapshot(json.loads(line), series=series)
                except ValueError as exc:
                    raise InvalidDataError(f"Invalid JSON at recording line {line_number}") from exc


class LiveHistory:
    """Accumulate observed last-lap samples without inventing missed timing data.

    One row is retained per car/lap. Newer corrections replace earlier samples;
    stale timestamps are ignored. The first update fixes the series/race/run.
    """

    def __init__(self):
        """Create an empty history with no established race/run identity."""
        self._identity = None
        self._timestamp = pd.NaT
        self._rows = {}
        self._latest = {}

    @property
    def laps(self):
        """pandas.DataFrame: Observed laps, timestamps, and detected missing counts.

        MissedLaps is unknown for the first observation per car. Position and
        FlagState are observation-time values, not certified lap-crossing values.
        """
        frame = pd.DataFrame(list(self._rows.values()), columns=["DriverNumber", "Driver", "LapNumber", "LapTime", "Speed", "Position", "FlagState", "ObservedAt", "MissedLaps"])
        frame["LapTime"] = pd.to_timedelta(frame.LapTime).astype("timedelta64[ns]")
        frame["MissedLaps"] = frame.MissedLaps.astype("Int64")
        return frame.sort_values(["DriverNumber", "LapNumber"]).reset_index(drop=True)

    def update(self, snapshot):
        """Incorporate a snapshot and return the count of inserted/corrected laps.

        Args:
            snapshot (LiveSnapshot): Parsed live timing update.

        Returns:
            int: Changed lap-time records. Duplicate times and older snapshots
            cause no change; lap jumps record how many laps were missed.

        Raises:
            DataNotAvailableError: Series, race, or run differs from prior updates.
        """
        identity = snapshot.series_id, snapshot.race_id, snapshot.run_id
        if self._identity is not None and identity != self._identity:
            raise DataNotAvailableError("History cannot mix series, races, or timing runs")
        self._identity = identity
        if pd.notna(snapshot.timestamp) and pd.notna(self._timestamp) and snapshot.timestamp < self._timestamp:
            return 0
        changed = 0
        for row in snapshot.timing.itertuples():
            if pd.isna(row.Laps) or row.Laps <= 0 or pd.isna(row.LastLapTime) or row.LastLapTime <= pd.Timedelta(0):
                continue
            number, lap = str(row.DriverNumber), int(row.Laps)
            previous_lap = self._latest.get(number)
            if previous_lap is not None and lap < previous_lap:
                continue
            key = number, lap
            previous = self._rows.get(key)
            if previous is not None and previous["LapTime"] == row.LastLapTime:
                continue
            missed = (previous["MissedLaps"] if previous else
                      max(0, lap - previous_lap - 1) if previous_lap is not None else pd.NA)
            self._rows[key] = {"DriverNumber": number, "Driver": row.Driver, "LapNumber": lap,
                               "LapTime": row.LastLapTime, "Speed": row.Speed, "Position": row.Position,
                               "FlagState": snapshot.flag_state, "ObservedAt": snapshot.timestamp,
                               "MissedLaps": missed}
            self._latest[number] = lap
            changed += 1
        if pd.notna(snapshot.timestamp):
            self._timestamp = snapshot.timestamp
        return changed

    @classmethod
    def from_recording(cls, path, *, series=1):
        """Rebuild observed lap history from a JSONL recording.

        Args:
            path (str or pathlib.Path): Recorded snapshot file.
            series (int or str): Expected series.

        Returns:
            LiveHistory: History with duplicates, corrections, and gaps processed.

        Raises:
            InvalidDataError: Invalid recording data.
            DataNotAvailableError: Wrong series or mixed race/run identities.
            OSError: Recording cannot be read.
        """
        history = cls()
        for snapshot in replay_live(path, series=series):
            history.update(snapshot)
        return history
