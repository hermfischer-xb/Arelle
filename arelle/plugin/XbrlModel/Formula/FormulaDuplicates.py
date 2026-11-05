"""
FormulaDuplicates.py - duplicate resolution for the Tavi Query and Rules Language.

A fact query de-duplicates: where more than one reported value falls at the same
intersection, the query yields one value rather than one per report of it.  This
is the behaviour of XBRL Formula 1.0 and it matters in practice -- a total that a
report states both in a table and parenthetically in the surrounding text would
otherwise produce two iterations and double every sum computed from it.

Several XbrlFact objects with the same dimensions (Tavi's duplicate facts)
de-duplicate. Since the fact value object was collapsed into the fact, a value
reported in more than one place, or amended, is itself several facts.

Classification follows Tavi's duplicate fact validation: complete duplicates
agree on value and decimals; consistent duplicates are numeric and have
intersecting rounding intervals; anything else is inconsistent and cannot be
de-duplicated to a single value.

See COPYRIGHT.md for copyright information.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional, Sequence, Tuple

from arelle.ModelValue import QName


COMPLETE = "complete"
CONSISTENT = "consistent"
INCONSISTENT = "inconsistent"


def _asDecimal(value) -> Optional[Decimal]:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
    return None


def _decimalsOf(obj) -> Optional[int]:
    """The stated decimals, or None for an exact (infinite-precision) value."""
    d = getattr(obj, "decimals", None)
    if d is None:
        return None
    if isinstance(d, str):
        if d.strip().upper() in ("INF", "+INF"):
            return None
        try:
            return int(d)
        except ValueError:
            return None
    try:
        return int(d)
    except (TypeError, ValueError):
        return None


def _roundingInterval(value: Decimal, decimals: Optional[int]) -> Tuple[Decimal, Decimal]:
    """The closed interval a value with this accuracy could have come from."""
    if decimals is None:
        return (value, value)
    half = Decimal(5) * (Decimal(10) ** Decimal(-decimals - 1))
    return (value - half, value + half)


def classify(values: Sequence[Any], decimalsList: Sequence[Optional[int]]) -> str:
    """Classify a set of duplicate values as complete, consistent or inconsistent."""
    if len(values) < 2:
        return COMPLETE
    first = values[0]
    firstDec = decimalsList[0]
    if all(v == first for v in values) and all(d == firstDec for d in decimalsList):
        return COMPLETE

    decs = [_asDecimal(v) for v in values]
    if any(d is None for d in decs):
        # A non-numeric value that is not a complete duplicate of the others has
        # no rounding interval to reconcile, so it cannot be consistent.
        return INCONSISTENT

    lo, hi = _roundingInterval(decs[0], decimalsList[0])
    for value, dec in zip(decs[1:], decimalsList[1:]):
        vlo, vhi = _roundingInterval(value, dec)
        lo, hi = max(lo, vlo), min(hi, vhi)
        if lo > hi:
            return INCONSISTENT
    return CONSISTENT


def _mostPrecise(items: Sequence[Any], decimalsList: Sequence[Optional[int]]) -> Any:
    """The item stating the greatest accuracy; an exact value wins outright."""
    best, bestDec = items[0], decimalsList[0]
    for item, dec in zip(items[1:], decimalsList[1:]):
        if bestDec is None:
            break
        if dec is None or dec > bestDec:
            best, bestDec = item, dec
    return best


# ---------------------------------------------------------------------------
# The object carrying a fact's value
# ---------------------------------------------------------------------------

def effectiveFactValue(fact) -> Optional[Any]:
    """The object that carries the fact's value: the fact itself, when it has a value or
    value sources, else None.

    A fact has at most one value (the fact value object was collapsed into the fact), so
    there is nothing left to resolve here; the function remains so that the language's
    `factValue` type and its readers (value, decimals, isNil) keep one access point.
    """
    if getattr(fact, "value", None) is not None or getattr(fact, "sources", None):
        return fact
    return None


# ---------------------------------------------------------------------------
# Fact de-duplication (several XbrlFact at one intersection)
# ---------------------------------------------------------------------------

def _dimensionKey(fact) -> frozenset:
    dims = getattr(fact, "dimensions", None) or {}
    return frozenset(
        (k, v) for k, v in dims.items()
        if isinstance(k, QName)
    )


def deduplicateFacts(facts: List[Any], mode: Optional[str] = None) -> List[Any]:
    """Collapse duplicate facts, preserving the order they were matched in.

    mode is None for the default (complete and consistent duplicates collapse,
    inconsistent duplicates are all kept), "nodups" (inconsistent duplicates are
    dropped entirely), or "dups" (no de-duplication at all).
    """
    if mode == "dups" or len(facts) < 2:
        return facts

    groups: dict = {}
    order: List[frozenset] = []
    for fact in facts:
        key = _dimensionKey(fact)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(fact)

    out: List[Any] = []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            out.append(group[0])
            continue
        fvs = [effectiveFactValue(f) for f in group]
        values = [getattr(fv, "value", None) if fv is not None else None for fv in fvs]
        decs = [_decimalsOf(fv) if fv is not None else None for fv in fvs]
        kind = classify(values, decs)
        if kind == COMPLETE:
            out.append(group[0])
        elif kind == CONSISTENT:
            out.append(_mostPrecise(group, decs))
        elif mode == "nodups":
            continue
        else:
            out.extend(group)
    return out
