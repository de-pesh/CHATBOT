#!/usr/bin/env python3
"""
Replace the LLM-generated criteria of every Sub-Control with the authoritative
criteria from the Management / Technical Criteria workbook.

The workbook is the source of truth: for each Sub-Control found in the sheet, the
script removes the Sub-Control's current criteria and inserts the sheet's list
(the counts may differ — the sheet always wins).

Data model (verified against the running database + stored procedures)
---------------------------------------------------------------------
Criteria live in ``temp_node_details`` (NOT ``node_details``) — that is the table
the application reads/writes criteria from, via the ``filter_temp_node_details`` and
``upsert_temp_node_detail`` stored procedures. A criterion row looks like:

    label_type            IS NULL          <- this is what marks a row as a criterion
    name                  = "Criteria"
    description           = <criterion text>
    label_id              IS NULL
    parent_node_id        = <Sub-Control node_id>
    version_file_id       = <the version_file the criteria were generated against>
    is_active             = 1
    created_by            = "LLM"           (for the existing generated rows)

Important nuance: the Sub-Control *structural* node lives in the active
``version_file`` (e.g. 440 for version 328), but its generated criteria were written
against an EARLIER ``version_file`` (e.g. 439). The ``node_id`` is preserved across
versions, so criteria are linked to their Sub-Control purely by ``parent_node_id``.
This script therefore replaces criteria *in place* at whatever ``version_file_id``
the existing criteria already use, so the app keeps reading them unchanged.

The script:
1. Resolves the active ``version_files`` for the target version (default 328) and maps
   each Sub-Control ``label_id`` (e.g. ``M1.1.1.1``) -> ``node_id`` from ``node_details``.
2. Loads each Sub-Control's existing ACTIVE criteria from ``temp_node_details``
   (by ``parent_node_id``), capturing their row ids and ``version_file_id``.
3. For each Sub-Control present in the workbook:
       a. Soft-deletes its current criteria (``is_active = 0``) — mirrors the soft-delete
          branch of ``upsert_temp_node_detail`` (COALESCE keeps every other column).
       b. Inserts the workbook's criteria as new ``temp_node_details`` rows with the exact
          same shape the LLM worker / ``upsert_temp_node_detail`` produces.
   All inside a single transaction.

Safety
------
* Dry-run by default — it only prints what *would* change. Pass ``--apply`` to write.
* Every write happens in ONE transaction; any error rolls the whole thing back.
* Old rows are soft-deleted (``is_active = 0``), never hard-deleted, so history is kept.
* New rows carry ``created_by = "criteria-sheet-import"`` so they are easy to identify.

Database connection
-------------------
The criteria you see in the UI live in the database behind the deployed API
(``http://74.162.89.207/main-service``) — make sure the connection below points at
THAT database, not a local copy. Provide it via (in priority order):

  1. ``--connection-string "Server=host;UserID=user;Password=pw;Database=ccpm;"``
  2. environment variable ``DB_CONNECTION_STRING`` (same format)
  3. individual ``--db-host/--db-user/--db-password/--db-name/--db-port`` flags
  4. ``--env-file path/to/.env`` (reads ``DB_CONNECTION_STRING`` from it)

The password is never logged; the script prints only host / db / target version.

Examples
--------
Preview every change (no writes)::

    python replace_criteria_from_sheet.py --connection-string "Server=...;UserID=...;Password=...;Database=ccpm;"

Preview just one family::

    python replace_criteria_from_sheet.py --only-family M1 --env-file ../.env

Apply for real::

    python replace_criteria_from_sheet.py --env-file ../.env --apply
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:  # pragma: no cover - dependency guard
    # Defer the hard failure until a DB connection is actually needed, so the
    # workbook-parsing half can still be imported / previewed without the driver.
    pymysql = None  # type: ignore[assignment]
    DictCursor = None  # type: ignore[assignment]


def require_pymysql() -> None:
    if pymysql is None:
        print(
            "ERROR: the 'PyMySQL' package is required to connect to the database.\n"
            "Install it with:  python -m pip install PyMySQL",
            file=sys.stderr,
        )
        sys.exit(1)


logger = logging.getLogger("replace_criteria")

DEFAULT_EXCEL = r"C:\Users\ZJ381WC\Downloads\Final Management Technical Criteria List.xlsx"
DEFAULT_FRAMEWORK_ID = 224
DEFAULT_VERSION_ID = 328
CRITERIA_NODE_NAME = "Criteria"
CREATED_BY_MARKER = "criteria-sheet-import"

# Column index of the canonical "Criteria" cell (0-based) and the Sub-Control label.
COL_SUBCONTROL_LABEL = 6
COL_CRITERIA = 8

# A Sub-Control label has exactly four dotted segments, e.g. M1.1.1.1 / T9.2.3.4.
_LABEL_RE = re.compile(r"^[A-Za-z]+\s*\d+(?:\s*\.\s*\d+){3}\b")
# A leading list marker: "1. ", "2) ", "10. " ... at the start of a line.
_MARKER_SPLIT_RE = re.compile(r"(?:^|\n)\s*\d+[.)]\s+")


# --------------------------------------------------------------------------- #
# Workbook parsing
# --------------------------------------------------------------------------- #
def extract_subcontrol_label(value: object) -> Optional[str]:
    """Return a 4-segment Sub-Control label (spaces removed) from a cell, or None."""
    if value is None:
        return None
    match = _LABEL_RE.match(str(value).strip())
    if not match:
        return None
    return match.group(0).replace(" ", "")


def split_criteria(raw: object, strip_numbering: bool = True) -> List[str]:
    """Split a criteria cell (a numbered, newline-separated list) into individual items."""
    if raw is None:
        return []
    text = str(raw).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    if strip_numbering:
        parts = _MARKER_SPLIT_RE.split(text)
    else:
        parts = text.split("\n")

    items: List[str] = []
    for part in parts:
        collapsed = re.sub(r"\s+", " ", part).strip()
        if collapsed:
            items.append(collapsed)
    return items


def parse_workbook(path: Path, strip_numbering: bool) -> Dict[str, List[str]]:
    """Parse the workbook into ``sub_control_label -> [criteria, ...]``."""
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - dependency guard
        print("ERROR: the 'openpyxl' package is required. Install it with: pip install openpyxl", file=sys.stderr)
        sys.exit(1)

    if not path.exists():
        raise FileNotFoundError(f"Workbook not found: {path}")

    workbook = load_workbook(path, read_only=True, data_only=True)
    result: Dict[str, List[str]] = {}
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if not row:
                    continue
                label = extract_subcontrol_label(row[COL_SUBCONTROL_LABEL] if len(row) > COL_SUBCONTROL_LABEL else None)
                if not label:
                    continue
                criteria_cell = row[COL_CRITERIA] if len(row) > COL_CRITERIA else None
                criteria = split_criteria(criteria_cell, strip_numbering=strip_numbering)
                if not criteria:
                    continue
                # First non-empty occurrence wins; later duplicate rows are ignored.
                result.setdefault(label, criteria)
    finally:
        workbook.close()
    return result


# --------------------------------------------------------------------------- #
# Connection handling
# --------------------------------------------------------------------------- #
@dataclass
class DbConfig:
    host: str
    user: str
    password: str
    database: str
    port: int = 3306

    @property
    def safe_repr(self) -> str:
        return f"{self.user}@{self.host}:{self.port}/{self.database}"


def parse_connection_string(conn: str) -> Dict[str, str]:
    """Parse a 'Server=..;UserID=..;Password=..;Database=..;Port=..' string."""
    parts = dict(
        item.split("=", 1)
        for item in conn.split(";")
        if "=" in item and item.strip()
    )
    return {k.strip(): v.strip() for k, v in parts.items()}


def load_env_file_value(env_path: Path, key: str) -> Optional[str]:
    """Return the value for ``key`` from a simple .env file (quotes stripped)."""
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def resolve_db_config(args: argparse.Namespace) -> DbConfig:
    """Resolve DB connection from CLI flags, env var, or an .env file."""
    conn_string: Optional[str] = args.connection_string or os.environ.get("DB_CONNECTION_STRING")

    if not conn_string and args.env_file:
        conn_string = load_env_file_value(Path(args.env_file), "DB_CONNECTION_STRING")

    if conn_string:
        parts = parse_connection_string(conn_string)
        return DbConfig(
            host=parts.get("Server") or parts.get("Host") or "localhost",
            user=parts.get("UserID") or parts.get("User") or "root",
            password=parts.get("Password", ""),
            database=parts.get("Database", ""),
            port=int(parts.get("Port", "3306") or "3306"),
        )

    if args.db_host and args.db_name and args.db_user:
        return DbConfig(
            host=args.db_host,
            user=args.db_user,
            password=args.db_password or "",
            database=args.db_name,
            port=args.db_port,
        )

    raise SystemExit(
        "ERROR: no database connection provided. Use --connection-string, set "
        "DB_CONNECTION_STRING, pass --env-file, or supply --db-host/--db-user/--db-name."
    )


# --------------------------------------------------------------------------- #
# Database operations
# --------------------------------------------------------------------------- #
@dataclass
class SubControl:
    label_id: str
    node_id: str
    version_file_id: int  # structural version_file (e.g. 440), not necessarily the criteria's


@dataclass
class PlanItem:
    label_id: str
    node_id: str
    insert_version_file_id: int
    existing_ids: List[int]
    new_criteria: List[str]

    @property
    def old_count(self) -> int:
        return len(self.existing_ids)


@dataclass
class RunStats:
    matched: int = 0
    unmatched_in_sheet: List[str] = field(default_factory=list)
    db_subcontrols_not_in_sheet: List[str] = field(default_factory=list)
    rows_deactivated: int = 0
    rows_inserted: int = 0


def _chunks(seq: List, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _natural_key(label: str):
    match = re.match(r"([A-Za-z]+)(\d.*)", label)
    if not match:
        return (label, [])
    return (match.group(1), [int(part) for part in match.group(2).split(".")])


class CriteriaReplacer:
    """Replace Sub-Control criteria in ``temp_node_details`` (where the app stores them)."""

    def __init__(
        self,
        conn: "pymysql.connections.Connection",
        framework_id: int,
        version_id: int,
        criteria_vf_override: Optional[int] = None,
    ) -> None:
        self.conn = conn
        self.framework_id = framework_id
        self.version_id = version_id
        self.criteria_vf_override = criteria_vf_override
        self._dominant_vf: Optional[int] = None

    def verify_target(self) -> None:
        with self.conn.cursor(DictCursor) as cur:
            cur.execute("SELECT id, framework_id, number FROM versions WHERE id = %s", (self.version_id,))
            version = cur.fetchone()
        if not version:
            raise SystemExit(f"ERROR: version {self.version_id} does not exist.")
        if version["framework_id"] != self.framework_id:
            raise SystemExit(
                f"ERROR: version {self.version_id} belongs to framework {version['framework_id']}, "
                f"not {self.framework_id}. Aborting to avoid touching the wrong data."
            )
        logger.info("Target framework %s, version %s (number %s).", self.framework_id, self.version_id, version["number"])

    def get_active_version_file_ids(self) -> List[int]:
        with self.conn.cursor(DictCursor) as cur:
            cur.execute(
                "SELECT id FROM version_files WHERE version_id = %s AND is_active = 1 ORDER BY id",
                (self.version_id,),
            )
            ids = [row["id"] for row in cur.fetchall()]
        if not ids:
            raise SystemExit(f"ERROR: no active version_files for version {self.version_id}.")
        logger.info("Active version_file id(s) for version %s: %s", self.version_id, ids)
        return ids

    def load_subcontrols(self, version_file_ids: List[int]) -> Dict[str, SubControl]:
        """Map Sub-Control label_id -> SubControl (structural nodes have label_type NOT NULL)."""
        placeholders = ", ".join(["%s"] * len(version_file_ids))
        query = (
            "SELECT label_id, node_id, version_file_id "
            "FROM node_details "
            f"WHERE version_file_id IN ({placeholders}) "
            "AND is_active = 1 AND label_type IS NOT NULL AND label_id IS NOT NULL"
        )
        with self.conn.cursor(DictCursor) as cur:
            cur.execute(query, tuple(version_file_ids))
            rows = cur.fetchall()

        mapping: Dict[str, SubControl] = {}
        for row in rows:
            label = str(row["label_id"]).strip()
            # Only Sub-Controls (4-segment labels) are criteria parents.
            if not _LABEL_RE.match(label):
                continue
            if label in mapping:
                logger.warning("Duplicate Sub-Control label %r in DB; keeping first node_id.", label)
                continue
            mapping[label] = SubControl(label_id=label, node_id=row["node_id"], version_file_id=row["version_file_id"])
        logger.info("Loaded %d Sub-Control node(s) from node_details.", len(mapping))
        return mapping

    def load_existing_criteria(self, node_ids: List[str]) -> Dict[str, List[Tuple[int, int]]]:
        """node_id -> [(criteria_row_id, version_file_id), ...] for ACTIVE criteria in temp_node_details.

        Criteria are matched by parent_node_id alone (no version_file scoping) because the
        node_id is preserved across versions and the criteria may sit in an earlier file.
        """
        result: Dict[str, List[Tuple[int, int]]] = {}
        if not node_ids:
            return result
        vf_counter: Dict[int, int] = {}
        with self.conn.cursor(DictCursor) as cur:
            for chunk in _chunks(node_ids, 500):
                placeholders = ", ".join(["%s"] * len(chunk))
                cur.execute(
                    "SELECT id, parent_node_id, version_file_id FROM temp_node_details "
                    f"WHERE parent_node_id IN ({placeholders}) "
                    "AND label_type IS NULL AND name = %s AND is_active = 1 "
                    "ORDER BY parent_node_id, id",
                    (*chunk, CRITERIA_NODE_NAME),
                )
                for row in cur.fetchall():
                    parent = str(row["parent_node_id"])
                    vf = int(row["version_file_id"])
                    result.setdefault(parent, []).append((int(row["id"]), vf))
                    vf_counter[vf] = vf_counter.get(vf, 0) + 1
        if vf_counter:
            self._dominant_vf = max(vf_counter, key=lambda k: vf_counter[k])
        logger.info(
            "Existing active criteria: %d sub-control(s) have criteria; version_file distribution: %s",
            len(result), vf_counter or "none",
        )
        return result

    def _insert_vf_for(self, existing: List[Tuple[int, int]]) -> int:
        """Pick the version_file_id to insert new criteria under (replace in place)."""
        if self.criteria_vf_override:
            return self.criteria_vf_override
        if existing:
            counts: Dict[int, int] = {}
            for _row_id, vf in existing:
                counts[vf] = counts.get(vf, 0) + 1
            return max(counts, key=lambda k: counts[k])
        if self._dominant_vf:
            return self._dominant_vf
        raise SystemExit(
            "ERROR: cannot determine a version_file_id for inserting criteria (this "
            "sub-control has no existing criteria). Pass --criteria-version-file-id."
        )

    def build_plan(
        self,
        sheet: Dict[str, List[str]],
        subcontrols: Dict[str, SubControl],
        stats: RunStats,
    ) -> List[PlanItem]:
        existing_map = self.load_existing_criteria([s.node_id for s in subcontrols.values()])
        plan: List[PlanItem] = []
        for label in sorted(sheet, key=_natural_key):
            sub = subcontrols.get(label)
            if not sub:
                stats.unmatched_in_sheet.append(label)
                continue
            stats.matched += 1
            existing = existing_map.get(sub.node_id, [])
            plan.append(
                PlanItem(
                    label_id=label,
                    node_id=sub.node_id,
                    insert_version_file_id=self._insert_vf_for(existing),
                    existing_ids=[row_id for row_id, _vf in existing],
                    new_criteria=sheet[label],
                )
            )
        sheet_labels = set(sheet)
        stats.db_subcontrols_not_in_sheet = sorted(
            (lbl for lbl in subcontrols if lbl not in sheet_labels), key=_natural_key
        )
        return plan

    def apply_plan(self, plan: List[PlanItem], stats: RunStats) -> None:
        """Soft-delete existing criteria and insert the sheet's criteria, in one transaction.

        Both operations reproduce exactly what ``upsert_temp_node_detail`` does:
          * soft-delete  -> UPDATE is_active=0, updated_by/updated_on set, all else preserved.
          * insert       -> INSERT row with created_by/created_on set, is_active=1.
        """
        all_ids = [row_id for item in plan for row_id in item.existing_ids]
        insert_rows: List[Tuple] = []
        for item in plan:
            for text in item.new_criteria:
                insert_rows.append(
                    (item.insert_version_file_id, str(uuid.uuid4()), CRITERIA_NODE_NAME, text, item.node_id, CREATED_BY_MARKER)
                )

        deactivate_sql = (
            "UPDATE temp_node_details SET is_active = 0, updated_on = NOW(), updated_by = %s "
            "WHERE id IN ({ph}) AND is_active = 1"
        )
        insert_sql = (
            "INSERT INTO temp_node_details "
            "(version_file_id, node_id, label_id, name, description, guidelines, "
            " corresponding_question, label_type, parent_node_id, is_active, created_by, created_on) "
            "VALUES (%s, %s, NULL, %s, %s, NULL, NULL, NULL, %s, 1, %s, NOW())"
        )
        with self.conn.cursor() as cur:
            for chunk in _chunks(all_ids, 1000):
                ph = ", ".join(["%s"] * len(chunk))
                cur.execute(deactivate_sql.format(ph=ph), (CREATED_BY_MARKER, *chunk))
                stats.rows_deactivated += cur.rowcount
            for chunk in _chunks(insert_rows, 500):
                cur.executemany(insert_sql, chunk)
                stats.rows_inserted += len(chunk)

    def verify_after_apply(self, plan: List[PlanItem]) -> Tuple[int, int]:
        """Re-read active criteria to confirm the post-apply state. Returns (actual, expected)."""
        existing_map = self.load_existing_criteria([p.node_id for p in plan])
        actual = sum(len(v) for v in existing_map.values())
        expected = sum(len(p.new_criteria) for p in plan)
        return actual, expected


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report_plan(plan: List[PlanItem], stats: RunStats, apply: bool) -> None:
    changed = [p for p in plan if p.old_count != len(p.new_criteria)]
    total_new = sum(len(p.new_criteria) for p in plan)
    total_old = sum(p.old_count for p in plan)

    insert_vfs = sorted({p.insert_version_file_id for p in plan})

    logger.info("---------------- Plan summary ----------------")
    logger.info("Sub-Controls in sheet matched to DB nodes : %d", stats.matched)
    logger.info("Sub-Controls in sheet NOT found in DB     : %d", len(stats.unmatched_in_sheet))
    logger.info("DB Sub-Controls not present in sheet      : %d (left untouched)", len(stats.db_subcontrols_not_in_sheet))
    logger.info("Existing active criteria (to deactivate)  : %d", total_old)
    logger.info("New criteria (to insert)                  : %d", total_new)
    logger.info("Sub-Controls whose criteria COUNT changes : %d", len(changed))
    logger.info("Insert target version_file_id(s)          : %s", insert_vfs)

    if stats.unmatched_in_sheet:
        preview = ", ".join(stats.unmatched_in_sheet[:15])
        more = " ..." if len(stats.unmatched_in_sheet) > 15 else ""
        logger.warning("Unmatched sheet Sub-Controls: %s%s", preview, more)

    # Show a handful of concrete examples so the change is easy to eyeball.
    logger.info("Examples (label: old_count -> new_count):")
    for item in plan[:12]:
        logger.info("  %-12s %d -> %d", item.label_id, item.old_count, len(item.new_criteria))
    if len(plan) > 12:
        logger.info("  ... and %d more", len(plan) - 12)

    if not apply:
        logger.info("DRY RUN: no changes written. Re-run with --apply to perform the replacement.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replace Sub-Control criteria from the criteria workbook.")
    parser.add_argument("--excel", default=os.environ.get("CCPM_EXCEL", DEFAULT_EXCEL), help="Path to the criteria workbook.")
    parser.add_argument("--framework-id", type=int, default=DEFAULT_FRAMEWORK_ID, help="Target framework id (safety check).")
    parser.add_argument("--version-id", type=int, default=DEFAULT_VERSION_ID, help="Target version id whose criteria are replaced.")
    parser.add_argument("--only-family", action="append", default=None, metavar="CODE", help="Restrict to a family (e.g. M1). Repeatable.")
    parser.add_argument("--keep-numbering", action="store_true", help="Keep the leading 'N. ' prefix on each criterion.")
    parser.add_argument("--criteria-version-file-id", type=int, default=None, help="Force the version_file_id new criteria are inserted under (default: reuse each sub-control's existing criteria version_file).")
    parser.add_argument("--apply", action="store_true", help="Actually write changes. Without this the script only previews (dry-run).")

    parser.add_argument("--connection-string", default=None, help='"Server=..;UserID=..;Password=..;Database=ccpm;"')
    parser.add_argument("--env-file", default=None, help="Path to a .env file to read DB_CONNECTION_STRING from.")
    parser.add_argument("--db-host", default=None)
    parser.add_argument("--db-port", type=int, default=3306)
    parser.add_argument("--db-user", default=None)
    parser.add_argument("--db-password", default=None)
    parser.add_argument("--db-name", default=None)

    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args(argv)


def filter_by_family(sheet: Dict[str, List[str]], families: Optional[List[str]]) -> Dict[str, List[str]]:
    if not families:
        return sheet
    return {
        label: criteria
        for label, criteria in sheet.items()
        if any(label == fam or label.startswith(fam + ".") for fam in families)
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    sheet = parse_workbook(Path(args.excel), strip_numbering=not args.keep_numbering)
    sheet = filter_by_family(sheet, args.only_family)
    total_items = sum(len(v) for v in sheet.values())
    logger.info("Parsed %d Sub-Control(s) with %d criteria item(s) from the workbook.", len(sheet), total_items)
    if not sheet:
        logger.error("No Sub-Control criteria parsed from the workbook; nothing to do.")
        return 1

    db = resolve_db_config(args)
    require_pymysql()
    logger.info("Connecting to %s", db.safe_repr)

    conn = pymysql.connect(
        host=db.host,
        port=db.port,
        user=db.user,
        password=db.password,
        database=db.database,
        autocommit=False,
        charset="utf8mb4",
    )
    try:
        replacer = CriteriaReplacer(
            conn, args.framework_id, args.version_id, criteria_vf_override=args.criteria_version_file_id
        )
        replacer.verify_target()
        version_file_ids = replacer.get_active_version_file_ids()
        subcontrols = replacer.load_subcontrols(version_file_ids)

        stats = RunStats()
        plan = replacer.build_plan(sheet, subcontrols, stats)
        if not plan:
            logger.error("No Sub-Controls from the sheet matched DB nodes; nothing to do.")
            conn.rollback()
            return 1

        report_plan(plan, stats, apply=args.apply)

        if args.apply:
            replacer.apply_plan(plan, stats)
            conn.commit()
            logger.info("APPLIED: deactivated %d old criteria, inserted %d new criteria.", stats.rows_deactivated, stats.rows_inserted)
            actual, expected = replacer.verify_after_apply(plan)
            if actual == expected:
                logger.info("VERIFIED: %d active criteria now present for the targeted sub-controls.", actual)
            else:
                logger.warning("POST-APPLY MISMATCH: expected %d active criteria but found %d. Review before relying on this.", expected, actual)
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        logger.exception("Aborted; transaction rolled back. No changes were committed.")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
