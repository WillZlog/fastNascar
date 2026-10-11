"""Season result collection and driver aggregates."""
import pandas as pd

from .api import NASCARClient
from .core import Session, get_event_schedule
from .exceptions import DataNotAvailableError


def get_season_results(year, *, series=1, race_ids=None, refresh=False, errors="raise", client=None):
    """Collect race results with season/event metadata.

    Args:
        year (int): Season year.
        series (int or str): Cup/1, Xfinity/2, or Trucks/3.
        race_ids (iterable of int, optional): Restrict requests to these IDs.
        refresh (bool): Refresh schedule and result feeds.
        errors (str): ``raise`` on unavailable feeds, or ``skip`` and report
            failures in the returned table's ``attrs['unavailable_races']``.
        client (NASCARClient, optional): Reused transport client.

    Returns:
        pandas.DataFrame: Race results with Year, SeriesId, RaceId, EventName,
        TrackName, RoundNumber, and EventDate. Empty-result races are listed in
        ``attrs['empty_races']``. A full season makes one request per race.

    Raises:
        ValueError: Invalid error mode or race IDs outside this points-race schedule.
        DataNotAvailableError: An unavailable feed with errors='raise'.
        InvalidDataError: Malformed upstream data; never silently skipped.
    """
    if errors not in ("raise", "skip"):
        raise ValueError("errors must be 'raise' or 'skip'")
    client = client or NASCARClient()
    schedule = get_event_schedule(year, series=series, refresh=refresh, client=client)
    if race_ids is not None:
        ids = set(race_ids)
        if not ids.issubset(set(schedule.RaceId)):
            raise ValueError("race_ids must belong to the selected points-race schedule")
        schedule = schedule.loc[schedule.RaceId.isin(ids)]
    tables, unavailable, empty = [], [], []
    for _, event in schedule.iterrows():
        try:
            session = Session(event, client=client).load(laps=False, refresh=refresh)
        except DataNotAvailableError as exc:
            if errors == "raise":
                raise
            unavailable.append({"RaceId": int(event.RaceId), "Error": str(exc)})
            continue
        if session.results.empty:
            empty.append(int(event.RaceId))
            continue
        frame = session.results.copy()
        for column in ("Year", "SeriesId", "RaceId", "EventName", "TrackName", "RoundNumber", "EventDate"):
            frame[column] = event[column]
        tables.append(frame)
    from .core import RESULT_COLUMNS
    result = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame(columns=RESULT_COLUMNS + ["Year", "SeriesId", "RaceId", "EventName", "TrackName", "RoundNumber", "EventDate"])
    result.attrs.update(unavailable_races=unavailable, empty_races=empty)
    return result


def driver_standings(results):
    """Aggregate collected results by stable driver ID.

    Args:
        results (pandas.DataFrame): Table returned by ``get_season_results``.

    Returns:
        pandas.DataFrame: DriverId, FullName, Races, Wins, Top5s, Top10s, Points,
        LapsLed, AverageFinish, AverageStart. Sorted by earned Points, then Wins.
        This is a result aggregate, not official playoff standings. Missing counts
        remain unknown if every source value is missing.

    Raises:
        ValueError: Results mix seasons/series or lack unique driver/race records.
    """
    if not results.empty:
        if results.Year.nunique() != 1 or results.SeriesId.nunique() != 1:
            raise ValueError("Aggregate one season and series at a time")
        if results.DriverId.isna().any() or results.duplicated(["RaceId", "DriverId"]).any():
            raise ValueError("Results require a unique driver ID per race")
    rows = []
    for driver_id, group in results.groupby("DriverId", sort=False):
        rows.append({"DriverId": driver_id, "FullName": group.FullName.iloc[-1],
                     "Races": group.RaceId.nunique(), "Wins": int(group.Position.eq(1).sum()),
                     "Top5s": int(group.Position.le(5).sum()), "Top10s": int(group.Position.le(10).sum()),
                     "Points": group.Points.sum(min_count=1), "LapsLed": group.LapsLed.sum(min_count=1),
                     "AverageFinish": group.Position.mean(), "AverageStart": group.GridPosition.mean()})
    frame = pd.DataFrame(rows, columns=["DriverId", "FullName", "Races", "Wins", "Top5s", "Top10s", "Points", "LapsLed", "AverageFinish", "AverageStart"])
    return frame.sort_values(["Points", "Wins"], ascending=False, kind="stable").reset_index(drop=True)
