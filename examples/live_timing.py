"""Show ten updates from the currently active NASCAR Cup feed."""
import fastnascar

for snapshot in fastnascar.iter_live(interval=2, max_updates=10):
    print(f"Race {snapshot.race_id}, lap {snapshot.lap_number}, flag {snapshot.flag_state}")
    print(snapshot.timing[["Position", "DriverNumber", "Driver", "LastLapTime"]].head())
