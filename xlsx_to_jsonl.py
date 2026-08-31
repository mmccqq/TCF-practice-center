#!/usr/bin/env python3
"""
Convert an Excel workbook - such as one produced by jsonl_to_xlsx.py and then
hand-edited - back into flat JSONL.

    pip install pandas openpyxl

    python3 xlsx_to_jsonl.py questions_formation/tache2.xlsx
    python3 xlsx_to_jsonl.py questions_formation.xlsx --all --out-dir questions_formation
    python3 xlsx_to_jsonl.py questions_formation/tache2.xlsx --sheet tache2 -o tache2.jsonl

Sheet selection
---------------
By default, if the workbook has a sheet whose name matches the file's own
stem (e.g. "tache2.xlsx" containing a sheet named "tache2") or only one
sheet total, that single sheet is converted. Otherwise every sheet is
converted, one per output file - this is the inverse of jsonl_to_xlsx.py's
"one sheet per input file" behaviour, for workbooks that combine several
jsonl files. Pass --sheet to pick one explicitly, or --all to force
converting every sheet regardless of the name-matching heuristic (useful to
notice stray sheets left over from manual editing, e.g. Excel/Numbers'
default blank "Sheet1").

Value handling
--------------
Excel has no integer type distinct from float, and pandas hands back numpy
scalar types that json.dumps can't serialize - both are normalised here:
numpy ints/floats become native Python values, whole-number floats (as you
get back for an "int" column after a round trip) become int, blank cells
become null, and fully-blank rows (e.g. a trailing blank row Excel likes to
leave) are dropped rather than written as an empty JSON object.

Leading zeros
-------------
`--string-cols` (default: "id") is read as text instead of letting pandas
infer a type. This matters: the scrapers' ids look numeric but carry a
significant leading zero ("0320260820401"), and pandas' dtype inference
coerces such a column to int64 on read, silently turning it into
320260820401 and destroying the 2-digit source prefix. The .xlsx itself
stores the value correctly as a string, so the damage happens only here -
which also means re-running this script recovers a file that was already
mangled, without needing to re-scrape.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd


def clean_value(v):
    if v is None:
        return None
    if hasattr(v, "item"):          # numpy scalar (int64, float64, bool_, ...)
        v = v.item()
    if isinstance(v, float):
        if math.isnan(v):
            return None
        if v.is_integer():          # Excel round-trip turns int columns into floats
            return int(v)
    return v


def sheet_to_jsonl(df: pd.DataFrame, out_path: Path) -> int:
    n = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for row in df.to_dict(orient="records"):
            record = {k: clean_value(v) for k, v in row.items()}
            if all(v is None for v in record.values()):
                continue            # stray blank row
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            n += 1
    return n


def pick_sheets(xlsx_path: Path, all_sheets: dict[str, pd.DataFrame],
                 requested: str | None, force_all: bool) -> dict[str, pd.DataFrame]:
    if requested:
        if requested not in all_sheets:
            sys.exit(f"no sheet {requested!r} in {xlsx_path} - found: {list(all_sheets)}")
        return {requested: all_sheets[requested]}
    if force_all or len(all_sheets) == 1:
        return all_sheets
    if xlsx_path.stem in all_sheets:
        return {xlsx_path.stem: all_sheets[xlsx_path.stem]}
    return all_sheets


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--sheet", help="convert only this sheet")
    ap.add_argument("--all", action="store_true",
                    help="convert every sheet, ignoring the filename-match heuristic")
    ap.add_argument("-o", "--out", type=Path,
                    help="output .jsonl path - only valid when exactly one sheet is selected")
    ap.add_argument("--out-dir", type=Path,
                    help="directory for <sheet>.jsonl files when multiple sheets are selected "
                         "(default: the workbook's own directory)")
    ap.add_argument("--string-cols", default="id",
                    help="comma-separated columns to read as text, protecting "
                         "significant leading zeros (default: id)")
    args = ap.parse_args()

    if not args.xlsx.exists():
        sys.exit(f"not found: {args.xlsx}")

    dtype = {c.strip(): str for c in args.string_cols.split(",") if c.strip()}
    all_sheets = pd.read_excel(args.xlsx, sheet_name=None, engine="openpyxl",
                               dtype=dtype)
    sheets = pick_sheets(args.xlsx, all_sheets, args.sheet, args.all)

    if args.out and len(sheets) > 1:
        sys.exit(f"-o/--out needs a single sheet, but {list(sheets)} were selected - "
                 f"use --sheet to pick one, or --out-dir for multiple")

    out_dir = args.out_dir or args.xlsx.parent
    for name, df in sheets.items():
        out_path = args.out or (out_dir / f"{name}.jsonl")
        n = sheet_to_jsonl(df, out_path)
        print(f"{args.xlsx}[{name}] -> {out_path} ({n} rows)")

    skipped = set(all_sheets) - set(sheets)
    if skipped:
        print(f"(skipped sheet(s) {sorted(skipped)} - pass --all to include them)", file=sys.stderr)


if __name__ == "__main__":
    main()
