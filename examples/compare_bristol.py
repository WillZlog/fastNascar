"""Compare Hocevar and Larson, with pit timing and rolling pace at Bristol 2026."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fastnascar


def main():
    """Load the Bristol night race and save pace, position, and delta plots.

    Returns:
        None: Writes a PNG to the repository's outputs directory.

    Raises:
        ImportError: Matplotlib is missing; install the plot extra.
        FastNASCARException: Required feed data is unavailable or malformed.
        OSError: The output cannot be written.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from fastnascar.plotting import plot_comparison, plot_positions, plot_rolling_pace

    root = Path(__file__).resolve().parents[1]
    fastnascar.Cache.enable_cache(root / ".fastnascar-cache")
    session = fastnascar.get_session(2026, race_id=5626).load(pit_stops=True, messages=True)
    print(fastnascar.best_lap_average(session.laps, 10).head(10).to_string(index=False))
    print(fastnascar.summarize_stints(session.laps.pick_driver("77"), session.pit_stops).to_string(index=False))
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), layout="constrained")
    plot_rolling_pace(session.laps, drivers=["77", "5"], window=10, ax=axes[0])
    plot_positions(session.laps, drivers=["77", "5"], ax=axes[1])
    plot_comparison(session.laps, "77", "5", ax=axes[2])
    fig.suptitle("Bristol 2026 night race · Hocevar (#77) vs Larson (#5)")
    axes[2].set_title("Per-lap pace difference · matching car laps, not on-track gap", fontsize=10)
    output = root / "outputs" / "bristol_2026_comparison.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    plt.close(fig)
    print(output)


if __name__ == "__main__":
    main()
