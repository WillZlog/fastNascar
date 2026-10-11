"""Optional plotting helpers. Importing fastnascar does not require matplotlib."""


def plot_lap_times(laps, *, drivers=None, ax=None):
    """Plot recorded lap times in seconds against each car's completed lap.

    Args:
        laps (fastnascar.Laps): Loaded laps, or a previously filtered selection.
        drivers (str, int, or iterable, optional): Exact full names or car numbers
            accepted by ``Laps.pick_drivers``. Omit to plot the whole selection.
        ax (matplotlib.axes.Axes, optional): Axes to draw on. Created if omitted.

    Returns:
        matplotlib.axes.Axes: Axes containing one line per selected car.

    Raises:
        ImportError: Matplotlib is not installed; install the ``plot`` extra.

    This helper includes slow laps. Call ``pick_quicklaps`` first to apply a
    pace threshold. Call ``plt.show()`` or ``ax.figure.savefig(...)`` to display
    or save the figure.
    """
    import matplotlib.pyplot as plt

    if ax is None:
        _, ax = plt.subplots()
    selected = laps if drivers is None else laps.pick_drivers(drivers)
    for number, rows in selected.groupby("DriverNumber", sort=False):
        rows = rows.sort_values("LapNumber")
        name = rows.Driver.iloc[0]
        ax.plot(rows.LapNumber, rows.LapTime.dt.total_seconds(), label=f"#{number} {name}")
    ax.set(xlabel="Car lap", ylabel="Lap time (seconds)")
    if len(selected):
        ax.legend()
    return ax
