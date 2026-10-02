#!/usr/bin/env python3
"""Attach per-visit 0/1 sequences to the Cape Town citywide install-date table.

The 2026-09-02 research preview stores interval endpoints and counts. The
visit-level sequence for the 90,348 citywide_27wave anchors lives in
``e8_descriptive_20260902/merged_scan_states/*.json``. This builder copies the
frozen CSV/GPKG and adds those sequences. It does not modify the checksummed
2026-09-02 files.

Top-52 rows keep blank sequence columns. Their ``scan_state_path`` values point
at a vanished ``/tmp`` directory and the JSON is not in the citywide tree.

``obs_code_raw`` is the model verdict. ``obs_code_repaired`` is the sequence
``infer_install_dates.py`` used after isolated-dip repair, which is the sequence
the published ``n_absent`` / ``n_present`` / ``n_unusable`` counts describe.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.temporal.build_ct_citywide_install_dated_deliverable import (
    CSV_FIELDS_CITYWIDE,
)
from scripts.temporal.build_ct_install_dated_deliverable import sha256
from scripts.temporal.infer_install_dates import (
    _all_results,
    _evidence_results,
    apply_dip_repair,
)
from scripts.temporal.scan_state import RoundResult, ScanState, load_scan_state

CITY_ROOT = (
    Path.home() / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
)
DEFAULT_SOURCE_CSV = (
    CITY_ROOT / "deliverable_2026-09-02/ct_citywide_install_dated_20260902.csv"
)
DEFAULT_SOURCE_GPKG = (
    CITY_ROOT / "deliverable_2026-09-02/ct_citywide_install_dated_20260902.gpkg"
)
DEFAULT_STATES_DIR = CITY_ROOT / "e8_descriptive_20260902/merged_scan_states"
DEFAULT_OUTPUT_DIR = CITY_ROOT / "sequences_20260923"

EXPECTED_ROWS = 111_801
EXPECTED_WAVE = 90_348
EXPECTED_TOP52 = 21_453
EXPECTED_CSV_SHA256 = (
    "c0bf9ed149fb73a97f34354c82f881df3f45769cfafe1e44e9fc68b1e29e7563"
)
EXPECTED_GPKG_SHA256 = (
    "30251ca685d879efd4ec3b1d65cef214b68ec3fff2e671a9c372e875474f17dd"
)
CENSUS_CUTOFF = "2025-01-31"
WAVE_COHORT = "citywide_27wave"
TOP52_COHORT = "top52"
SCHEMA_VERSION = "ct_presence_sequence_v1"
FEATURE_LAYER = "ct_solar_install_dated"
LONG_TABLE = "presence_observations"
FROZEN_STAMP = "2026-09-23T00:00:00.000Z"
DATE_BOUND_STATUSES = ("done_appears", "done_installed_during_census")
EARLIEST_PRESENT_STATUSES = (
    "done_appears",
    "done_already_present_before_geid_history",
)

SEQUENCE_FIELDS = (
    "sequence_coverage",
    "obs_dates",
    "obs_code_raw",
    "obs_code_repaired",
    "dip_repaired_dates",
    "ref_dates",
    "ref_code_raw",
)
LONG_FIELDS = (
    "source_anchor_id",
    "cohort",
    "frame_role",
    "obs_index",
    "file_order",
    "capture_date",
    "code_raw",
    "code_repaired",
    "quality_flag",
    "confidence",
    "decision_source",
    "dip_repaired",
)
WIDE_CSV_NAME = "ct_citywide_install_dated_20260902_with_sequences.csv"
WIDE_GPKG_NAME = "ct_citywide_install_dated_20260902_with_sequences.gpkg"
LONG_CSV_NAME = "ct_citywide_presence_observations_20260923.csv"


@dataclass(frozen=True)
class Visit:
    file_order: int
    capture_date: str
    code_raw: str
    code_repaired: str
    quality_flag: str
    confidence: float | None
    decision_source: str

    @property
    def dip_repaired(self) -> bool:
        return self.code_raw != self.code_repaired


@dataclass(frozen=True)
class EncodedSequence:
    obs_dates: tuple[str, ...]
    obs_code_raw: tuple[str, ...]
    obs_code_repaired: tuple[str, ...]
    dip_repaired_dates: tuple[str, ...]
    ref_dates: tuple[str, ...]
    ref_code_raw: tuple[str, ...]
    evidence: tuple[Visit, ...]
    reference: tuple[Visit, ...]
    file_order_was_chronological: bool

    @property
    def duplicate_evidence_dates(self) -> bool:
        return len(set(self.obs_dates)) != len(self.obs_dates)


@dataclass(frozen=True)
class Reconcile:
    issues: tuple[str, ...]
    earliest_clamped: bool
    census_earliest_outside_sequence: bool
    repair_changed_counts: bool


def result_code(result: RoundResult) -> str:
    """Map one scored frame to the published count alphabet.

    ``0`` and ``1`` are usable absence and presence. ``u`` is every frame the
    interval counter treats as unusable: quality other than ``usable``, or a
    missing presence verdict.
    """
    if result.quality_flag != "usable" or result.pv_present is None:
        return "u"
    if result.pv_present is True:
        return "1"
    if result.pv_present is False:
        return "0"
    raise ValueError(f"unexpected pv_present {result.pv_present!r}")


def _capture_day(capture_date: str, anchor_id: str) -> str:
    day = capture_date.strip()[:10]
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        raise ValueError(f"{anchor_id}: unusable capture_date {capture_date!r}")
    date.fromisoformat(day)
    return day


def format_dates(dates: tuple[str, ...] | list[str]) -> str:
    return ";".join(dates)


def format_codes(codes: tuple[str, ...] | list[str]) -> str:
    return " ".join(codes)


def format_confidence(value: float | None) -> str:
    if value is None:
        return ""
    return format(value, ".6g")


def encode_scan_state(state: ScanState) -> EncodedSequence:
    """Evidence sequence in capture-date order, plus reference frames apart.

    Evidence uses the same census fence and dip repair as
    ``infer_install_dates.py``: frames marked reference-only, and frames on or
    after ``state.census_date``, stay out of the install-date sequence.
    """
    cutoff = (state.census_date or "").strip()[:10]
    if cutoff != CENSUS_CUTOFF:
        raise ValueError(f"{state.anchor_id}: census_date {cutoff!r} != {CENSUS_CUTOFF}")
    census_mid = date.fromisoformat(cutoff)
    raw_all = _all_results(state)
    evidence_ids = {id(result) for result in _evidence_results(state, census_mid)}
    repaired_state, library_days = apply_dip_repair(state, census_mid_date=census_mid)
    repaired_all = _all_results(repaired_state)
    if len(raw_all) != len(repaired_all):
        raise ValueError(f"{state.anchor_id}: dip repair changed the result count")

    evidence: list[Visit] = []
    reference: list[Visit] = []
    for index, (raw, repaired) in enumerate(zip(raw_all, repaired_all, strict=True)):
        day = _capture_day(raw.capture_date, state.anchor_id)
        repaired_day = _capture_day(repaired.capture_date, state.anchor_id)
        if repaired_day != day:
            raise ValueError(f"{state.anchor_id}: dip repair moved {day} to {repaired_day}")
        visit = Visit(
            file_order=index,
            capture_date=day,
            code_raw=result_code(raw),
            code_repaired=result_code(repaired),
            quality_flag=raw.quality_flag,
            confidence=None if raw.confidence is None else float(raw.confidence),
            decision_source=raw.decision_source,
        )
        if id(raw) in evidence_ids:
            if day >= cutoff:
                raise ValueError(f"{state.anchor_id}: evidence frame {day} is on/after census")
            evidence.append(visit)
        else:
            reference.append(visit)

    file_order_was_chronological = [visit.capture_date for visit in evidence] == sorted(
        visit.capture_date for visit in evidence
    )
    evidence_sorted = tuple(sorted(evidence, key=lambda visit: (visit.capture_date, visit.file_order)))
    reference_sorted = tuple(
        sorted(reference, key=lambda visit: (visit.capture_date, visit.file_order))
    )
    dip_days = tuple(
        sorted({visit.capture_date for visit in evidence_sorted if visit.dip_repaired})
    )
    if set(library_days) != set(dip_days):
        raise ValueError(
            f"{state.anchor_id}: dip-repair dates {list(library_days)} != code changes {list(dip_days)}"
        )
    return EncodedSequence(
        obs_dates=tuple(visit.capture_date for visit in evidence_sorted),
        obs_code_raw=tuple(visit.code_raw for visit in evidence_sorted),
        obs_code_repaired=tuple(visit.code_repaired for visit in evidence_sorted),
        dip_repaired_dates=dip_days,
        ref_dates=tuple(visit.capture_date for visit in reference_sorted),
        ref_code_raw=tuple(visit.code_raw for visit in reference_sorted),
        evidence=evidence_sorted,
        reference=reference_sorted,
        file_order_was_chronological=file_order_was_chronological,
    )


def _count_code(codes: tuple[str, ...], token: str) -> int:
    return sum(code == token for code in codes)


def _day_field(value: str) -> str:
    value = (value or "").strip()
    return value[:10] if value else ""


def reconcile_sequence(
    *,
    scan_status: str,
    n_observations: int,
    n_absent: int,
    n_present: int,
    n_unusable: int,
    latest_absent_date: str,
    earliest_present_date: str,
    scan_notes: str,
    encoded: EncodedSequence,
) -> Reconcile:
    """Check the repaired sequence against the published interval counts.

    Census-bounded rows store the census/flight upper bound in
    ``earliest_present_date``. That day is an analysis bound, so it is allowed
    to be absent from the visit list. A Vexcel present-side clamp is likewise
    allowed only when the interval notes say ``present_clamped`` and the
    replacement day is not itself a visit.
    """
    issues: list[str] = []
    observed = len(encoded.obs_dates)
    absent = _count_code(encoded.obs_code_repaired, "0")
    present = _count_code(encoded.obs_code_repaired, "1")
    unusable = _count_code(encoded.obs_code_repaired, "u")
    if absent + present + unusable != observed:
        issues.append("code_alphabet")
    if observed != n_observations:
        issues.append("count_observations")
    if absent != n_absent:
        issues.append("count_absent")
    if present != n_present:
        issues.append("count_present")
    if unusable != n_unusable:
        issues.append("count_unusable")
    if _count_code(encoded.obs_code_raw, "u") != unusable:
        issues.append("repair_changed_unusable")

    zeros = [
        day for day, code in zip(encoded.obs_dates, encoded.obs_code_repaired, strict=True) if code == "0"
    ]
    ones = [
        day for day, code in zip(encoded.obs_dates, encoded.obs_code_repaired, strict=True) if code == "1"
    ]
    if scan_status in DATE_BOUND_STATUSES:
        latest = _day_field(latest_absent_date)
        expected_latest = max(zeros) if zeros else ""
        if latest != expected_latest:
            issues.append("latest_absent")

    earliest = _day_field(earliest_present_date)
    expected_earliest = min(ones) if ones else ""
    clamped = False
    if scan_status in EARLIEST_PRESENT_STATUSES and earliest != expected_earliest:
        clamped = (
            "present_clamped" in (scan_notes or "")
            and bool(earliest)
            and earliest not in encoded.obs_dates
        )
        if not clamped:
            issues.append("earliest_present")
    census_outside = (
        scan_status == "done_installed_during_census"
        and bool(earliest)
        and earliest not in encoded.obs_dates
    )
    repair_changed_counts = _count_code(encoded.obs_code_raw, "0") != absent or _count_code(
        encoded.obs_code_raw, "1"
    ) != present
    return Reconcile(
        issues=tuple(issues),
        earliest_clamped=clamped,
        census_earliest_outside_sequence=census_outside,
        repair_changed_counts=repair_changed_counts,
    )


def sequence_columns(encoded: EncodedSequence | None, coverage: str) -> dict[str, str]:
    if encoded is None:
        return {field: "" if field != "sequence_coverage" else coverage for field in SEQUENCE_FIELDS}
    return {
        "sequence_coverage": coverage,
        "obs_dates": format_dates(encoded.obs_dates),
        "obs_code_raw": format_codes(encoded.obs_code_raw),
        "obs_code_repaired": format_codes(encoded.obs_code_repaired),
        "dip_repaired_dates": format_dates(encoded.dip_repaired_dates),
        "ref_dates": format_dates(encoded.ref_dates),
        "ref_code_raw": format_codes(encoded.ref_code_raw),
    }


def _require_hash(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise SystemExit(f"{path} sha256 {actual} != frozen {expected}")


def _read_source_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    if fieldnames != list(CSV_FIELDS_CITYWIDE):
        raise SystemExit(f"source CSV columns differ from CSV_FIELDS_CITYWIDE: {fieldnames}")
    return fieldnames, rows


def _prepare_gpkg(source: Path, dest: Path) -> sqlite3.Connection:
    shutil.copy2(source, dest)
    connection = sqlite3.connect(dest)
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=MEMORY")
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({FEATURE_LAYER})")}
    missing = [field for field in SEQUENCE_FIELDS if field not in columns]
    for field in missing:
        connection.execute(f"ALTER TABLE {FEATURE_LAYER} ADD COLUMN {field} TEXT")
    connection.execute(
        f"""
        CREATE TABLE {LONG_TABLE} (
            fid INTEGER PRIMARY KEY,
            source_anchor_id TEXT NOT NULL,
            cohort TEXT NOT NULL,
            frame_role TEXT NOT NULL,
            obs_index INTEGER NOT NULL,
            file_order INTEGER NOT NULL,
            capture_date TEXT NOT NULL,
            code_raw TEXT NOT NULL,
            code_repaired TEXT NOT NULL,
            quality_flag TEXT,
            confidence REAL,
            decision_source TEXT,
            dip_repaired INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO gpkg_contents (
            table_name, data_type, identifier, description, last_change, srs_id
        ) VALUES (?, 'attributes', ?, ?, ?, NULL)
        """,
        (
            LONG_TABLE,
            LONG_TABLE,
            "One row per GEHI visit. frame_role=evidence is the install-date sequence.",
            FROZEN_STAMP,
        ),
    )
    connection.commit()
    return connection


def _geom_checksum(connection: sqlite3.Connection) -> tuple[int, int]:
    count, total = connection.execute(
        f"SELECT COUNT(*), COALESCE(SUM(length(geom)), 0) FROM {FEATURE_LAYER}"
    ).fetchone()
    return int(count), int(total)


def _park_spatial_triggers(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    """Drop rtree triggers that call SpatiaLite functions.

    Python's sqlite3 can update attribute columns, but the GeoPackage rtree
    triggers call ``ST_IsEmpty`` and this interpreter has no SpatiaLite loaded.
    The triggers are recreated after the attribute write. Geometry bytes are
    not updated, so the existing rtree stays valid.
    """
    rows = connection.execute(
        """
        SELECT name, sql FROM sqlite_master
        WHERE type='trigger' AND sql LIKE '%ST_IsEmpty%'
        """
    ).fetchall()
    saved = [(str(name), str(sql)) for name, sql in rows if sql]
    for name, _sql in saved:
        connection.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    connection.commit()
    return saved


def _restore_spatial_triggers(connection: sqlite3.Connection, saved: list[tuple[str, str]]) -> None:
    for _name, sql in saved:
        connection.execute(sql)


def _render_readme(stats: dict[str, object]) -> str:
    return f"""# 开普敦全城安装日表，附逐次 0/1 序列

日期：2026-09-23。模式版本：`{SCHEMA_VERSION}`。

这是 2026-09-02 研究预览表的加列版，一共 {EXPECTED_ROWS:,} 行。原来的 37 列原样保留。新列把每一次历史影像的日期和判定写在同一行上。冻结的 `deliverable_2026-09-02/` 和 Dropbox 上的 `ct_citywide_install_dated_2026-09-02_research_preview/` 都没有改。

## 这三个文件各是什么

- `{WIDE_CSV_NAME}`：一行一座安装。用表格软件或 pandas 直接打开。
- `{WIDE_GPKG_NAME}`：同一批多边形，坐标系 EPSG:32734，图层 `{FEATURE_LAYER}`。里面还有一张长表 `{LONG_TABLE}`，一次观测一行。
- `{LONG_CSV_NAME}`：和 GPKG 里的长表相同，给 pandas 用。

读宽表时把序列列当文本。`obs_code_raw` 形如 `0 u 0 1 1`，中间有空格，避免表格软件把前导 0 吃掉。

同一目录可以用 `scripts/temporal/build_ct_presence_panel.py` 把序列转成锚点×日期面板：一列一个证据观测日，格子是 `0`/`1`/`u`，空格表示这一天没有拍到这座安装。

## 序列怎么读

`obs_dates` 用分号分开，`obs_code_raw` 和 `obs_code_repaired` 用空格分开。三个字段一样长，第 i 个位置是同一次观测。

- `0`：这一天模型判定没有光伏，而且这张图可用（usable）。
- `1`：这一天判定有光伏，而且这张图可用。
- `u`：这一天看不清。质量不是 usable，或者判定是空的。它计入 `n_unusable`，不计入「没有」或「有」。

`obs_code_raw` 是模型原始判定。`obs_code_repaired` 是写成安装区间时用的序列。孤立凹陷修补（dip repair）会把夹在两次有把握的「有」中间的「没有」改成「有」；被改的日期写在 `dip_repaired_dates`。表上的 `n_absent`、`n_present`、`n_unusable`、`n_observations` 对的是 repaired 这一列。

`ref_dates` 和 `ref_code_raw` 是普查日 {CENSUS_CUTOFF} 当天及之后的帧，以及标成 reference_only 的帧。它们留在文件里供核对，不进 `n_observations`，也不要拼进安装日序列。

普查上界那一行（`scan_status=done_installed_during_census`）的 `earliest_present_date` 是普查或航飞给出的上界，通常不是序列里的某一天。`sequence_coverage=complete` 且备注里有 `present_clamped` 的行，最早「有」的日期也可能被航飞日期替换，那一天同样不在序列里。

## 覆盖

| sequence_coverage | 行数 | 含义 |
|---|---:|---|
| `complete` | {stats["n_complete"]:,} | 后 27 个波次。序列来自 `merged_scan_states/` 里对应的 JSON |
| `missing_scan_state` | {stats["n_missing_scan_state"]:,} | Top-52。路径指向当时的临时目录，JSON 不在本地这棵树里，2026-09-02 的交付包里也没有 |

证据帧 {stats["n_evidence_visits"]:,} 次，参照帧 {stats["n_reference_visits"]:,} 次。修补改过判定的安装有 {stats["n_anchors_dip_repaired"]:,} 座，共 {stats["n_visits_flipped"]:,} 个日期。次数对不上的行：{stats["n_issue_rows"]}。最后一次「没有」对不上的行：{stats["n_latest_absent_mismatches"]}。

长表的 `obs_index` 是该安装、该 `frame_role` 内按日期排的序号，从 0 起。`file_order` 是 JSON 里原来的位置。宽表的日期按时间排；{stats["n_not_chronological"]:,} 座安装的 JSON 原顺序和时间顺序不同，以宽表的时间顺序为准。

## 对账

构建时用 `infer_install_dates.py` 的同一道普查日过滤和孤立凹陷修补重算了 repaired 序列，并和宽表上的次数、`latest_absent_date` 核对。明细在 `gates.json`。源 CSV 的 sha256 仍是 `{EXPECTED_CSV_SHA256}`。
"""


def _write_long_row(
    writer: csv.DictWriter,
    batch: list[tuple],
    *,
    anchor_id: str,
    cohort: str,
    frame_role: str,
    visits: tuple[Visit, ...],
) -> None:
    for obs_index, visit in enumerate(visits):
        writer.writerow(
            {
                "source_anchor_id": anchor_id,
                "cohort": cohort,
                "frame_role": frame_role,
                "obs_index": obs_index,
                "file_order": visit.file_order,
                "capture_date": visit.capture_date,
                "code_raw": visit.code_raw,
                "code_repaired": visit.code_repaired,
                "quality_flag": visit.quality_flag,
                "confidence": format_confidence(visit.confidence),
                "decision_source": visit.decision_source,
                "dip_repaired": int(visit.dip_repaired),
            }
        )
        batch.append(
            (
                anchor_id,
                cohort,
                frame_role,
                obs_index,
                visit.file_order,
                visit.capture_date,
                visit.code_raw,
                visit.code_repaired,
                visit.quality_flag,
                visit.confidence,
                visit.decision_source,
                int(visit.dip_repaired),
            )
        )


def _flush_batches(
    connection: sqlite3.Connection,
    feature_batch: list[tuple],
    long_batch: list[tuple],
) -> None:
    if feature_batch:
        connection.executemany(
            f"""
            UPDATE {FEATURE_LAYER}
            SET sequence_coverage=?, obs_dates=?, obs_code_raw=?, obs_code_repaired=?,
                dip_repaired_dates=?, ref_dates=?, ref_code_raw=?
            WHERE fid=?
            """,
            feature_batch,
        )
        feature_batch.clear()
    if long_batch:
        connection.executemany(
            f"""
            INSERT INTO {LONG_TABLE} (
                source_anchor_id, cohort, frame_role, obs_index, file_order, capture_date,
                code_raw, code_repaired, quality_flag, confidence, decision_source, dip_repaired
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            long_batch,
        )
        long_batch.clear()
    connection.commit()


def _sample(bucket: list[dict[str, str]], item: dict[str, str], limit: int = 20) -> None:
    if len(bucket) < limit:
        bucket.append(item)


def build(args: argparse.Namespace) -> dict[str, object]:
    output_dir = args.output_dir
    partial = output_dir.with_name(output_dir.name + ".partial")
    if output_dir.exists() or partial.exists():
        raise SystemExit(f"refusing to overwrite {output_dir} or {partial}")
    _require_hash(args.source_csv, EXPECTED_CSV_SHA256)
    _require_hash(args.source_gpkg, EXPECTED_GPKG_SHA256)
    fieldnames, rows = _read_source_rows(args.source_csv)
    if len(rows) != EXPECTED_ROWS:
        raise SystemExit(f"expected {EXPECTED_ROWS} rows, found {len(rows)}")
    partial.mkdir(parents=True)
    log_path = partial / "build.log"
    log_handle = log_path.open("w", encoding="utf-8")

    def log(message: str) -> None:
        print(message, file=sys.stderr)
        log_handle.write(message + "\n")
        log_handle.flush()

    connection = _prepare_gpkg(args.source_gpkg, partial / WIDE_GPKG_NAME)
    geom_before = _geom_checksum(connection)
    parked_triggers = _park_spatial_triggers(connection)
    fid_by_anchor = {
        anchor_id: fid
        for anchor_id, fid in connection.execute(
            f"SELECT source_anchor_id, fid FROM {FEATURE_LAYER}"
        )
    }
    if len(fid_by_anchor) != EXPECTED_ROWS:
        raise SystemExit(f"gpkg anchor ids are not unique: {len(fid_by_anchor)}")

    wide_fields = fieldnames + list(SEQUENCE_FIELDS)
    wide_path = partial / WIDE_CSV_NAME
    long_path = partial / LONG_CSV_NAME
    started = time.time()
    stats: dict[str, object] = {
        "n_complete": 0,
        "n_missing_scan_state": 0,
        "n_evidence_visits": 0,
        "n_reference_visits": 0,
        "n_anchors_dip_repaired": 0,
        "n_visits_flipped": 0,
        "n_issue_rows": 0,
        "n_latest_absent_mismatches": 0,
        "n_earliest_present_mismatches": 0,
        "n_earliest_clamped": 0,
        "n_census_earliest_outside": 0,
        "n_not_chronological": 0,
        "n_duplicate_evidence_dates": 0,
        "max_evidence_visits": 0,
    }
    issue_samples: list[dict[str, str]] = []
    hard_fail: list[str] = []
    feature_batch: list[tuple] = []
    long_batch: list[tuple] = []
    seen_anchors: set[str] = set()
    cohort_counts = {WAVE_COHORT: 0, TOP52_COHORT: 0}

    with wide_path.open("w", newline="", encoding="utf-8") as wide_handle, long_path.open(
        "w", newline="", encoding="utf-8"
    ) as long_handle:
        wide_writer = csv.DictWriter(
            wide_handle, fieldnames=wide_fields, quoting=csv.QUOTE_NONNUMERIC
        )
        long_writer = csv.DictWriter(
            long_handle, fieldnames=list(LONG_FIELDS), quoting=csv.QUOTE_NONNUMERIC
        )
        wide_writer.writeheader()
        long_writer.writeheader()
        for index, row in enumerate(rows, start=1):
            anchor_id = row["source_anchor_id"].strip()
            cohort = row["cohort"].strip()
            if anchor_id in seen_anchors:
                hard_fail.append(f"duplicate anchor {anchor_id}")
                break
            seen_anchors.add(anchor_id)
            if cohort not in cohort_counts:
                hard_fail.append(f"unexpected cohort {cohort} on {anchor_id}")
                break
            cohort_counts[cohort] += 1
            if row["census_cutoff"].strip()[:10] != CENSUS_CUTOFF:
                hard_fail.append(f"{anchor_id}: census_cutoff {row['census_cutoff']}")
                break
            fid = fid_by_anchor.get(anchor_id)
            if fid is None:
                hard_fail.append(f"{anchor_id} missing from gpkg")
                break

            encoded: EncodedSequence | None = None
            coverage = "missing_scan_state"
            if cohort == WAVE_COHORT:
                state_path = args.states_dir / Path(row["scan_state_path"].strip()).name
                state = load_scan_state(state_path)
                if state is None:
                    hard_fail.append(f"missing scan state {state_path}")
                    break
                if state.anchor_id != anchor_id:
                    hard_fail.append(
                        f"{state_path.name}: json anchor {state.anchor_id} != {anchor_id}"
                    )
                    break
                try:
                    encoded = encode_scan_state(state)
                except ValueError as exc:
                    hard_fail.append(str(exc))
                    break
                coverage = "complete"
                reconcile = reconcile_sequence(
                    scan_status=row["scan_status"].strip(),
                    n_observations=int(row["n_observations"] or 0),
                    n_absent=int(row["n_absent"] or 0),
                    n_present=int(row["n_present"] or 0),
                    n_unusable=int(row["n_unusable"] or 0),
                    latest_absent_date=row["latest_absent_date"],
                    earliest_present_date=row["earliest_present_date"],
                    scan_notes=row["scan_notes"],
                    encoded=encoded,
                )
                if reconcile.issues:
                    stats["n_issue_rows"] = int(stats["n_issue_rows"]) + 1
                    if "latest_absent" in reconcile.issues:
                        stats["n_latest_absent_mismatches"] = int(stats["n_latest_absent_mismatches"]) + 1
                    if "earliest_present" in reconcile.issues:
                        stats["n_earliest_present_mismatches"] = (
                            int(stats["n_earliest_present_mismatches"]) + 1
                        )
                    _sample(
                        issue_samples,
                        {
                            "source_anchor_id": anchor_id,
                            "scan_status": row["scan_status"],
                            "issues": ",".join(reconcile.issues),
                            "n_absent": row["n_absent"],
                            "n_present": row["n_present"],
                            "n_unusable": row["n_unusable"],
                            "latest_absent_date": row["latest_absent_date"],
                            "earliest_present_date": row["earliest_present_date"],
                            "obs_dates": format_dates(encoded.obs_dates),
                            "obs_code_repaired": format_codes(encoded.obs_code_repaired),
                        },
                    )
                if reconcile.earliest_clamped:
                    stats["n_earliest_clamped"] = int(stats["n_earliest_clamped"]) + 1
                if reconcile.census_earliest_outside_sequence:
                    stats["n_census_earliest_outside"] = int(stats["n_census_earliest_outside"]) + 1
                if reconcile.repair_changed_counts:
                    stats["n_anchors_dip_repaired"] = int(stats["n_anchors_dip_repaired"]) + 1
                if not encoded.file_order_was_chronological:
                    stats["n_not_chronological"] = int(stats["n_not_chronological"]) + 1
                if encoded.duplicate_evidence_dates:
                    stats["n_duplicate_evidence_dates"] = int(stats["n_duplicate_evidence_dates"]) + 1
                stats["n_complete"] = int(stats["n_complete"]) + 1
                stats["n_evidence_visits"] = int(stats["n_evidence_visits"]) + len(encoded.evidence)
                stats["n_reference_visits"] = int(stats["n_reference_visits"]) + len(encoded.reference)
                stats["n_visits_flipped"] = int(stats["n_visits_flipped"]) + len(encoded.dip_repaired_dates)
                stats["max_evidence_visits"] = max(
                    int(stats["max_evidence_visits"]), len(encoded.evidence)
                )
                _write_long_row(
                    long_writer,
                    long_batch,
                    anchor_id=anchor_id,
                    cohort=cohort,
                    frame_role="evidence",
                    visits=encoded.evidence,
                )
                _write_long_row(
                    long_writer,
                    long_batch,
                    anchor_id=anchor_id,
                    cohort=cohort,
                    frame_role="reference",
                    visits=encoded.reference,
                )
            else:
                stats["n_missing_scan_state"] = int(stats["n_missing_scan_state"]) + 1

            columns = sequence_columns(encoded, coverage)
            wide_row = dict(row)
            wide_row.update(columns)
            wide_writer.writerow(wide_row)
            feature_batch.append(
                (
                    columns["sequence_coverage"],
                    columns["obs_dates"],
                    columns["obs_code_raw"],
                    columns["obs_code_repaired"],
                    columns["dip_repaired_dates"],
                    columns["ref_dates"],
                    columns["ref_code_raw"],
                    fid,
                )
            )
            if index % 5000 == 0:
                _flush_batches(connection, feature_batch, long_batch)
                elapsed = time.time() - started
                log(f"{index}/{EXPECTED_ROWS} rows  {elapsed:.0f}s")
        _flush_batches(connection, feature_batch, long_batch)

    if cohort_counts[WAVE_COHORT] != EXPECTED_WAVE or cohort_counts[TOP52_COHORT] != EXPECTED_TOP52:
        hard_fail.append(f"cohort counts {cohort_counts}")
    preserved = _source_columns_preserved(args.source_csv, wide_path, fieldnames)
    if preserved:
        hard_fail.append(preserved)
    if int(stats["n_issue_rows"]) or int(stats["n_latest_absent_mismatches"]) or int(
        stats["n_earliest_present_mismatches"]
    ):
        hard_fail.append(
            "reconcile failed: "
            f"issue_rows={stats['n_issue_rows']} "
            f"latest_absent={stats['n_latest_absent_mismatches']} "
            f"earliest_present={stats['n_earliest_present_mismatches']}"
        )

    geom_after = _geom_checksum(connection)
    if geom_after != geom_before:
        hard_fail.append(f"geometry checksum changed {geom_before} -> {geom_after}")
    coverage_counts = dict(
        connection.execute(
            f"SELECT sequence_coverage, COUNT(*) FROM {FEATURE_LAYER} GROUP BY 1"
        )
    )
    long_count = connection.execute(f"SELECT COUNT(*) FROM {LONG_TABLE}").fetchone()[0]
    expected_long = int(stats["n_evidence_visits"]) + int(stats["n_reference_visits"])
    if long_count != expected_long:
        hard_fail.append(f"long table rows {long_count} != {expected_long}")
    late_evidence = connection.execute(
        f"""
        SELECT COUNT(*) FROM {LONG_TABLE}
        WHERE frame_role='evidence' AND capture_date >= ?
        """,
        (CENSUS_CUTOFF,),
    ).fetchone()[0]
    if late_evidence:
        hard_fail.append(f"evidence frames on/after census: {late_evidence}")
    null_coverage = connection.execute(
        f"SELECT COUNT(*) FROM {FEATURE_LAYER} WHERE sequence_coverage IS NULL"
    ).fetchone()[0]
    if null_coverage:
        hard_fail.append(f"rows missing sequence_coverage: {null_coverage}")
    connection.execute(
        f"CREATE INDEX idx_presence_obs_anchor ON {LONG_TABLE}(source_anchor_id)"
    )
    connection.execute(
        "INSERT INTO gpkg_ogr_contents (table_name, feature_count) VALUES (?, ?)",
        (LONG_TABLE, long_count),
    )
    connection.execute(
        "UPDATE gpkg_contents SET last_change=? WHERE table_name=?",
        (FROZEN_STAMP, FEATURE_LAYER),
    )
    _restore_spatial_triggers(connection, parked_triggers)
    connection.commit()
    connection.close()

    gates = {
        "schema_version": SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_csv": str(args.source_csv),
        "source_csv_sha256": EXPECTED_CSV_SHA256,
        "source_gpkg_sha256": EXPECTED_GPKG_SHA256,
        "states_dir": str(args.states_dir),
        "geom_checksum": {"count": geom_before[0], "geom_bytes": geom_before[1]},
        "coverage_counts_gpkg": coverage_counts,
        "long_rows": long_count,
        "hard_fail": hard_fail,
        "issue_samples": issue_samples,
        **stats,
    }
    (partial / "gates.json").write_text(
        json.dumps(gates, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (partial / "README.md").write_text(_render_readme(stats), encoding="utf-8")
    if hard_fail:
        log("FAILED " + "; ".join(hard_fail[:8]))
        log_handle.close()
        raise SystemExit(f"gates failed; partial output left at {partial}")

    log(
        "OK "
        f"complete={stats['n_complete']} missing={stats['n_missing_scan_state']} "
        f"evidence={stats['n_evidence_visits']} flipped_dates={stats['n_visits_flipped']} "
        f"elapsed={time.time() - started:.0f}s"
    )
    log_handle.close()
    names = [WIDE_CSV_NAME, WIDE_GPKG_NAME, LONG_CSV_NAME, "gates.json", "README.md", "build.log"]
    sums = [f"{sha256(partial / name)}  {name}" for name in names]
    (partial / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    partial.rename(output_dir)
    return gates


def _source_columns_preserved(source: Path, wide: Path, fieldnames: list[str]) -> str | None:
    """The added sequence columns must leave every original cell unchanged."""
    with source.open(newline="", encoding="utf-8") as source_handle, wide.open(
        newline="", encoding="utf-8"
    ) as wide_handle:
        source_reader = csv.DictReader(source_handle)
        wide_reader = csv.DictReader(wide_handle)
        if list(source_reader.fieldnames or []) != fieldnames:
            return "source header drifted while reading it back"
        if list(wide_reader.fieldnames or []) != fieldnames + list(SEQUENCE_FIELDS):
            return f"wide header {wide_reader.fieldnames}"
        compared = 0
        for compared, (original, written) in enumerate(zip(source_reader, wide_reader, strict=False), start=1):
            for field in fieldnames:
                if original[field] != written[field]:
                    return f"row {compared} field {field} changed"
        source_extra = next(source_reader, None)
        written_extra = next(wide_reader, None)
    if compared != EXPECTED_ROWS or source_extra is not None or written_extra is not None:
        return f"preserved-column row count {compared}, extras={source_extra is not None}/{written_extra is not None}"
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", type=Path, default=DEFAULT_SOURCE_CSV)
    parser.add_argument("--source-gpkg", type=Path, default=DEFAULT_SOURCE_GPKG)
    parser.add_argument("--states-dir", type=Path, default=DEFAULT_STATES_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    build(parse_args(argv))


if __name__ == "__main__":
    main()
