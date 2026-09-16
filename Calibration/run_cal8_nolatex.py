#!/usr/bin/env python
"""Run CAL_8_POSTPROCESSING without LaTeX text rendering.

CAL_8 hardcodes ``'text': {'usetex': True}`` in its PlotParameters class, which
liscal/evaluation.py applies via ``plt.rc('text', ...)``. That routes every label
through LaTeX, and this machine has TeXLive 2026 *basic*, which lacks type1cm.sty
and dvipng. Installing them needs sudo on /usr/local/texlive (root:wheel).

Rather than edit liscal, this loads CAL_8 as a module, flips that one flag, and
then replays CAL_8's own __main__ block verbatim. Only the font rendering
differs: mathtext instead of Computer Modern. Every number and figure is
identical.

To go back to LaTeX output: install the packages and use CAL_8 directly.

    sudo tlmgr install type1cm dvipng
    python bin/CAL_8_POSTPROCESSING.py <settings> <station>

Usage (from the lisflood-calibration checkout, same env as run_full_calibration.sh):
    python /path/to/Calibration/run_cal8_nolatex.py <settings_file> <station>
"""
import importlib.util
import os
import sys

CAL_8 = os.path.join(
    "/Users/mansi/Documents/LisFlood/lisflood-calibration",
    "bin", "CAL_8_POSTPROCESSING.py",
)


def load_cal8():
    """Import CAL_8 as a module. Its __main__ guard means nothing runs on import."""
    spec = importlib.util.spec_from_file_location("cal8", CAL_8)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def patch_grid_keyword():
    """matplotlib 3.5 renamed Axes.grid's ``b`` argument to ``visible``, and 3.10
    rejects the old spelling outright. liscal/evaluation.py:474 still calls
    ``ax.grid(b=True, axis='y')``. The two spellings mean exactly the same thing,
    so translating is faithful -- same shim rationale as compat/sitecustomize.py.

    Remove this once liscal supports matplotlib >= 3.5.
    """
    from matplotlib.axes import Axes

    original = Axes.grid

    def grid(self, visible=None, which="major", axis="both", **kwargs):
        if "b" in kwargs:
            visible = kwargs.pop("b")
        return original(self, visible, which=which, axis=axis, **kwargs)

    Axes.grid = grid


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run_cal8_nolatex.py <settings_file> <station>")
    settings_file, station = sys.argv[1], sys.argv[2]

    cal8 = load_cal8()

    # the one change: don't render through LaTeX
    cal8.PlotParameters.text["text"] = {"usetex": False}
    patch_grid_keyword()

    from liscal import subcatchment, objective, products

    cfg = cal8.ConfigPostProcessing(settings_file)
    obsid = int(station)

    print("=================== " + str(obsid) + " ====================")
    subcatch = subcatchment.SubCatchment(cfg, obsid, initialise=False)

    best = os.path.join(subcatch.path, "out", "streamflow_simulated_best.csv")
    if not os.path.exists(best):
        raise Exception("Calibration not complete! Cannot generate products: " + best)

    obj = objective.ObjectiveKGE(cfg, subcatch)
    products.create_products(cfg, subcatch, obj)

    print("==================== END ====================")
