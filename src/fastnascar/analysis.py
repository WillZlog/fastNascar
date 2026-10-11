"""Pace and strategy analysis on loaded pandas tables. No network requests."""
from numbers import Integral

import pandas as pd

from .exceptions import DataNotAvailableError


def _window_size(window):
    """Validate a positive integer rolling-window size."""
    if isinstance(window, bool) or not isinstance(window, Integral) or window < 1:
        raise ValueError("window must be a positive integer")
    return int(window)


def rolling_pace(laps, window=10):
    """Compute mean and median pace over consecutive car laps.

    Args:
        laps (Laps): Loaded or filtered lap table.
        window (int): Required count of consecutive, positive timed laps.

    Returns:
        pandas.DataFrame: DriverNumber, Driver, LapNumber, MeanLapTime,
        MedianLapTime, and ValidLaps. Windows crossing missing car laps or invalid
        times have NaT pace. Slow laps are included; no green-flag inference is made.
    """
    window = _window_size(window)
    rows = []
    for number, group in laps.groupby("DriverNumber", sort=False):
        group = group.sort_values("LapNumber").drop_duplicates("LapNumber", keep="last")
        times = group.set_index("LapNumber").LapTime.dt.total_seconds()
        times = times.where(times > 0)
        # A sequence number avoids allocating rows for very large gaps.
        segment = times.index.to_series().diff().ne(1).fillna(True).cumsum()
        for _, part in times.groupby(segment):
            rolling = part.rolling(window, min_periods=window)
            means, medians = rolling.mean(), rolling.median()
            count = part.rolling(window, min_periods=1).count()
            for lap in part.index:
                rows.append({"DriverNumber": str(number), "Driver": group.Driver.iloc[0],
                             "LapNumber": int(lap), "MeanLapTime": means.loc[lap],
                             "MedianLapTime": medians.loc[lap], "ValidLaps": int(count.loc[lap])})
    frame = pd.DataFrame(rows, columns=["DriverNumber", "Driver", "LapNumber", "MeanLapTime", "MedianLapTime", "ValidLaps"])
    from ._time import seconds_to_timedelta
    for column in ("MeanLapTime", "MedianLapTime"):
        frame[column] = seconds_to_timedelta(frame[column])
    return frame


def best_lap_average(laps, window=10):
    """Find each car's fastest complete consecutive-lap window.

    Args:
        laps (Laps): Loaded lap selection, including any slow laps you retain.
        window (int): Number of consecutive laps in each average.

    Returns:
        pandas.DataFrame: DriverNumber, Driver, StartLap, EndLap, AverageLapTime,
        sorted by pace. Cars without a valid complete window are omitted.
    """
    window = _window_size(window)
    pace = rolling_pace(laps, window).dropna(subset=["MeanLapTime"])
    rows = []
    for number, group in pace.groupby("DriverNumber", sort=False):
        best = group.sort_values("MeanLapTime", kind="stable").iloc[0]
        rows.append({"DriverNumber": number, "Driver": best.Driver,
                     "StartLap": int(best.LapNumber) - window + 1,
                     "EndLap": int(best.LapNumber), "AverageLapTime": best.MeanLapTime})
    frame = pd.DataFrame(rows, columns=["DriverNumber", "Driver", "StartLap", "EndLap", "AverageLapTime"])
    return frame.sort_values("AverageLapTime", kind="stable").reset_index(drop=True)


def compare_drivers(laps, driver, reference):
    """Compare recorded lap times at matching car lap numbers.

    Args:
        laps (Laps): Loaded lap data.
        driver (str or int): Driver selector accepted by ``pick_driver``.
        reference (str or int): Comparison driver selector.

    Returns:
        pandas.DataFrame: LapNumber, LapTime, ReferenceLapTime, Delta. Positive
        Delta means driver was slower. Missing laps remain NaT; Delta is a
        per-lap pace difference, never an on-track gap or elapsed race deficit.

    Raises:
        DataNotAvailableError: Either selector fails to identify exactly one car.
    """
    selections = [laps.pick_driver(value) for value in (driver, reference)]
    if any(group.DriverNumber.nunique() != 1 for group in selections):
        raise DataNotAvailableError("Each comparison selector must identify one car")
    first, second = [group.sort_values("LapNumber").drop_duplicates("LapNumber", keep="last")
                     for group in selections]
    frame = first[["LapNumber", "LapTime"]].merge(
        second[["LapNumber", "LapTime"]].rename(columns={"LapTime": "ReferenceLapTime"}),
        on="LapNumber", how="outer").sort_values("LapNumber").reset_index(drop=True)
    frame["Delta"] = frame.LapTime - frame.ReferenceLapTime
    return pd.DataFrame(frame)


def summarize_stints(laps, pit_stops):
    """Summarize intervals between observed pit-entry laps for each car.

    Args:
        laps (Laps): Loaded car laps.
        pit_stops (pandas.DataFrame): Parsed historical pit records.

    Returns:
        pandas.DataFrame: DriverNumber, Stint, StartLap, EndLap, RecordedLaps,
        TimedLaps, MedianLapTime, FastestLapTime. Pit-entry laps belong to the
        preceding interval and are excluded from pace calculations, as is the
        first lap after entry. These are pit intervals, not verified tire stints;
        missing pit data can merge actual stints. Caution laps remain included.
    """
    rows = []
    for number, group in laps.groupby("DriverNumber", sort=False):
        entries = sorted(set(int(value) for value in pit_stops.loc[
            pit_stops.DriverNumber.astype(str).eq(str(number)), "LapNumber"].dropna() if value > 0))
        group = group.sort_values("LapNumber").copy()
        group["Stint"] = group.LapNumber.map(lambda lap: 1 + sum(lap > entry for entry in entries))
        excluded = set(entries) | {entry + 1 for entry in entries}
        for stint, part in group.groupby("Stint", sort=True):
            times = part.loc[~part.LapNumber.isin(excluded), "LapTime"]
            times = times.loc[times > pd.Timedelta(0)]
            rows.append({"DriverNumber": str(number), "Stint": int(stint),
                         "StartLap": int(part.LapNumber.min()), "EndLap": int(part.LapNumber.max()),
                         "RecordedLaps": len(part), "TimedLaps": len(times),
                         "MedianLapTime": times.median(), "FastestLapTime": times.min()})
    return pd.DataFrame(rows, columns=["DriverNumber", "Stint", "StartLap", "EndLap", "RecordedLaps", "TimedLaps", "MedianLapTime", "FastestLapTime"])


def flag_periods(track_status):
    """Group consecutive known leader laps with the same flag code.

    Args:
        track_status (pandas.DataFrame): LeaderLapNumber and FlagState records.

    Returns:
        pandas.DataFrame: StartLap, EndLap, FlagState, RecordedLaps. Missing lap
        numbers break periods, so unknown laps are never counted as cautions.
    """
    status = track_status.dropna(subset=["LeaderLapNumber", "FlagState"]).sort_values("LeaderLapNumber")
    status = status.drop_duplicates("LeaderLapNumber", keep="last")
    rows = []
    for row in status.itertuples():
        lap, flag = int(row.LeaderLapNumber), int(row.FlagState)
        if rows and rows[-1]["FlagState"] == flag and rows[-1]["EndLap"] + 1 == lap:
            rows[-1]["EndLap"] = lap
            rows[-1]["RecordedLaps"] += 1
        else:
            rows.append({"StartLap": lap, "EndLap": lap, "FlagState": flag, "RecordedLaps": 1})
    return pd.DataFrame(rows, columns=["StartLap", "EndLap", "FlagState", "RecordedLaps"])
