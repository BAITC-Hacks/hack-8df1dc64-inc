"""Optional constant-pressure air-density correction, after the wind curve."""

from collections import defaultdict
from math import fsum, isfinite


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a finite number.")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError("Expected a finite number.") from exc
    if not isfinite(value):
        raise ValueError("Expected a finite number.")
    return value


def _kelvin(temperature_c):
    kelvin = 273.15 + _finite_number(temperature_c)
    if kelvin <= 0:
        raise ValueError("Temperature must be above absolute zero (-273.15 C).")
    return kelvin


def reference_temperatures(points):
    """Average each turbine's complete hours, already validated by build_curves.

    The caller must supply the same cutoff-limited training points as the curve.
    Forecast temperatures are never used to estimate these references.
    """
    groups = defaultdict(list)
    for point in points:
        temperature = point["temperature_c"]
        _kelvin(temperature)
        groups[point["turbine_id"]].append(temperature)
    return {turbine: fsum(value / len(values) for value in values)
            for turbine, values in groups.items()}


def temperature_corrected_power(base_power, temperature_c, reference_temperature_c):
    """Return base power * (T_ref + 273.15) / (T + 273.15), clipped to [0, 1].

    Assumes constant air pressure. Nonfinite inputs and physically impossible
    temperatures raise ValueError; they are never replaced with made-up weather.
    """
    power = _finite_number(base_power)
    reference_kelvin = _kelvin(reference_temperature_c)
    forecast_kelvin = _kelvin(temperature_c)
    # Keep zero power at zero even if an extreme density ratio overflows.
    if power <= 0:
        return 0.0
    corrected = power * (reference_kelvin / forecast_kelvin)
    return max(0.0, min(1.0, corrected))
