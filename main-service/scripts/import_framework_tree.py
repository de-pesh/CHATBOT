#!/usr/bin/env python3
"""
Bulk import a hierarchical framework tree into CCPM main-service.

What this script does:
1) Creates a framework (or uses an existing one).
2) Creates framework configuration hierarchy from the provided JSON.
3) Adds control nodes one-by-one via /change and approves each change.

Notes:
- The add-change API requires a parent node for every new node.
- For top-level labels (e.g. M1, T1), provide seed parent node IDs via --root-parent-map.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests


@dataclass
class SourceNode:
    label: str
    name: str
    description: Optional[str]
    children: List["SourceNode"] = field(default_factory=list)


class ApiClient:
    def __init__(self, base_url: str, token: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        return self._handle(resp)

    def post(self, path: str, payload: dict) -> dict:
        resp = self.session.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
        return self._handle(resp)

    def patch(self, path: str, payload: dict) -> dict:
        resp = self.session.patch(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
        return self._handle(resp)

    @staticmethod
    def _handle(resp: requests.Response) -> dict:
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
        if not resp.text.strip():
            return {}
        return resp.json()


def parse_source_tree(raw: dict) -> List[SourceNode]:
    def parse_node(label: str, data: dict) -> SourceNode:
        children_raw = data.get("childs") or {}
        children = [parse_node(k, v) for k, v in children_raw.items()]
        return SourceNode(
            label=label,
            name=(data.get("name") or label).strip(),
            description=(data.get("description") or "").strip() or None,
            children=children,
        )

    roots: List[SourceNode] = []
    for label, data in raw.items():
        roots.append(parse_node(label, data))
    return roots


def to_config_payload(roots: List[SourceNode], root_name: str, root_description: str) -> dict:
    def convert(node: SourceNode) -> dict:
        return {
            "name": node.name,
            "description": node.description,
            "children": [convert(c) for c in node.children],
        }

    return {
        "name": root_name,
        "description": root_description,
        "children": [convert(r) for r in roots],
    }


def map_label_to_config_id(
    source_roots: List[SourceNode],
    config_hierarchy: dict,
) -> Dict[str, int]:
    """
    Map source label IDs (M1, M1.1, ...) to framework_configuration IDs.

    This relies on child order consistency between submitted and returned hierarchy.
    """
    mapping: Dict[str, int] = {}

    source_stack: List[Tuple[SourceNode, dict]] = list(zip(source_roots, config_hierarchy.get("children", [])))

    while source_stack:
        src, cfg = source_stack.pop(0)
        cfg_id = cfg.get("id")
        if cfg_id is None:
            raise RuntimeError(f"Could not resolve configuration id for label {src.label}")
        mapping[src.label] = int(cfg_id)

        src_children = src.children
        cfg_children = cfg.get("children", [])

        if len(src_children) != len(cfg_children):
            raise RuntimeError(
                f"Configuration child count mismatch for label {src.label}: "
                f"source={len(src_children)} response={len(cfg_children)}"
            )

        for i in range(len(src_children)):
            source_stack.append((src_children[i], cfg_children[i]))

    return mapping


def flatten_preorder(roots: List[SourceNode]) -> List[Tuple[SourceNode, Optional[str]]]:
    rows: List[Tuple[SourceNode, Optional[str]]] = []

    def walk(node: SourceNode, parent_label: Optional[str]) -> None:
        rows.append((node, parent_label))
        for child in node.children:
            walk(child, node.label)

    for root in roots:
        walk(root, None)
    return rows


def tree_to_label_node_map(tree_response: List[dict]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}

    def walk(node: dict) -> None:
        label = node.get("label_id")
        node_id = node.get("node_id")
        if label and node_id:
            mapping[str(label)] = str(node_id)
        for child in node.get("children") or []:
            walk(child)

    for file_tree in tree_response:
        for root_node in file_tree.get("tree", []) or []:
            walk(root_node)
    return mapping


def choose_version_id(versions_response: dict) -> int:
    rows = versions_response.get("results", [])
    if not rows:
        raise RuntimeError("No versions found for framework.")

    # Prefer active + in_use, otherwise highest number.
    in_use = [r for r in rows if r.get("is_active") and r.get("in_use")]
    pool = in_use if in_use else [r for r in rows if r.get("is_active")]
    if not pool:
        pool = rows
    chosen = max(pool, key=lambda r: (int(r.get("number", 0)), int(r.get("id", 0))))
    return int(chosen["id"])


def get_root_parent_seed(root_label: str, root_parent_map: Dict[str, str]) -> Optional[str]:
    # Exact label key wins.
    if root_label in root_parent_map:
        return root_parent_map[root_label]

    # Prefix key fallback, e.g. M1 -> M, T7 -> T
    prefix = ""
    for ch in root_label:
        if ch.isalpha():
            prefix += ch
        else:
            break
    if prefix and prefix in root_parent_map:
        return root_parent_map[prefix]

    return None


def import_tree(
    client: ApiClient,
    json_path: Path,
    framework_name: str,
    framework_description: str,
    config_root_name: str,
    config_root_description: str,
    framework_id: Optional[int],
    skip_config_create: bool,
    root_parent_map: Dict[str, str],
    delay_ms: int,
    max_retries: int,
) -> None:
    with json_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    source_roots = parse_source_tree(raw)

    if framework_id is None:
        print("Creating framework...")
        fw = client.post("/frameworks/", {"name": framework_name, "description": framework_description})
        framework_id = int(fw["id"])
        print(f"Created framework_id={framework_id}")
    else:
        print(f"Using existing framework_id={framework_id}")

    cfg_payload = {
        "framework_id": framework_id,
        "hierarchy": to_config_payload(source_roots, config_root_name, config_root_description),
    }
    if skip_config_create:
        print("Skipping framework configuration creation; loading existing configuration...")
        cfg_resp = client.get(f"/framework-configuration/framework/{framework_id}")
    else:
        print("Creating framework configuration hierarchy...")
        try:
            cfg_resp = client.post("/framework-configuration/", cfg_payload)
        except Exception as exc:
            print(f"Configuration create failed ({exc}); trying to load existing configuration...")
            cfg_resp = client.get(f"/framework-configuration/framework/{framework_id}")

    label_to_config = map_label_to_config_id(source_roots, cfg_resp["hierarchy"])
    print(f"Resolved {len(label_to_config)} label_type IDs from framework configuration")

    print("Loading version + existing tree...")
    versions = client.get(f"/frameworks/{framework_id}/versions", params={"page_number": 1, "page_size": 1000})
    version_id = choose_version_id(versions)
    tree_resp = client.get(f"/frameworks/{framework_id}/version/{version_id}/tree")
    label_to_node = tree_to_label_node_map(tree_resp)
    print(f"Found {len(label_to_node)} existing nodes in framework tree")

    rows = flatten_preorder(source_roots)

    created_count = 0
    skipped_count = 0
    failed: List[str] = []

    for src, parent_label in rows:
        if src.label in label_to_node:
            skipped_count += 1
            continue

        if parent_label:
            parent_node_id = label_to_node.get(parent_label)
        else:
            parent_node_id = get_root_parent_seed(src.label, root_parent_map)

        if not parent_node_id:
            failed.append(f"{src.label}: missing parent_node_id (parent_label={parent_label})")
            continue

        label_type = label_to_config.get(src.label)
        if not label_type:
            failed.append(f"{src.label}: missing label_type mapping")
            continue

        payload = {
            "framework_id": framework_id,
            "version_id": version_id,
            "action_type": "add",
            "node_data": {
                "label_id": src.label,
                "name": src.name,
                "description": src.description,
                "label_type": label_type,
                "parent_node_id": parent_node_id,
            },
            "remarks": "Bulk import",
        }

        attempt = 0
        while attempt <= max_retries:
            try:
                change = client.post("/change/", payload)
                change_id = int(change["id"])
                client.patch(f"/change/{change_id}", {"status": "approved"})
                detail = client.get(f"/change/{change_id}")
                new_node_id = detail.get("new", {}).get("node_id")
                if not new_node_id:
                    raise RuntimeError(f"Could not resolve node_id after approving change {change_id}")

                label_to_node[src.label] = str(new_node_id)
                created_count += 1
                print(f"[OK] {src.label} -> node_id={new_node_id}")
                break
            except Exception as exc:
                attempt += 1
                if attempt > max_retries:
                    failed.append(f"{src.label}: {exc}")
                    print(f"[FAIL] {src.label}: {exc}")
                else:
                    wait_s = 0.5 * attempt
                    print(f"[RETRY] {src.label}: {exc} (retry in {wait_s:.1f}s)")
                    time.sleep(wait_s)

        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

    print("\nImport complete")
    print(f"Framework ID: {framework_id}")
    print(f"Version ID: {version_id}")
    print(f"Created nodes: {created_count}")
    print(f"Skipped existing nodes: {skipped_count}")
    print(f"Failed nodes: {len(failed)}")

    if failed:
        print("\nFailed entries:")
        for item in failed:
            print(f"- {item}")
        raise SystemExit(2)


def import_by_depth(
    client: ApiClient,
    json_path: Path,
    framework_id: int,
    depth: int,
    label_type: int,
    file_id: Optional[int] = None,
    version_id: Optional[int] = None,
    remarks: Optional[str] = None,
) -> None:
    """Call POST /change/bulk-import-by-depth with the full regulation JSON at a given depth."""
    with json_path.open("r", encoding="utf-8") as f:
        nodes = json.load(f)

    payload = {
        "framework_id": framework_id,
        "depth": depth,
        "label_type": label_type,
        "nodes": nodes,
        "file_id": file_id,
        "version_id": version_id,
        "remarks": remarks or f"Bulk import depth {depth}",
    }

    print(f"Posting depth={depth}, label_type={label_type} to /change/bulk-import-by-depth ...")
    result = client.post("/change/bulk-import-by-depth", payload)

    total = result.get("total_nodes_requested", 0)
    created = result.get("nodes_created", 0)
    failed = result.get("nodes_failed", 0)
    skipped = result.get("nodes_skipped", 0)
    duration = result.get("duration_seconds", 0)

    print(f"\nDepth {depth} import complete  ({duration:.2f}s)")
    print(f"  Requested : {total}")
    print(f"  Created   : {created}")
    print(f"  Failed    : {failed}")
    print(f"  Skipped   : {skipped}")

    node_results = result.get("node_results", [])
    errors = [r for r in node_results if r.get("status") == "error"]
    if errors:
        print(f"\nFailed nodes ({len(errors)}):")
        for r in errors:
            print(f"  [{r.get('label_id')}] {r.get('message')}")

    if failed and not errors:
        print("\nWarning: failed count > 0 but no error details returned.")

    if failed:
        raise SystemExit(2)


def parse_root_parent_map(value: str) -> Dict[str, str]:
    """
    Accepts either:
    1) Inline JSON string: '{"M":"uuid1","T":"uuid2"}'
    2) Path to a JSON file with same shape.
    """
    if not value:
        return {}

    maybe_file = Path(value)
    if maybe_file.exists():
        with maybe_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.loads(value)

    if not isinstance(data, dict):
        raise ValueError("root parent map must be a JSON object")

    return {str(k): str(v) for k, v in data.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk import framework controls from hierarchical JSON")
    parser.add_argument("--base-url", required=True, help="API base URL, e.g. http://74.162.89.207/main-service")
    parser.add_argument("--token", default=os.environ.get("CCPM_TOKEN"), help="Bearer token (or set CCPM_TOKEN)")
    parser.add_argument("--json-file", required=True, help="Path to source hierarchy JSON")

    parser.add_argument("--framework-id", type=int, default=None, help="Existing framework ID (skip framework creation)")
    parser.add_argument("--framework-name", default="Imported Framework", help="Framework name when creating new")
    parser.add_argument("--framework-description", default="Imported via bulk script", help="Framework description")

    parser.add_argument("--config-root-name", default="Control Hierarchy", help="Synthetic root name for framework configuration")
    parser.add_argument("--config-root-description", default="Autogenerated configuration root", help="Synthetic root description")
    parser.add_argument(
        "--skip-config-create",
        action="store_true",
        help="Do not create framework configuration; use existing one from API.",
    )

    parser.add_argument(
        "--root-parent-map",
        default="",
        help=(
            "JSON object or JSON file mapping top-level label/prefix to parent node UUID. "
            "Example: '{\"M\":\"<uuid>\",\"T\":\"<uuid>\"}'"
        ),
    )
    parser.add_argument("--delay-ms", type=int, default=0, help="Delay between node inserts in milliseconds")
    parser.add_argument("--max-retries", type=int, default=2, help="Retries per node on transient failures")

    # --- Bulk-depth mode args ---
    parser.add_argument("--bulk-depth", type=int, default=None,
                        help="If set, use bulk-import-by-depth endpoint for this depth level (1=root, 2=children, 3=grandchildren, 4=sub-controls)")
    parser.add_argument("--bulk-label-type", type=int, default=None,
                        help="framework_configuration label_type ID for nodes at --bulk-depth (required when --bulk-depth is set)")
    parser.add_argument("--file-id", type=int, default=None,
                        help="File ID for the import (used in bulk-depth mode)")
    parser.add_argument("--version-id", type=int, default=None,
                        help="Version ID for the import (used in bulk-depth mode; omit to let backend resolve)")

    args = parser.parse_args()

    if not args.token:
        raise SystemExit("Missing token. Use --token or set CCPM_TOKEN.")

    client = ApiClient(args.base_url, args.token)

    # --- Bulk-depth mode ---
    if args.bulk_depth is not None:
        if not args.bulk_label_type:
            raise SystemExit("--bulk-label-type is required when --bulk-depth is set.\n"
                             "  depth 1 → label_type 352\n"
                             "  depth 2 → label_type 353\n"
                             "  depth 3 → label_type 354\n"
                             "  depth 4 → label_type 355")
        if not args.framework_id:
            raise SystemExit("--framework-id is required when --bulk-depth is set.")
        import_by_depth(
            client=client,
            json_path=Path(args.json_file),
            framework_id=args.framework_id,
            depth=args.bulk_depth,
            label_type=args.bulk_label_type,
            file_id=args.file_id,
            version_id=args.version_id,
            remarks=f"Bulk import depth {args.bulk_depth}",
        )
        return

    # --- Original tree-import mode ---
    root_parent_map = parse_root_parent_map(args.root_parent_map)

    import_tree(
        client=client,
        json_path=Path(args.json_file),
        framework_name=args.framework_name,
        framework_description=args.framework_description,
        config_root_name=args.config_root_name,
        config_root_description=args.config_root_description,
        framework_id=args.framework_id,
        skip_config_create=args.skip_config_create,
        root_parent_map=root_parent_map,
        delay_ms=args.delay_ms,
        max_retries=args.max_retries,
    )


if __name__ == "__main__":
    main()
