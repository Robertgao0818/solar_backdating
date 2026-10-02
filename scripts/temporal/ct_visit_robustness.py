"""Robustness summaries for an irregular 0/1/u visit sequence.

``u`` frames are omitted from the usable sequence. They are not absences and
they do not break a run. "Consecutive" means successive usable observations in
time order, not successive calendar days.

A sequence is monotonic when no usable present is followed later by a usable
absent. Confirmation at k is the earliest run of k usable presents. The
confirm date is the day of the k-th present in that run. The last absent is
the latest usable absent before the run starts.
"""

from __future__ import annotations

from datetime import date

USABLE = frozenset({"0", "1"})
KS = (1, 2, 3)


def usable_series(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(day, code) for day, code in pairs if code in USABLE]


def reversal_count(series: list[tuple[str, str]]) -> int:
    count = 0
    previous = ""
    for _day, code in series:
        if previous == "1" and code == "0":
            count += 1
        previous = code
    return count


def is_monotonic(series: list[tuple[str, str]]) -> bool:
    """True when a usable present is never followed later by a usable absent."""
    return reversal_count(series) == 0


def max_consecutive_present(series: list[tuple[str, str]]) -> int:
    best = run = 0
    for _day, code in series:
        if code == "1":
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def confirming_run(series: list[tuple[str, str]], k: int) -> dict[str, str | int] | None:
    """Earliest run of ``k`` successive usable presents.

    Returns last_absent (blank if the run has no preceding absent), confirm_date,
    run_span_days (first present of the run to the k-th), and absent_gap_days
    (last absent to the confirm date; blank when there is no preceding absent).
    """
    if k < 1:
        raise ValueError(f"k must be positive, got {k}")
    run = 0
    start: int | None = None
    for index, (_day, code) in enumerate(series):
        if code != "1":
            run = 0
            start = None
            continue
        if run == 0:
            start = index
        run += 1
        if run < k or start is None:
            continue
        kth = start + k - 1
        last_absent = ""
        for earlier in range(start - 1, -1, -1):
            if series[earlier][1] == "0":
                last_absent = series[earlier][0]
                break
        first = date.fromisoformat(series[start][0])
        onset = date.fromisoformat(series[kth][0])
        gap: str | int = ""
        if last_absent:
            gap = (onset - date.fromisoformat(last_absent)).days
        return {
            "last_absent": last_absent,
            "confirm_date": onset.isoformat(),
            "run_span_days": (onset - first).days,
            "absent_gap_days": gap,
        }
    return None


def usable_code(series: list[tuple[str, str]]) -> str:
    return " ".join(code for _day, code in series)


def side_fields(prefix: str, pairs: list[tuple[str, str]]) -> dict[str, str | int]:
    series = usable_series(pairs)
    absent = sum(code == "0" for _day, code in series)
    present = sum(code == "1" for _day, code in series)
    fields: dict[str, str | int] = {
        f"{prefix}_n_absent": absent,
        f"{prefix}_n_present": present,
        f"{prefix}_monotonic": int(is_monotonic(series)),
        f"{prefix}_reversals": reversal_count(series),
        f"{prefix}_max_consecutive_present": max_consecutive_present(series),
        f"{prefix}_usable_code": usable_code(series),
    }
    for k in KS:
        found = confirming_run(series, k)
        fields[f"{prefix}_k{k}_reached"] = int(found is not None)
        fields[f"{prefix}_k{k}_last_absent"] = "" if found is None else found["last_absent"]
        fields[f"{prefix}_k{k}_confirm_date"] = "" if found is None else found["confirm_date"]
        fields[f"{prefix}_k{k}_run_span_days"] = "" if found is None else found["run_span_days"]
        fields[f"{prefix}_k{k}_absent_gap_days"] = "" if found is None else found["absent_gap_days"]
    return fields
