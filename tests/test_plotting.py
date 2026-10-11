"""Check plotting units and missing-lap behavior with the optional plot extra."""
import importlib.util
import unittest

from test_features import lap_table


@unittest.skipUnless(importlib.util.find_spec("matplotlib"), "install the plot extra")
class PlotTests(unittest.TestCase):
    """Validate charts against small, hand-calculated timing selections."""

    def setUp(self):
        """Select a noninteractive backend and two cars with missing lap records."""
        import matplotlib
        matplotlib.use("Agg")
        self.laps = lap_table({"77": [(1, 12), (2, 10), (4, 11), (5, 13)],
                               "5": [(1, 10), (2, 9), (3, 10)]})

    def tearDown(self):
        """Close figures so tests do not leak plotting state."""
        import matplotlib.pyplot as plt
        plt.close("all")

    def test_comparison_units_and_position_orientation(self):
        """Plot delta seconds and keep first position at the top on reused axes."""
        from fastnascar.plotting import plot_comparison, plot_positions
        comparison = plot_comparison(self.laps, "77", "5")
        self.assertEqual(comparison.lines[0].get_ydata()[0], 2)
        positions = plot_positions(self.laps, drivers="77")
        plot_positions(self.laps, drivers="5", ax=positions)
        self.assertTrue(positions.yaxis_inverted())

    def test_rolling_chart_breaks_across_missing_car_laps(self):
        """The plotted rolling mean must not bridge an absent car lap."""
        import math
        from fastnascar.plotting import plot_rolling_pace
        axes = plot_rolling_pace(self.laps, window=2, drivers="77")
        line = axes.lines[0]
        self.assertEqual(list(line.get_xdata()), [1, 2, 3, 4, 5])
        self.assertEqual(line.get_ydata()[1], 11)
        self.assertTrue(math.isnan(line.get_ydata()[2]))
        self.assertTrue(math.isnan(line.get_ydata()[3]))
