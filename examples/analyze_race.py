"""Run after installing: python examples/analyze_race.py"""
import fastnascar

fastnascar.Cache.enable_cache(".fastnascar-cache")
session = fastnascar.get_session(2024, race_id=5392).load()
print(session.results[["Position", "DriverNumber", "FullName", "Points"]].head(10))
hamlin = session.laps.pick_driver("11")
print(hamlin.pick_fastest())
print(hamlin.pick_quicklaps().LapTime.dt.total_seconds().describe())
