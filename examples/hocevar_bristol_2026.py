"""Chart Carson Hocevar's lap times at Bristol in 2026 using real NASCAR data.

From the repository root:
    python -m pip install -e ".[plot]"
    python examples/hocevar_bristol_2026.py
    python examples/hocevar_bristol_2026.py --race spring --show

The default is the September 19 Bass Pro Shops Night Race (race ID 5626).
"""
import argparse
from pathlib import Path
import sys

# Also works directly from the checkout when pandas/matplotlib are installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fastnascar

RACES = {"fall": 5626, "spring": 5604}


def main():
    """Parse CLI options, load Hocevar's race laps, and write PNG/SVG/CSV files.

    Returns:
        None

    Raises:
        SystemExit: Invalid CLI options, missing plotting dependencies, unavailable
            race data, or no usable Hocevar lap times.
        OSError: Output files cannot be created.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--race", choices=RACES, default="fall")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "outputs")
    parser.add_argument("--show", action="store_true", help="Open the chart in a matplotlib window")
    parser.add_argument("--refresh", action="store_true", help="Fetch fresh lap and result data")
    args = parser.parse_args()

    try:
        import matplotlib
        if not args.show:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import MultipleLocator
    except ImportError:
        parser.exit(1, 'Plotting requires matplotlib. Run: python -m pip install -e ".[plot]"\n')

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    fastnascar.Cache.enable_cache(Path(__file__).resolve().parents[1] / ".fastnascar-cache")
    try:
        session = fastnascar.get_session(2026, race_id=RACES[args.race]).load(refresh=args.refresh)
    except fastnascar.FastNASCARException as exc:
        parser.exit(1, f"Could not load the Bristol race: {exc}\n")

    # Results use a plain full name; lap feeds may add a '(C)' playoff suffix.
    driver = session.get_driver("Carson Hocevar")
    laps = session.laps.loc[session.laps.DriverId == driver.DriverId].sort_values("LapNumber").copy()
    seconds = laps.LapTime.dt.total_seconds()
    if laps.empty or seconds.notna().sum() == 0:
        parser.exit(1, "No lap times were available for Carson Hocevar.\n")

    # Reindex missing lap numbers so the line breaks across any absent records.
    plot_seconds = seconds.set_axis(laps.LapNumber.astype(int)).reindex(range(1, int(laps.LapNumber.max()) + 1))
    median = plot_seconds.rolling(10, min_periods=10).median()
    fastest = laps.pick_fastest()
    base = f"hocevar_bristol_2026_{args.race}"
    csv_path = output_dir / f"{base}.csv"
    png_path = output_dir / f"{base}.png"
    svg_path = output_dir / f"{base}.svg"
    export = laps.copy()
    export["LapTimeSeconds"] = seconds
    export["Rolling10LapMedianSeconds"] = laps.LapNumber.map(median)
    export.to_csv(csv_path, index=False)

    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False}):
        fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True,
                                 gridspec_kw={"height_ratios": [1, 1.4]}, layout="constrained")
        fig.patch.set_facecolor("#f7f9fc")
        date = session.event.EventDate.strftime("%B %d, %Y")
        fig.suptitle(f"Carson Hocevar · #{driver.DriverNumber} · Bristol 2026\n"
                     f"{session.event.EventName} · {date}", fontsize=17, fontweight="bold")
        for ax in axes:
            ax.set_facecolor("white")
            ax.plot(plot_seconds.index, plot_seconds, color="#2374ab", linewidth=0.8,
                    marker=".", markersize=2.4, alpha=0.85, label="Recorded lap time")
            ax.set_ylabel("Lap time (seconds)")
            ax.grid(axis="y", color="#dce3ea", linewidth=0.6)
            ax.set_xlim(1, max(2, int(laps.LapNumber.max())))
            ax.xaxis.set_major_locator(MultipleLocator(50))
        axes[0].set_title("All laps · full time range, including slow laps", loc="left", fontsize=11)
        axes[0].legend(loc="upper right", frameon=False)
        axes[1].plot(median.index, median, color="#e07826", linewidth=1.8,
                     label="10-lap rolling median (all recorded laps)")
        # Same data in both panels; only the lower panel's displayed y-range is limited.
        lower = max(0, float(seconds.min()) - 0.5)
        upper = max(lower + 3, float(seconds.quantile(0.75)) + 2)
        axes[1].set_ylim(lower, upper)
        axes[1].set_title(f"Pace detail · y-axis zoom {lower:.1f}–{upper:.1f} s; slower laps remain above",
                          loc="left", fontsize=11)
        axes[1].scatter([int(fastest.LapNumber)], [fastest.LapTime.total_seconds()],
                        color="#b93645", s=38, zorder=5,
                        label=f"Fastest: {fastest.LapTime.total_seconds():.3f} s (lap {fastest.LapNumber})")
        axes[1].legend(loc="upper right", frameon=False, fontsize=9)
        axes[1].set_xlabel("Hocevar's completed car lap")
        fig.supxlabel("Source: NASCAR lap-times feed · No laps filtered · Gaps indicate missing data", fontsize=9)
        fig.savefig(png_path, dpi=180, facecolor=fig.get_facecolor())
        fig.savefig(svg_path, facecolor=fig.get_facecolor())
        if args.show:
            plt.show()
        plt.close(fig)

    print(f"{session.event.EventName} (race ID {session.event.RaceId})")
    print(f"#{driver.DriverNumber} {driver.FullName}: {len(laps)} laps; finish P{driver.Position}")
    print(f"Fastest lap: {fastest.LapTime.total_seconds():.3f}s on lap {fastest.LapNumber}")
    for path in (png_path, svg_path, csv_path):
        print(path)


if __name__ == "__main__":
    main()
