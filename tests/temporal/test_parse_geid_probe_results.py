"""Regression test for the by_city aggregation in parse_geid_probe_results.

A region whose anchors are all date-less (year_min/year_max == "") used to
crash the per-region aggregation because min()/max() were called over an empty
generator with no default. The fix adds default=None so date-less regions get
an empty cell instead of aborting before the summary CSV is written.
"""
import pandas as pd


# These two lambdas mirror the year_min / year_max aggregations in
# parse_geid_probe_results.main(). They are reproduced here because the agg is
# inline in main(); the assertions below pin the exact min()/max() default
# behavior the fix relies on.
_YEAR_MIN = lambda s: min((int(x) for x in s if x != ""), default=None)
_YEAR_MAX = lambda s: max((int(x) for x in s if x != ""), default=None)


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.groupby("region_key")
        .agg(
            anchors=("anchor_id", "nunique"),
            year_min=("year_min", _YEAR_MIN),
            year_max=("year_max", _YEAR_MAX),
        )
        .reset_index()
    )


def test_min_max_empty_generator_default_none():
    # The underlying construct: empty filtered generator must not raise.
    s = ["", ""]
    assert min((int(x) for x in s if x != ""), default=None) is None
    assert max((int(x) for x in s if x != ""), default=None) is None


def test_dateless_region_yields_missing_without_raising():
    df = pd.DataFrame(
        {
            "region_key": ["dateless", "dateless", "dated", "dated"],
            "anchor_id": ["a1", "a2", "b1", "b2"],
            "year_min": ["", "", "2014", "2016"],
            "year_max": ["", "", "2020", "2024"],
        }
    )

    by_city = _aggregate(df)  # must not raise ValueError

    # The lambdas return None for the date-less region; pandas coerces it to a
    # missing value (NaN) once the column also holds ints from the dated region.
    dateless = by_city.loc[by_city["region_key"] == "dateless"].iloc[0]
    assert pd.isna(dateless["year_min"])
    assert pd.isna(dateless["year_max"])

    dated = by_city.loc[by_city["region_key"] == "dated"].iloc[0]
    assert dated["year_min"] == 2014
    assert dated["year_max"] == 2024


def test_summary_csv_tolerates_none(tmp_path):
    # pandas writes None as an empty cell; to_csv must not raise.
    df = pd.DataFrame(
        {
            "region_key": ["dateless"],
            "anchor_id": ["a1"],
            "year_min": [""],
            "year_max": [""],
        }
    )
    by_city = _aggregate(df)  # all-dateless: lambda returns None
    out = tmp_path / "vintage_city_summary.csv"
    by_city.to_csv(out, index=False)  # must not raise
    text = out.read_text()
    assert "dateless" in text
    # None / NaN year columns serialise to empty cells.
    row = by_city.loc[by_city["region_key"] == "dateless"].iloc[0]
    assert pd.isna(row["year_min"]) or row["year_min"] is None
    assert pd.isna(row["year_max"]) or row["year_max"] is None
