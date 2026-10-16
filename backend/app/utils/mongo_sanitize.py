"""
Scrub the values MongoDB will happily store but JSON cannot represent.

`NaN`, `Infinity` and `-Infinity` are legal IEEE-754 doubles and PyMongo writes
them without complaint, but they are NOT legal JSON: Starlette's JSONResponse
serializes with `json.dumps(..., allow_nan=False)`, which raises
`ValueError: Out of range float values are not JSON compliant` and turns the
whole request into a 500.

Pandas makes this trivial to hit. An empty CSV cell becomes `float('nan')`, so
a straight row-to-dict copy — the seeder's `spatial_signature` path — writes a
NaN double into what the entity model declares as `Optional[str]`. One bad
document then poisons every endpoint that returns it, including endpoints that
only embed it (e.g. `/api/equipment/{id}` through `recent_lots`).

`scrub_nans` is the single chokepoint for the fix: it walks a structure and
replaces non-finite floats (and pandas `NaT`) with `None`, which PyMongo stores
as BSON null and every JSON serializer accepts. It is applied on the way INTO
MongoDB (repositories, seeder) rather than on the way out, so the stored data
itself stops being poisonous.
"""
import math
from typing import Any


def scrub_nans(value: Any) -> Any:
    """Recursively replace non-finite floats (NaN/±Inf) and pandas NaT with None.

    Handles the shapes documents actually arrive in: dicts, lists, tuples, and
    anything float-like (numpy scalars, `decimal.Decimal`) via `math.isfinite`,
    which converts through `__float__`. Anything else — strings, datetimes,
    ObjectIds — passes through untouched.
    """
    if isinstance(value, dict):
        return {key: scrub_nans(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_nans(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_nans(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    try:
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):  # not numeric — datetime, ObjectId, ...
        pass
    if type(value).__name__ == "NaTType":  # pandas.NaT, checked by name: no pandas import here
        return None
    return value
