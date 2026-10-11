# FastNASCAR

FastNASCAR provides NASCAR Cup lap times, results, and live timing in pandas.
Select a race, load the session, and work with its lap data:

```python
import fastnascar

session = fastnascar.get_session(2026, race_id=5626).load()
hocevar = session.laps.pick_driver("77")
print(hocevar.pick_fastest())
```

Early release. Race laps and weekend results work; historical practice and
qualifying laps aren't supported yet. No throttle, brake, RPM, or GPS telemetry.
This project is unaffiliated with NASCAR.

## Install

Python 3.10+ is required. From a checkout:

```sh
python -m pip install -e .
python -m pip install -e ".[plot]"  # optional matplotlib charts
```

## Pick a race

```python
schedule = fastnascar.get_event_schedule(2026)
print(schedule[["RoundNumber", "RaceId", "EventName", "EventDate"]])

session = fastnascar.get_session(2026, 29)
session = fastnascar.get_session(2026, "Bass Pro Shops Night Race")
session = fastnascar.get_session(2026, race_id=5626)
session.load()
```

Rounds count points races in scheduled order. Use
`include_exhibition=True` to include exhibitions in the schedule; select those
by name or race ID. Repeated names raise `AmbiguousEventError`, with the matching
race IDs. Schedule dates retain NASCAR's local timestamps without a timezone.

## Work with laps

```python
hocevar = session.laps.pick_driver("77")
print(hocevar.pick_fastest())
print(hocevar.pick_laps([1, 2, 3]))
print(hocevar.pick_quicklaps().LapTime.dt.total_seconds().mean())
print(session.results[["Position", "DriverNumber", "FullName", "Points"]])
```

| Table | Columns |
| --- | --- |
| `session.laps` | Driver, DriverNumber, DriverId, LapNumber, LapTime, Speed, Position |
| `session.results` | DriverNumber, DriverId, FullName, TeamName, Manufacturer, Position, GridPosition, Laps, LapsLed, Points, Status, BestLapTime |
| `session.track_status` | LeaderLapNumber, FlagState |

Lap times are pandas timedeltas. Speeds are lap-average mph. Car numbers are
strings; missing values stay missing. Lap zero is excluded because it records
the starting order.

`pick_drivers()` accepts one or more car numbers or exact full names.
`pick_quicklaps(1.07)` keeps times below 107% of the fastest lap in the current
selection. Select a driver first to use that driver's pace. The filter doesn't
identify cautions or pit laps.

Flags refer to leader laps and stay separate from car laps. A lapped car's lap
number doesn't line up with the leader's. FlagState contains raw feed codes.

## Practice and qualifying

```python
practice = fastnascar.get_session(2026, race_id=5626, identifier="P")
practice.load(laps=False)
qualifying = fastnascar.get_session(2026, race_id=5626, identifier="Q")
qualifying.load(laps=False)
```

These sessions provide results and best lap times. When a weekend has several
runs of the same type, pass the exact `run_name` from the weekend feed.
Qualifying results may cover only the final round.

## Plot a race

```python
from fastnascar.plotting import plot_lap_times
import matplotlib.pyplot as plt

plot_lap_times(session.laps, drivers=["77", "5"])
plt.show()
```

The Bristol example saves a full-race chart, a pace detail view, and a CSV:

```sh
python examples/hocevar_bristol_2026.py
python examples/hocevar_bristol_2026.py --race spring --show
```

The default is the September 19, 2026 night race. Files go into `outputs/`.
On Windows, `examples/hocevar_bristol_2026.ps1` also runs the script using
an available Python installation or the bundled desktop runtime.

## Live timing

```python
import fastnascar

snapshot = fastnascar.get_live_session()
print(snapshot.timing)
print(snapshot.pit_stops)

for snapshot in fastnascar.iter_live(interval=2, max_updates=10):
    print(snapshot.timestamp)
    print(snapshot.timing.query("DriverNumber == '77'"))
```

The live feed serves the currently active session. It can't replay an earlier
race. Requests bypass the cache. Updates can repeat or become stale; check the
source timestamp. Pass `race_id=...` to require a specific race.

Polling raises an error if the feed switches race, timing run, or series.
The default delay is two seconds, with a minimum of one second. LastLapTime
and ElapsedTime are timedeltas; gaps and status retain the feed's values.
Polling can miss laps between updates.

Record and replay snapshots:

```python
fastnascar.record_live("race.jsonl", interval=2, max_updates=100)
for snapshot in fastnascar.replay_live("race.jsonl"):
    print(snapshot.timestamp, snapshot.timing.head())
```

Recordings append to the file and flush after each update. Replay runs offline.

## Cache

```python
fastnascar.Cache.enable_cache(".fastnascar-cache")
session = fastnascar.get_session(2026, race_id=5626).load()

# Reuse downloaded data without network access.
fastnascar.Cache.enable_cache(".fastnascar-cache", offline=True)
session = fastnascar.get_session(2026, race_id=5626).load()
```

Settings apply across the process. Cached responses expire after an hour.
`refresh=True` on `load()` fetches new data. Offline mode accepts old entries
and raises on a cache miss, including live requests.
`Cache.disable_cache()` restores network access without deleting files;
`Cache.clear_cache()` removes this package's cached responses.

Unavailable feeds raise `DataNotAvailableError`; unsupported schemas raise
`InvalidDataError`. Accessing a table before loading it raises
`SessionNotLoadedError`. A failed reload keeps the previous session tables.
Function docstrings describe arguments, return values, and errors.

## Data

Feeds are hosted at `https://cf.nascar.com`:

- Schedule: `cacher/{year}/race_list_basic.json`
- Results: `cacher/{year}/1/{race_id}/weekend-feed.json`
- Race laps and flags: `cacher/{year}/1/{race_id}/lap-times.json`
- Live timing: `live/feeds/live-feed.json`

Coverage depends on what NASCAR makes available. Verified examples include
the 2024 Food City 500, the 2026 Bristol night race, and Cup live timing.
Feed URLs and schemas can change.

Code is MIT licensed. NASCAR feed data and test excerpts aren't covered by
that license.

## Development

```sh
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m build
python -m twine check dist/*
```

Tests run offline with recorded feed excerpts and mocked requests. Examples
make real requests. CI runs the tests on Python 3.10, 3.12, and 3.13.
