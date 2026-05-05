from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_GEHI_EXE = Path("/home/gaosh/zasolar_data/tools/GEHistoricalImagery/GEHistoricalImagery")
DEFAULT_PROVIDER = "TM"
DEFAULT_PROBE_ZOOM = 19

INFO_LEVEL_RE = re.compile(r"Level\s*=\s*(?P<level>\d+),\s*Path\s*=\s*(?P<path>[0-3]+)")
INFO_DATE_RE = re.compile(r"date\s*=\s*(?P<date>\d{4}/\d{2}/\d{2}),\s*version\s*=\s*(?P<version>\d+)")
AVAIL_DATE_RE = re.compile(r"\[\d+\]\s*(?P<date>\d{4}/\d{2}/\d{2})")
TILE_AVAIL_DATE_RE = re.compile(r"Tile availability on\s+(?P<date>\d{4}/\d{2}/\d{2})")


@dataclass(frozen=True)
class GehiRunResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def command(self) -> str:
        return " ".join(shlex.quote(part) for part in self.args)

    @property
    def stdout_sha256(self) -> str:
        return hashlib.sha256(self.stdout.encode("utf-8")).hexdigest()

    @property
    def stderr_sha256(self) -> str:
        return hashlib.sha256(self.stderr.encode("utf-8")).hexdigest()


def decode_gehi_output(raw: bytes) -> str:
    """Decode GEHistoricalImagery output captured from subprocess pipes.

    The `availability` command switches Console.OutputEncoding to UTF-16LE and
    then enters an interactive chooser. `info` normally emits UTF-8. Decode both
    forms and normalize NUL-padded output for stable parsing.
    """
    if not raw:
        return ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-16le", errors="replace")
    if text.count("\x00") > max(1, len(text) // 20):
        text = raw.decode("utf-16le", errors="replace")
    return text.replace("\x00", "")


def run_gehi(args: Sequence[object], *, executable: Path = DEFAULT_GEHI_EXE, timeout: float = 300.0) -> GehiRunResult:
    cmd = [str(executable), *[str(arg) for arg in args]]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    return GehiRunResult(
        args=tuple(cmd),
        returncode=proc.returncode,
        stdout=decode_gehi_output(proc.stdout),
        stderr=decode_gehi_output(proc.stderr),
    )


def parse_gehi_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y/%m/%d").date()


def iso_to_gehi_date(value: object) -> str:
    text = str(value).strip()[:10]
    parsed = datetime.strptime(text, "%Y-%m-%d").date()
    return parsed.strftime("%Y/%m/%d")


def anchor_location_arg(anchor: Mapping[str, object]) -> str:
    lat = float(anchor["centroid_lat"])
    lon = float(anchor["centroid_lon"])
    return f"{lat:.10f},{lon:.10f}"


def anchor_bbox_args(anchor: Mapping[str, object]) -> tuple[str, str]:
    """Return GEHI lower-left and upper-right args in LAT,LONG order."""
    lon_min = min(float(anchor["chip_lon_min"]), float(anchor["chip_lon_max"]))
    lon_max = max(float(anchor["chip_lon_min"]), float(anchor["chip_lon_max"]))
    lat_min = min(float(anchor["chip_lat_min"]), float(anchor["chip_lat_max"]))
    lat_max = max(float(anchor["chip_lat_min"]), float(anchor["chip_lat_max"]))
    lower_left = f"{lat_min:.10f},{lon_min:.10f}"
    upper_right = f"{lat_max:.10f},{lon_max:.10f}"
    return lower_left, upper_right


def parse_info_output(text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    current_level: int | None = None
    current_path = ""
    for line in text.splitlines():
        level_match = INFO_LEVEL_RE.search(line)
        if level_match:
            current_level = int(level_match.group("level"))
            current_path = level_match.group("path")
            continue
        date_match = INFO_DATE_RE.search(line)
        if date_match and current_level is not None:
            rows.append(
                {
                    "zoom": current_level,
                    "path": current_path,
                    "capture_date": parse_gehi_date(date_match.group("date")).isoformat(),
                    "version": int(date_match.group("version")),
                }
            )
    return rows


def parse_availability_output(text: str) -> list[str]:
    dates = {parse_gehi_date(m.group("date")).isoformat() for m in AVAIL_DATE_RE.finditer(text)}
    dates.update(parse_gehi_date(m.group("date")).isoformat() for m in TILE_AVAIL_DATE_RE.finditer(text))
    return sorted(dates)


def dedupe_info_rows_by_version(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, int], list[Mapping[str, object]]] = {}
    for row in rows:
        key = (str(row.get("anchor_id", "")), int(row["version"]))
        grouped.setdefault(key, []).append(row)

    out: list[dict[str, object]] = []
    for (_anchor_id, _version), items in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        sorted_items = sorted(items, key=lambda item: str(item["capture_date"]))
        first = dict(sorted_items[0])
        dates = sorted({str(item["capture_date"]) for item in sorted_items})
        first["capture_date"] = dates[0]
        first["capture_date_min"] = dates[0]
        first["capture_date_max"] = dates[-1]
        first["all_capture_dates"] = ";".join(dates)
        first["n_date_labels"] = len(dates)
        first["version_dedupe_key"] = f"{first.get('anchor_id', '')}:{first['version']}"
        return_fields = {k: first[k] for k in first}
        out.append(return_fields)
    return out


def assert_gehi_success(result: GehiRunResult, *, allow_availability_chooser_exit: bool = False) -> None:
    if result.returncode == 0:
        return
    if allow_availability_chooser_exit and parse_availability_output(result.stdout):
        if "Cannot read keys" in result.stdout or "Cannot read keys" in result.stderr:
            return
    raise RuntimeError(
        f"GEHistoricalImagery failed with return code {result.returncode}: "
        f"{result.stderr[:1000] or result.stdout[:1000]}"
    )

