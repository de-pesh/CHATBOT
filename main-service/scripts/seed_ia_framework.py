#!/usr/bin/env python3
"""
Seed the "IA Framework via script" framework into CCPM main-service from the
Management / Technical Criteria Excel workbook, driving the *real* change /
approval / versioning flow exactly as the application intends.

End-to-end flow
---------------
1. Authenticate (OAuth2 password form) and keep a Bearer token.
2. Create the framework            -> backend auto-creates version 1 + an open change request.
3. Upload a small placeholder file -> backend creates the required ``version_file`` mapping
                                       (the add-node API refuses to run without one).
4. Create the framework configuration:
       IA Framework
         └─ Control Family
              └─ Control Sub Family
                   └─ Control
                        └─ Sub Control
5. For each hierarchy depth ``d`` = 1..4:
       a. For every node at depth ``d``: ``POST /change`` (add) then ``PATCH /change`` (approved).
       b. ``PATCH /change-request``       -> promotes the whole layer and creates the next version.
       c. Re-read the new active version's tree to map ``label_id`` -> ``node_id`` for the next layer.

Because a node's ``node_id`` is preserved when a version is promoted, the ids captured for one
layer remain valid as the parents of the next layer.

The hierarchy is encoded entirely in the dotted labels found in the workbook:
    M1            (Control Family,     depth 1)
      M1.1        (Control Sub Family, depth 2)
        M1.1.1    (Control,            depth 3)
          M1.1.1.1(Sub Control,        depth 4, leaf)
A node's parent is its label with the final ``.N`` segment removed.

Credentials are NEVER hard-coded: pass them via ``--username`` / ``--password`` or the
environment variables ``CCPM_USERNAME`` / ``CCPM_PASSWORD``.

Examples
--------
Dry run (parse the workbook only, no API calls)::

    python seed_ia_framework.py --excel "Final Management Technical Criteria List.xlsx" --dry-run

Full seed against a local server::

    set CCPM_USERNAME=admin@example.com
    set CCPM_PASSWORD=*****
    python seed_ia_framework.py --base-url http://127.0.0.1:8000 \
        --excel "C:\\Users\\me\\Downloads\\Final Management Technical Criteria List.xlsx"

Smoke test a single family subtree end-to-end::

    python seed_ia_framework.py --only-family M1 --only-family T1
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover - dependency guard
    print("ERROR: the 'requests' package is required. Install it with: pip install requests", file=sys.stderr)
    sys.exit(1)


logger = logging.getLogger("seed_ia_framework")

DEFAULT_BASE_URL = "http://74.162.89.207/main-service"
DEFAULT_FRAMEWORK_NAME = "IA Regulation Framework"
DEFAULT_EXCEL = r"C:\Users\ZJ381WC\Downloads\Final Management Technical Criteria List.xlsx"

# The four configuration levels, top to bottom.
CONFIG_LEVELS = ["Control Family", "Control Sub Family", "Control", "Sub Control"]

# Per-row column indices (0-based) in the workbook.
COL_FAMILY_NAME = 2       # C: e.g. "M1. Strategy & Planning"
COL_SUBFAMILY_NAME = 3    # D: e.g. "M1.1 Entity Context and Leadership"
COL_CONTROL_NAME = 4      # E: e.g. "M1.1.1 Understanding the Entity ..."
COL_CONTROL_DESC = 5      # F: control description
COL_SUBCONTROL_NAME = 6   # G: e.g. "M1.1.1.1"
COL_SUBCONTROL_DESC = 7   # H: sub-control description

# Leading dotted label, e.g. "M1", "M1.1", "T9.2.3.4" (tolerates stray spaces).
_LABEL_RE = re.compile(r"^[A-Za-z]+\s*\d+(?:\s*\.\s*\d+)*")


# --------------------------------------------------------------------------- #
# Workbook parsing
# --------------------------------------------------------------------------- #
@dataclass
class Node:
    label: str
    name: str
    description: Optional[str]
    depth: int

    @property
    def parent_label(self) -> Optional[str]:
        return self.label.rsplit(".", 1)[0] if self.depth > 1 else None


def clean_text(value: object) -> str:
    """Collapse newlines / runs of whitespace and trim."""
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def extract_label(value: object) -> Optional[str]:
    """Return the leading dotted label (spaces removed) from a cell, or None."""
    if value is None:
        return None
    match = _LABEL_RE.match(str(value).strip())
    if not match:
        return None
    return match.group(0).replace(" ", "")


def strip_label_prefix(name: str, label: str) -> str:
    """Strip a leading label code (e.g. ``M1.1``) and trailing separators from a name.

    The workbook embeds the code inside the descriptive text (``"M1. Strategy & Planning"``),
    which the UI renders as ``"{label_id}: {name}"`` and would therefore show the code twice.
    Returns the descriptive remainder, or "" when the cell held only the code.
    """
    if not name:
        return ""
    stripped = _LABEL_RE.sub("", name, count=1)
    return stripped.lstrip(" .:)-\u2013\u2014\t").strip()


def natural_key(label: str):
    """Sort key so M1 < M2 < ... < M10 and M before T (e.g. ('M', [1, 1, 10]))."""
    match = re.match(r"([A-Za-z]+)(\d.*)", label)
    if not match:
        return (label, [])
    return (match.group(1), [int(part) for part in match.group(2).split(".")])


def _upsert(nodes: Dict[str, Node], cell_value: object, description: object, expected_depth: int) -> None:
    """Add or enrich a node parsed from one cell."""
    label = extract_label(cell_value)
    if not label:
        return
    depth = label.count(".") + 1
    # The workbook repeats the code inside the name; strip it so the UI (which shows
    # "{label_id}: {name}") does not display the code twice. Sub-controls hold only a
    # code in this column, so they fall back to the label as their name.
    name = strip_label_prefix(clean_text(cell_value), label) or label
    desc = clean_text(description) or None
    existing = nodes.get(label)
    if existing is None:
        nodes[label] = Node(label=label, name=name, description=desc, depth=depth)
        if depth != expected_depth:
            logger.debug("Label %r found in a depth-%d column but parsed as depth %d", label, expected_depth, depth)
        return
    # Enrich an already-seen node (cells repeat across rows / merged ranges).
    if (not existing.name or existing.name == existing.label) and name and name != label:
        existing.name = name
    if existing.description is None and desc:
        existing.description = desc


def parse_workbook(path: Path) -> Dict[str, Node]:
    """Parse the workbook into a ``label -> Node`` map across all sheets."""
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - dependency guard
        print("ERROR: the 'openpyxl' package is required. Install it with: pip install openpyxl", file=sys.stderr)
        sys.exit(1)

    if not path.exists():
        raise FileNotFoundError(f"Workbook not found: {path}")

    workbook = load_workbook(path, read_only=True, data_only=True)
    nodes: Dict[str, Node] = {}
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if not row:
                    continue

                def cell(index: int) -> object:
                    return row[index] if index < len(row) else None

                _upsert(nodes, cell(COL_FAMILY_NAME), None, 1)
                _upsert(nodes, cell(COL_SUBFAMILY_NAME), None, 2)
                _upsert(nodes, cell(COL_CONTROL_NAME), cell(COL_CONTROL_DESC), 3)
                _upsert(nodes, cell(COL_SUBCONTROL_NAME), cell(COL_SUBCONTROL_DESC), 4)
    finally:
        workbook.close()

    _synthesize_missing_ancestors(nodes)
    return nodes


def _synthesize_missing_ancestors(nodes: Dict[str, Node]) -> None:
    """Guarantee a connected tree by creating any missing intermediate ancestors."""
    for label in list(nodes):
        parts = label.split(".")
        for depth in range(1, len(parts)):
            ancestor = ".".join(parts[:depth])
            if ancestor not in nodes:
                logger.warning("Synthesising missing ancestor %r (referenced by %r)", ancestor, label)
                nodes[ancestor] = Node(label=ancestor, name=ancestor, description=None, depth=depth)


def build_layers(
    nodes: Dict[str, Node],
    only_families: Optional[List[str]] = None,
) -> Dict[int, List[Node]]:
    """Group nodes by depth (1..4), optionally restricted to certain families, naturally sorted."""
    layers: Dict[int, List[Node]] = {1: [], 2: [], 3: [], 4: []}
    for node in nodes.values():
        if node.depth < 1 or node.depth > 4:
            logger.warning("Skipping node %r with unexpected depth %d", node.label, node.depth)
            continue
        if only_families and not _in_families(node.label, only_families):
            continue
        layers[node.depth].append(node)
    for depth in layers:
        layers[depth].sort(key=lambda n: natural_key(n.label))
    return layers


def _in_families(label: str, families: List[str]) -> bool:
    for family in families:
        if label == family or label.startswith(family + "."):
            return True
    return False


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
class ApiError(RuntimeError):
    pass


class ApiClient:
    def __init__(self, base_url: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def authenticate(self, username: str, password: str) -> None:
        url = f"{self.base_url}/auth/authenticate"
        resp = self.session.post(url, data={"username": username, "password": password}, timeout=self.timeout)
        data = self._handle(resp)
        token = data.get("access_token")
        if not token:
            raise ApiError("Authentication response did not contain an access_token.")
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        logger.info("Authenticated as %s", username)

    def get(self, path: str, params: Optional[dict] = None):
        return self._handle(self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout))

    def post(self, path: str, payload: dict):
        return self._handle(self.session.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout))

    def patch(self, path: str, payload: Optional[dict] = None):
        return self._handle(self.session.patch(f"{self.base_url}{path}", json=payload, timeout=self.timeout))

    def post_files(self, path: str, files: dict, params: Optional[dict] = None):
        return self._handle(
            self.session.post(f"{self.base_url}{path}", files=files, params=params, timeout=self.timeout)
        )

    @staticmethod
    def _handle(resp: "requests.Response"):
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise ApiError(f"HTTP {resp.status_code} for {resp.request.method} {resp.url}: {detail}")
        if not resp.text.strip():
            return {}
        return resp.json()


# --------------------------------------------------------------------------- #
# Seeder
# --------------------------------------------------------------------------- #
@dataclass
class SeedStats:
    added: Dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0})
    failed: Dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0})


class FrameworkSeeder:
    def __init__(self, client: ApiClient, framework_name: str, remarks: str) -> None:
        self.client = client
        self.framework_name = framework_name
        self.remarks = remarks
        self.label_to_node_id: Dict[str, str] = {}
        self.stats = SeedStats()

    # ----- framework / file / config ------------------------------------- #
    def create_framework(self) -> int:
        payload = {"name": self.framework_name, "description": "Seeded by seed_ia_framework.py"}
        framework = self.client.post("/frameworks/", payload)
        framework_id = framework["id"]
        logger.info("Created framework #%s '%s' (version 1 + open change request auto-created)", framework_id, self.framework_name)
        return framework_id

    def upload_placeholder_file(self, framework_id: int) -> int:
        content = (
            f"{self.framework_name}\n"
            "Placeholder framework file uploaded by seed_ia_framework.py to create the "
            "required version_file mapping. Nodes are added via the change API.\n"
        ).encode("utf-8")
        files = {"files": ("ia-framework-via-script-seed.txt", BytesIO(content), "text/plain")}
        params = {"file_type": "framework", "framework_id": framework_id}
        response = self.client.post_files("/files/", files=files, params=params)
        if not isinstance(response, list) or not response:
            raise ApiError(f"Unexpected file-upload response: {response!r}")
        file_id = response[0]["id"]
        logger.info("Uploaded placeholder file #%s -> version_file mapping created for framework #%s", file_id, framework_id)
        return file_id

    def create_configuration(self, framework_id: int) -> Dict[int, int]:
        hierarchy = self._build_config_hierarchy()
        self.client.post("/framework-configuration/", {"framework_id": framework_id, "hierarchy": hierarchy})
        config = self.client.get(f"/framework-configuration/framework/{framework_id}")
        label_types = self._extract_label_types(config["hierarchy"])
        logger.info("Created framework configuration. Label types by depth: %s", label_types)
        return label_types

    def _build_config_hierarchy(self) -> dict:
        node: dict = {"name": CONFIG_LEVELS[-1], "description": f"{CONFIG_LEVELS[-1]} level", "children": []}
        for level_name in reversed(CONFIG_LEVELS[:-1]):
            node = {"name": level_name, "description": f"{level_name} level", "children": [node]}
        return {"name": self.framework_name, "description": "Root configuration", "children": [node]}

    @staticmethod
    def _extract_label_types(hierarchy: dict) -> Dict[int, int]:
        label_types: Dict[int, int] = {}
        current = hierarchy
        for depth, expected_name in enumerate(CONFIG_LEVELS, start=1):
            children = current.get("children") or []
            if not children:
                raise ApiError(f"Configuration hierarchy is missing a child at depth {depth} ({expected_name}).")
            current = children[0]
            if clean_text(current.get("name")) != expected_name:
                logger.warning("Config depth %d name is %r, expected %r", depth, current.get("name"), expected_name)
            label_types[depth] = int(current["id"])
        return label_types

    # ----- version / change-request helpers ------------------------------ #
    def get_active_version(self, framework_id: int) -> dict:
        data = self.client.get(f"/frameworks/{framework_id}/versions")
        results = data.get("results", []) if isinstance(data, dict) else []
        candidates = [v for v in results if v.get("is_active") and v.get("in_use")]
        if not candidates:
            candidates = [v for v in results if v.get("is_active")]
        if not candidates:
            raise ApiError(f"No active version found for framework {framework_id}.")
        return max(candidates, key=lambda v: v.get("number", 0))

    def get_open_change_request(self, version_id: int) -> dict:
        change_requests = self.client.get(f"/change-request/version/{version_id}")
        open_requests = [cr for cr in change_requests if cr.get("status") == "open"]
        if not open_requests:
            raise ApiError(f"No open change request found for version {version_id}.")
        return max(open_requests, key=lambda cr: cr.get("id", 0))

    def refresh_node_id_map(self, framework_id: int) -> None:
        tree = self.client.get(f"/frameworks/{framework_id}/version/active/tree")
        mapping: Dict[str, str] = {}

        def walk(node: dict) -> None:
            label, node_id = node.get("label_id"), node.get("node_id")
            if label and node_id:
                mapping[str(label)] = str(node_id)
            for child in node.get("children") or []:
                walk(child)

        for file_tree in tree or []:
            for root in file_tree.get("tree", []) or []:
                walk(root)
        self.label_to_node_id = mapping
        logger.info("Active tree now exposes %d nodes for parent resolution.", len(mapping))

    # ----- node operations ----------------------------------------------- #
    def add_and_approve_node(self, framework_id: int, node: Node, label_type: int, parent_node_id: Optional[str]) -> None:
        node_data = {
            "label_id": node.label,
            "name": node.name,
            "description": node.description,
            "label_type": label_type,
            "parent_node_id": parent_node_id,
        }
        payload = {
            "framework_id": framework_id,
            "action_type": "add",
            "node_data": node_data,
            "remarks": self.remarks,
        }
        change = self.client.post("/change/", payload)
        change_id = change["id"]
        self.client.patch(f"/change/{change_id}", {"status": "approved"})

    # ----- orchestration -------------------------------------------------- #
    def seed(self, framework_id: int, label_types: Dict[int, int], layers: Dict[int, List[Node]]) -> None:
        version = self.get_active_version(framework_id)
        change_request = self.get_open_change_request(version["id"])
        logger.info("Starting at version %s (id=%s), change request id=%s", version["number"], version["id"], change_request["id"])

        for depth in (1, 2, 3, 4):
            nodes = layers.get(depth, [])
            level_name = CONFIG_LEVELS[depth - 1]
            logger.info("=== Depth %d (%s): %d node(s) ===", depth, level_name, len(nodes))
            if not nodes:
                logger.info("No nodes at depth %d; skipping layer.", depth)
                continue

            added_here = self._add_layer(framework_id, depth, nodes, label_types[depth])

            if added_here == 0:
                logger.warning("No nodes were added at depth %d; not advancing the version.", depth)
                continue

            # Promote the whole layer -> new version, then re-resolve version / CR / node-id map.
            self.client.patch(f"/change-request/{change_request['id']}")
            version = self.get_active_version(framework_id)
            change_request = self.get_open_change_request(version["id"])
            self.refresh_node_id_map(framework_id)
            logger.info("Approved layer %d -> framework is now at version %s (id=%s).", depth, version["number"], version["id"])

        self._log_summary(version)

    def _add_layer(self, framework_id: int, depth: int, nodes: List[Node], label_type: int) -> int:
        added_here = 0
        for index, node in enumerate(nodes, start=1):
            parent_node_id: Optional[str] = None
            if depth > 1:
                parent_node_id = self.label_to_node_id.get(node.parent_label or "")
                if not parent_node_id:
                    logger.error("No parent node id for %r (parent %r); skipping.", node.label, node.parent_label)
                    self.stats.failed[depth] += 1
                    continue
            try:
                self.add_and_approve_node(framework_id, node, label_type, parent_node_id)
                self.stats.added[depth] += 1
                added_here += 1
            except ApiError as exc:
                logger.error("Failed to add/approve %r: %s", node.label, exc)
                self.stats.failed[depth] += 1
            if index % 25 == 0 or index == len(nodes):
                logger.info("  depth %d progress: %d/%d", depth, index, len(nodes))
        return added_here

    def _log_summary(self, final_version: dict) -> None:
        logger.info("---------------- Seeding summary ----------------")
        for depth in (1, 2, 3, 4):
            logger.info(
                "Depth %d (%-18s): added=%d failed=%d",
                depth,
                CONFIG_LEVELS[depth - 1],
                self.stats.added[depth],
                self.stats.failed[depth],
            )
        total_added = sum(self.stats.added.values())
        total_failed = sum(self.stats.failed.values())
        logger.info("Total: added=%d failed=%d. Final active version: %s", total_added, total_failed, final_version.get("number"))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _dump_dry_run(layers: Dict[int, List[Node]], dump_json: Optional[Path]) -> None:
    print("Parsed framework layers:")
    for depth in (1, 2, 3, 4):
        nodes = layers.get(depth, [])
        sample = ", ".join(n.label for n in nodes[:8])
        more = " ..." if len(nodes) > 8 else ""
        print(f"  Depth {depth} ({CONFIG_LEVELS[depth - 1]}): {len(nodes)} node(s)  [{sample}{more}]")
    print(f"  Total: {sum(len(v) for v in layers.values())} node(s)")
    if dump_json:
        serialisable = {
            str(depth): [
                {"label": n.label, "name": n.name, "description": n.description, "parent": n.parent_label}
                for n in layers.get(depth, [])
            ]
            for depth in (1, 2, 3, 4)
        }
        dump_json.write_text(json.dumps(serialisable, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Wrote parsed tree to {dump_json}")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the IA Framework into CCPM main-service from Excel.")
    parser.add_argument("--base-url", default=os.environ.get("CCPM_BASE_URL", DEFAULT_BASE_URL), help="main-service base URL.")
    parser.add_argument("--excel", default=os.environ.get("CCPM_EXCEL", DEFAULT_EXCEL), help="Path to the criteria workbook.")
    parser.add_argument("--framework-name", default=DEFAULT_FRAMEWORK_NAME, help="Name of the framework to create.")
    parser.add_argument("--username", default=os.environ.get("CCPM_USERNAME"), help="Login email (or env CCPM_USERNAME).")
    parser.add_argument("--password", default=os.environ.get("CCPM_PASSWORD"), help="Login password (or env CCPM_PASSWORD). Prefer the env var or the interactive prompt.")
    parser.add_argument("--remarks", default="Seeded via seed_ia_framework.py", help="Remarks attached to every change.")
    parser.add_argument("--only-family", action="append", default=None, metavar="CODE", help="Restrict to a family subtree (e.g. M1). Repeatable.")
    parser.add_argument("--timeout", type=int, default=60, help="Per-request timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Parse the workbook and print layer counts without calling the API.")
    parser.add_argument("--dump-json", type=Path, default=None, help="With --dry-run, also write the parsed tree to this JSON file.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    nodes = parse_workbook(Path(args.excel))
    layers = build_layers(nodes, only_families=args.only_family)
    logger.info("Parsed %d unique nodes from %s", len(nodes), args.excel)

    if args.dry_run:
        _dump_dry_run(layers, args.dump_json)
        return 0

    username = args.username or input("Login email: ").strip()
    password = args.password or getpass.getpass("Password: ")
    if not username or not password:
        logger.error("Username and password are required (use --username/--password or CCPM_USERNAME/CCPM_PASSWORD).")
        return 2

    client = ApiClient(args.base_url, timeout=args.timeout)
    try:
        client.authenticate(username, password)
        seeder = FrameworkSeeder(client, args.framework_name, args.remarks)
        framework_id = seeder.create_framework()
        seeder.upload_placeholder_file(framework_id)
        label_types = seeder.create_configuration(framework_id)
        seeder.seed(framework_id, label_types, layers)
    except ApiError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
