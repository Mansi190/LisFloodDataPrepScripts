"""NumPy 2 compatibility shim for LISFLOOD 4.3.1 / liscal.

The env has numpy 2.4.3, but lisflood 4.3.1 (site-packages) still uses
``np.bool8`` (21 call sites) and liscal uses ``np.NaN`` (7 call sites).
Both spellings were removed in NumPy 2.0; each had an exact equivalent that
still exists, so restoring the old names is faithful, not a behaviour change:

    np.bool8 -> np.bool_     (identical dtype alias)
    np.NaN   -> np.nan       (identical float value)

Placed on PYTHONPATH so Python imports it automatically at interpreter
startup -- including in the multiprocessing 'spawn' workers the DEAP
calibration pool creates on macOS, which inherit PYTHONPATH.

Remove this once lisflood/liscal are updated for NumPy 2.
"""
try:
    import numpy as _np

    if not hasattr(_np, "bool8"):
        _np.bool8 = _np.bool_
    if not hasattr(_np, "NaN"):
        _np.NaN = _np.nan
except Exception:  # never let the shim break interpreter startup
    pass
