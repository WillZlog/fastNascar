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


@dataclass
class LiveSnapshot:
    """One Cup timing update, with timing and pit-stop tables.

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
    """
    race_id: int
    run_id: int | None
    lap_number: int | None
    flag_state: int | None
    timestamp: pd.Timestamp
    timing: pd.DataFrame
    pit_stops: pd.DataFrame
    raw_data: dict


def parse_snapshot(payload):
    """Normalize a raw live Cup feed into timing and pit-stop tables.

    Args:
        payload (dict): Decoded current live-feed response.

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
    if payload.get("series_id") != 1:
        raise DataNotAvailableError("The live feed is currently serving another series, not Cup")
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
                        timing.sort_values("Position", kind="stable").reset_index(drop=True), pit_stops, payload)


def get_live_session(*, race_id=None, client=None):
    """Fetch the current Cup timing snapshot without caching.

    Args:
        race_id (int, optional): Require this race ID instead of accepting any Cup race.
        client (NASCARClient, optional): Client to use for the request.

    Returns:
        LiveSnapshot: Current timing, observed stops, and source metadata.

    Raises:
        DataNotAvailableError: Network/offline failure, wrong series, or a different race.
        InvalidDataError: Unsupported JSON or live-feed schema.
    """
    snapshot = parse_snapshot((client or NASCARClient()).live())
    if race_id is not None and snapshot.race_id != race_id:
        raise DataNotAvailableError(f"Live feed is serving race {snapshot.race_id}, not {race_id}")
    return snapshot


def iter_live(*, interval=2.0, max_updates=None, race_id=None, client=None):
    """Poll the current Cup session, yielding each fetched snapshot.

    Args:
        interval (float): Delay in seconds between updates, at least one second.
            Request and consumer processing time are additional to this delay.
        max_updates (int, optional): Positive snapshot limit. None polls until
            the iterator is closed, interrupted, or encounters an error.
        race_id (int, optional): Require this NASCAR race ID.
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
        snapshot = get_live_session(race_id=race_id, client=client)
        current = snapshot.race_id, snapshot.run_id
        if identity is not None and current != identity:
            raise DataNotAvailableError("Live feed switched race or timing run")
        identity = current
        yield snapshot
        count += 1
        if max_updates is None or count < max_updates:
            time.sleep(interval)


def record_live(path, *, interval=2.0, max_updates=None, race_id=None, client=None):
    """Append raw live snapshots to a UTF-8 JSONL file, flushing each update.

    Args:
        path (str or pathlib.Path): Recording file; its parent must already exist.
        interval (float): Delay between updates in seconds, at least one second.
        max_updates (int, optional): Positive limit, or None to record indefinitely.
        race_id (int, optional): Require a specific NASCAR race ID.
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
    with Path(path).open("a", encoding="utf-8") as file:
        for snapshot in iter_live(interval=interval, max_updates=max_updates, race_id=race_id, client=client):
            file.write(json.dumps(snapshot.raw_data) + "\n")
            file.flush()
            count += 1
    return count


def replay_live(path):
    """Yield snapshots from a JSONL recording without requests or timed delays.

    Args:
        path (str or pathlib.Path): Recording produced by ``record_live``.

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
                    yield parse_snapshot(json.loads(line))
                except ValueError as exc:
                    raise InvalidDataError(f"Invalid JSON at recording line {line_number}") from exc
