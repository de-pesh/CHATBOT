import logging
from typing import List, Dict, Any, Optional
import asyncio
from datetime import datetime, timezone
from fastapi import HTTPException, status
import redis.exceptions as redis_exceptions
from app.config import Settings, get_settings

from app.core.stored_procedures import StoredProcedures
from app.models.assessment import (
    # Referenced by the return annotation on fetch_assessment_results, which
    # could not resolve while this was only imported inside that function.
    AssessmentResultsResponse,
    AssessmentCreate,
    AssessmentUpdate,
    AssessmentFrameworkUpdate,
    AssessmentControlUpdate,
    AssessmentInDB,
    AssessmentListResponse,
    AssessmentStatus,
    ControlWithTagsResponse,
    ControlTags,
    AssetTagInfo,
    OrgUnitTagInfo,
    PeopleTagInfo,
    ComprehensiveAssessmentResponse,
    FrameworkInAssessment,
    AssessmentEvidenceFrameworkResponse,
    AssessmentEvidenceResponse,
    EvidenceFileInfo,
    RunAssessmentResponse,
    SubmitAssessmentResponse,
    AssessmentLLMDetailsResponse,
)
from app.models.files import FileInDB, FileType
from app.services.database_service import DatabaseService
from app.services.redis_service import RedisQueueService
from app.services.workflow_service import AssessmentWorkflowService

logger = logging.getLogger(__name__)


class AssessmentController:
    """
    Controller for managing assessment-related operations.
    """
    PENDING_RESULT_STATUSES = {"initiated", "queued", "processing", "running", "in-progress", "in_progress"}
    TERMINAL_RESULT_STATUSES = {"completed", "failed", "error"}

    def __init__(
            self, 
            db_service: DatabaseService,
            redis_service: Optional[RedisQueueService] = None,
            kb_queue_service: Optional[RedisQueueService] = None
        ):
        """
        Initializes the AssessmentController.

        Args:
            db_service (DatabaseService): The database service instance.
            redis_service (RedisQueueService, optional): Queue used to enqueue
                parser/analysis pipeline tasks.
            kb_queue_service (RedisQueueService, optional): Queue used to enqueue
                assessment knowledge-base snapshot tasks for the chatbot.
        """
        self.db_service = db_service
        self.redis_service = redis_service
        self.kb_queue_service = kb_queue_service
        # Per-framework cache of the CONTROL-level framework_configuration ids
        # (the second-deepest configuration level). Populated lazily per run/read.
        self._control_types_cache: Dict[int, set] = {}

    async def _enqueue_kb_task(
        self,
        assessment_id: int,
        task_type: str,
        version_number: Optional[int] = None,
    ) -> None:
        """Best-effort enqueue of an assessment knowledge-base snapshot task.

        Failures are logged but never propagated, so chatbot indexing can never
        block or fail a core assessment operation.
        """
        if self.kb_queue_service is None:
            logger.debug("KB queue service not configured; skipping KB task '%s'.", task_type)
            return
        try:
            await self.kb_queue_service.send_message(
                {
                    "task_type": task_type,
                    "assessment_id": assessment_id,
                    "version_number": version_number,
                }
            )
            logger.info(
                "Enqueued assessment KB task '%s' for assessment %s (version=%s).",
                task_type,
                assessment_id,
                version_number,
            )
        except Exception as exc:  # noqa: BLE001 - KB indexing is best-effort
            logger.warning(
                "Failed to enqueue assessment KB task '%s' for assessment %s: %s",
                task_type,
                assessment_id,
                exc,
            )

    @staticmethod
    def _normalize_result_status(value: Any) -> str:
        """Normalize status strings across services and legacy values."""
        status_value = str(value or "").strip().lower().replace("_", "-")
        if status_value == "inprogress":
            return "in-progress"
        return status_value

    @staticmethod
    def _derive_compliance_from_adherence(adherence: Any) -> Optional[str]:
        """Deterministically derive a compliance label from an adherence score.

        Thresholds (inclusive lower bound):
            >= 85  -> Compliant
            >= 50  -> Partially Compliant
            <  50  -> Non-Compliant
            None   -> None  (score not yet available)
        """
        if adherence is None:
            return None
        try:
            score = float(adherence)
        except (TypeError, ValueError):
            return None
        if score >= 85:
            return "Compliant"
        if score >= 50:
            return "Partially Compliant"
        return "Non-Compliant"

    @staticmethod
    def _parse_dt(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value

        raw = str(value).strip()
        if not raw:
            return None

        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None

    async def _auto_resolve_stale_pending_rows(
        self,
        assessment_version_id: int,
        rows: List[Dict[str, Any]],
        user_id: str,
    ) -> None:
        """
        Auto-resolve stale pending rows so a single stuck node doesn't block submit/rerun forever.
        If all sibling rows for a label are terminal, stale parent rows are completed; otherwise marked failed.
        """
        settings: Settings = get_settings()
        timeout_minutes = max(1, int(settings.RESULT_PENDING_TIMEOUT_MINUTES))
        now = datetime.now(timezone.utc)

        rows_by_label: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            label_key = str(row.get("label") or "")
            rows_by_label.setdefault(label_key, []).append(row)

        for row in rows:
            normalized = self._normalize_result_status(row.get("status"))
            if normalized not in self.PENDING_RESULT_STATUSES:
                continue

            ts = self._parse_dt(row.get("updated_on")) or self._parse_dt(row.get("created_on"))
            if ts is None:
                continue

            age_minutes = (now - ts).total_seconds() / 60.0
            if age_minutes < timeout_minutes:
                continue

            label_key = str(row.get("label") or "")
            same_label = rows_by_label.get(label_key, [])
            sibling_terminal = True
            for sibling in same_label:
                if sibling.get("id") == row.get("id"):
                    continue
                sibling_status = self._normalize_result_status(sibling.get("status"))
                if sibling_status in self.PENDING_RESULT_STATUSES:
                    sibling_terminal = False
                    break

            if sibling_terminal and len(same_label) > 1:
                new_status = "completed"
                status_message = (
                    f"Auto-completed stale parent after {timeout_minutes} minutes; "
                    "children were already terminal."
                )
            else:
                new_status = "failed"
                status_message = (
                    f"Auto-failed stale pending node after timeout of {timeout_minutes} minutes."
                )

            resolved_label = row.get("label") or row.get("label_id")
            if not resolved_label and row.get("node_id"):
                node_id = str(row.get("node_id"))
                node_rows = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_NODES,
                    [None, None, node_id, None, None, None, None, True, 1, 1],
                )
                if node_rows and node_rows[0]:
                    resolved_label = node_rows[0][0].get("label_id")

            if not resolved_label and row.get("node_id"):
                node_id = str(row.get("node_id"))
                temp_node_rows = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_TEMP_NODES,
                    [None, None, node_id, None, None, None, None, True, 1, 1],
                )
                if temp_node_rows and temp_node_rows[0]:
                    resolved_label = temp_node_rows[0][0].get("label_id")

            if not resolved_label:
                logger.warning(
                    "Skipping stale status auto-resolution for row id=%s because label could not be resolved.",
                    row.get("id"),
                )
                continue

            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_RESULT_BY_LABEL,
                [
                    row.get("id"),
                    row.get("assessment_version_id", assessment_version_id),
                    resolved_label,
                    row.get("node_id"),
                    new_status,
                    status_message,
                    row.get("is_active", True),
                    user_id,
                ],
            )

    async def _get_assessment_labels(self, assessment_id: int) -> List[Dict[str, Any]]:
        results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_LABELS,
            [None, assessment_id, None, None, True, 1, 1000]
        )
        if not results or not results[0]:
            return []
        return results[0]

    async def get_assessments_by_framework(self, framework_id: int) -> List[Dict[str, Any]]:
        """Lists assessments that include the given framework.

        Used by internal services (e.g. the chatbot) to discover peer
        assessments sharing a framework so it can answer across them. Rows are
        defensively re-checked in Python so only labels whose framework_id truly
        matches are returned, regardless of how the stored procedure interprets
        the filter argument.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_LABELS,
                [None, None, framework_id, None, True, 1, 1000],
            )
        except Exception as e:
            logger.exception(f"Error listing assessments for framework {framework_id}: {e}")
            return []

        rows = results[0] if results and results[0] else []
        assessments: List[Dict[str, Any]] = []
        seen_ids: set = set()
        for row in rows:
            if row.get("framework_id") != framework_id:
                continue
            assessment_id = row.get("assessment_id")
            if assessment_id is None or assessment_id in seen_ids:
                continue
            seen_ids.add(assessment_id)

            name = None
            try:
                assessment = await self.get_assessment_by_id(assessment_id)
                name = getattr(assessment, "name", None)
            except Exception:
                name = None

            assessments.append({
                "assessment_id": assessment_id,
                "name": name,
                "framework_id": framework_id,
            })
        return assessments

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        """Best-effort convert a value (framework_configuration id / label_type) to int."""
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _get_framework_control_label_types(self, framework_id: Any) -> set:
        """Return the framework_configuration ids that represent the CONTROL level.

        The control level is defined dynamically as the SECOND-DEEPEST configuration
        level, i.e. the parent(s) of the deepest configured level (the sub-control /
        criteria-holding level). Works for any framework depth. Returns an empty set
        when it cannot be determined (callers then fall back to the selected node).
        """
        framework_key = self._coerce_int(framework_id)
        try:
            res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_FRAMEWORK_CONFIGURATIONS,
                [None, framework_id, None, None, True, 1, 100000],
            )
        except Exception as exc:  # noqa: BLE001 - config lookup is best-effort
            logger.warning("Could not load framework configuration for framework %s: %s", framework_id, exc)
            return set()

        rows = res[0] if res and res[0] else []
        parent_of: Dict[int, Optional[int]] = {}
        for row in rows:
            cid = self._coerce_int(row.get("id"))
            if cid is None:
                continue
            parent_of[cid] = self._coerce_int(row.get("parent_id"))
        if not parent_of:
            return set()

        def _depth(config_id: int) -> int:
            depth = 0
            current = config_id
            seen: set = set()
            while parent_of.get(current) is not None and current not in seen:
                seen.add(current)
                current = parent_of[current]
                depth += 1
            return depth

        depths = {cid: _depth(cid) for cid in parent_of}
        max_depth = max(depths.values())
        if max_depth == 0:
            # Only a single (root) configuration level exists; no distinct control level.
            return set()

        deepest_ids = {cid for cid, depth in depths.items() if depth == max_depth}
        control_types = {parent_of[cid] for cid in deepest_ids if parent_of.get(cid) is not None}
        control_types.discard(None)
        logger.debug(
            "Framework %s control-level label_types resolved to %s (max_config_depth=%s).",
            framework_key, control_types, max_depth,
        )
        return control_types

    async def _get_control_types_cached(self, framework_id: Any) -> set:
        """Lazily cache the control-level label_types per framework within this controller."""
        framework_key = self._coerce_int(framework_id)
        if framework_key not in self._control_types_cache:
            self._control_types_cache[framework_key] = await self._get_framework_control_label_types(framework_id)
        return self._control_types_cache[framework_key]

    async def _fetch_node_row(self, node_id: Any) -> Optional[Dict[str, Any]]:
        """Fetch a single structural node row by node_id (permanent first, then temp)."""
        node_identifier = str(node_id)
        is_digit = node_identifier.isdigit()
        for sp in (StoredProcedures.FILTER_NODES, StoredProcedures.FILTER_TEMP_NODES):
            try:
                if is_digit:
                    res = await self.db_service.call_procedure(
                        sp, [int(node_identifier), None, None, None, None, None, None, True, 1, 1]
                    )
                else:
                    res = await self.db_service.call_procedure(
                        sp, [None, None, node_identifier, None, None, None, None, True, 1, 1]
                    )
            except Exception as exc:  # noqa: BLE001 - resilient node lookup
                logger.debug("Node lookup via %s failed for %s: %s", sp, node_identifier, exc)
                continue
            if res and res[0]:
                return res[0][0]
        return None

    async def _fetch_child_nodes(self, parent_node_id: Any) -> List[Dict[str, Any]]:
        """Return active child structural nodes of a parent (permanent + temp, deduped)."""
        children_by_id: Dict[str, Dict[str, Any]] = {}
        for sp in (StoredProcedures.FILTER_NODES, StoredProcedures.FILTER_TEMP_NODES):
            try:
                res = await self.db_service.call_procedure(
                    sp, [None, None, None, None, None, None, str(parent_node_id), True, 1, 10000]
                )
            except Exception as exc:  # noqa: BLE001 - resilient child lookup
                logger.debug("Child lookup via %s failed for parent %s: %s", sp, parent_node_id, exc)
                continue
            for row in (res[0] if res and res[0] else []):
                child_id = row.get("node_id")
                if child_id and str(child_id) not in children_by_id:
                    children_by_id[str(child_id)] = row
        return list(children_by_id.values())

    async def _collect_descendant_controls(self, node_id: Any, control_types: set) -> List[tuple]:
        """BFS downward from a node above control level, collecting control-level anchors.

        Returns a list of (control_node_id, control_label_id). Descent stops at each
        control node (we never descend below the control level).
        """
        anchors: List[tuple] = []
        seen_anchor_ids: set = set()
        visited: set = set()
        queue: List[str] = [str(node_id)]
        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)
            for child in await self._fetch_child_nodes(current):
                child_id = child.get("node_id")
                if not child_id:
                    continue
                child_id = str(child_id)
                label_type = self._coerce_int(child.get("label_type"))
                if label_type in control_types:
                    if child_id not in seen_anchor_ids:
                        seen_anchor_ids.add(child_id)
                        anchors.append((child_id, child.get("label_id")))
                    continue  # do not descend below the control level
                if label_type is None:
                    continue  # criteria/leaf node; never an ancestor of a control
                if child_id not in visited:
                    queue.append(child_id)
        return anchors

    async def _walk_up_to_control(self, node_row: Dict[str, Any], control_types: set) -> Optional[tuple]:
        """Walk up the parent chain from a node below control level to its control ancestor."""
        current = node_row
        visited: set = set()
        guard = 0
        while current is not None and guard < 50:
            guard += 1
            if self._coerce_int(current.get("label_type")) in control_types:
                return (str(current.get("node_id")), current.get("label_id"))
            parent_id = current.get("parent_node_id")
            if not parent_id or str(parent_id) in visited:
                return None
            visited.add(str(parent_id))
            current = await self._fetch_node_row(parent_id)
        return None

    async def _resolve_control_anchors_for_node(self, node_id: Any, control_types: set) -> List[tuple]:
        """Resolve a selected node to its CONTROL-level anchor node(s).

        - node at control level      -> [itself]
        - node above the control level -> all control-level descendants
        - node below the control level -> nearest control-level ancestor
        Falls back to the node itself when the control level cannot be determined.
        Returns a list of (control_node_id, control_label_id).
        """
        node_identifier = str(node_id)
        node_row = await self._fetch_node_row(node_identifier)
        if not control_types:
            label_id = node_row.get("label_id") if node_row else None
            return [(node_identifier, label_id)]
        if node_row is None:
            return [(node_identifier, None)]

        if self._coerce_int(node_row.get("label_type")) in control_types:
            return [(node_identifier, node_row.get("label_id"))]

        # Above the control level: fan out to every control descendant.
        descendants = await self._collect_descendant_controls(node_identifier, control_types)
        if descendants:
            return descendants

        # Below the control level: climb to the nearest control ancestor.
        ancestor = await self._walk_up_to_control(node_row, control_types)
        if ancestor:
            return [ancestor]

        # Fallback: keep the node as its own anchor so the run never silently drops it.
        return [(node_identifier, node_row.get("label_id"))]

    async def _get_control_anchor_node_ids(self, assessment_id: int) -> set:
        """Return the CONTROL-level node_ids for all selected nodes of an assessment.

        Selected nodes are resolved to their control-level anchors (config-driven),
        so gating and result aggregation always operate at the control level even if
        a node above the control level was selected.
        """
        anchor_ids: set = set()
        assessment_labels = await self._get_assessment_labels(assessment_id)
        for al in assessment_labels:
            control_types = await self._get_control_types_cached(al.get("framework_id"))
            al_m_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_LABEL_MAPPINGS,
                [None, al["id"], None, True, 1, 5000],
            )
            if al_m_res and al_m_res[0]:
                for mapping in al_m_res[0]:
                    node_id = mapping.get("node_id")
                    if not node_id:
                        continue
                    anchors = await self._resolve_control_anchors_for_node(node_id, control_types)
                    for anchor_node_id, _label in anchors:
                        anchor_ids.add(str(anchor_node_id))
        return anchor_ids

    async def _get_selected_control_node_ids(self, assessment_id: int) -> set:
        """Return CONTROL-level node_ids for the assessment.

        Selected nodes are resolved to their control-level anchors (the second-deepest
        framework_configuration level), so run gating and result aggregation always
        operate at the control level regardless of which node the user selected.
        """
        return await self._get_control_anchor_node_ids(assessment_id)

    def _get_control_level_pending_rows(
        self,
        rows: List[Dict[str, Any]],
        selected_control_node_ids: set,
    ) -> List[Dict[str, Any]]:
        """Return only control-level pending rows that do not already have a terminal sibling row.

        Some versions can contain duplicate rows for the same selected control node. If any row
        for that control is already terminal, that control should not block rerun/submit.
        """
        rows_by_node_id: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            node_id = str(row.get("node_id") or "")
            if selected_control_node_ids and node_id not in selected_control_node_ids:
                continue
            rows_by_node_id.setdefault(node_id, []).append(row)

        blocking_rows: List[Dict[str, Any]] = []
        for node_id, node_rows in rows_by_node_id.items():
            has_terminal_row = any(
                self._normalize_result_status(row.get("status")) in self.TERMINAL_RESULT_STATUSES
                for row in node_rows
            )
            if has_terminal_row:
                continue

            blocking_rows.extend(
                row
                for row in node_rows
                if self._normalize_result_status(row.get("status")) in self.PENDING_RESULT_STATUSES
            )

        return blocking_rows

    async def _get_active_framework_version_id(self, framework_id: int) -> int:
        version_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_VERSIONS,
            [None, framework_id, None, True, True, 1, 1]
        )
        if not version_result or not version_result[0]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Framework {framework_id} does not have an active version."
            )
        return version_result[0][0]['id']

    async def _get_assessment_control_owner_email(self, assessment_id: int) -> Optional[str]:
        control_owner_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_CONTROL_OWNERS,
            [None, assessment_id, None, True, 1, 1],
        )
        if not control_owner_result or not control_owner_result[0]:
            return None
        return str(control_owner_result[0][0].get("email") or "").strip().lower() or None

    async def _get_evidence_map_by_control(
        self,
        assessment_id: int,
        assessment_label_mapping_ids: List[int],
    ) -> Dict[int, List[EvidenceFileInfo]]:
        if not assessment_label_mapping_ids:
            return {}

        evidence_map: Dict[int, List[EvidenceFileInfo]] = {mapping_id: [] for mapping_id in assessment_label_mapping_ids}

        sp_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_EVIDENCE_FILES,
            [assessment_id, None, True, 1, 10000],
        )

        rows = sp_result[0] if sp_result and sp_result[0] else []
        for row in rows:
            mapping_id = row.get("assessment_label_mapping_id")
            if mapping_id not in evidence_map:
                continue
            evidence_map[mapping_id].append(
                EvidenceFileInfo(
                    id=row.get("id"),
                    file_id=row.get("file_id"),
                    name=row.get("name"),
                    mimetype=row.get("mimetype"),
                    file_type=row.get("file_type"),
                    file_path=row.get("file_path"),
                    created_on=row.get("created_on"),
                    created_by=row.get("created_by"),
                )
            )

        return evidence_map

    async def _get_uploaded_evidence_files(self, assessment_id: int) -> List[FileInDB]:
        mapping_results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_FILES,
            [None, assessment_id, None, True, 1, 10000],
        )
        mapping_rows = mapping_results[0] if mapping_results and mapping_results[0] else []
        if not mapping_rows:
            return []

        seen_file_ids: set[int] = set()
        ordered_file_ids: List[int] = []
        for row in mapping_rows:
            file_id = row.get("file_id")
            if file_id is None or file_id in seen_file_ids:
                continue
            seen_file_ids.add(file_id)
            ordered_file_ids.append(int(file_id))

        uploaded_files: List[FileInDB] = []
        for file_id in ordered_file_ids:
            file_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_FILES,
                [file_id, None, None, None, True, 1, 1],
            )
            if not file_results or not file_results[0]:
                continue

            file_record = FileInDB(**file_results[0][0])
            if file_record.file_type == FileType.EVIDENCE:
                uploaded_files.append(file_record)

        return uploaded_files

    async def _get_version_result_bundle(
        self,
        assessment_version_id: int,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        bundle_res = await self.db_service.call_procedure(
            StoredProcedures.FILTER_VERSION_RESULT_BUNDLE,
            [assessment_version_id],
        )

        rows = bundle_res[0] if bundle_res and bundle_res[0] else []
        by_label: Dict[int, Dict[str, Any]] = {}
        detailed_results: List[Dict[str, Any]] = []

        for row in rows:
            result_by_label_id = row.get("result_by_label_id")
            if not result_by_label_id:
                continue

            if result_by_label_id not in by_label:
                by_label[result_by_label_id] = {
                    "id": result_by_label_id,
                    "assessment_version_id": row.get("assessment_version_id"),
                    "label": row.get("label"),
                    "node_id": row.get("node_id"),
                    "status": row.get("status"),
                    "error_message": row.get("error_message"),
                    "created_on": row.get("rbl_created_on"),
                    "created_by": row.get("rbl_created_by"),
                    "updated_on": row.get("rbl_updated_on"),
                    "updated_by": row.get("rbl_updated_by"),
                }

            if row.get("result_id"):
                detailed_results.append(
                    {
                        "id": row.get("result_id"),
                        "result_by_label_id": result_by_label_id,
                        "adherence": row.get("adherence"),
                        "reason": row.get("reason"),
                        "recommendation": row.get("recommendation"),
                        "compliance": row.get("compliance"),
                        "created_on": row.get("result_created_on"),
                        "created_by": row.get("result_created_by"),
                        "updated_on": row.get("result_updated_on"),
                        "updated_by": row.get("result_updated_by"),
                    }
                )

        return list(by_label.values()), detailed_results

    async def _clone_unchanged_control_results_fallback(
        self,
        previous_assessment_version_id: int,
        new_assessment_version_id: int,
        node_ids: List[str],
        user_id: str,
    ) -> None:
        """
        Fallback clone path when CLONE_UNCHANGED_CONTROL_RESULTS SP is unavailable.
        Copies results_by_label rows and their detailed results node-wise.
        """
        for node_id in node_ids:
            prev_rbl_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_RESULTS_BY_LABEL,
                [None, previous_assessment_version_id, None, node_id, None, True, 1, 5000],
            )
            prev_rbl_rows = prev_rbl_res[0] if prev_rbl_res and prev_rbl_res[0] else []
            if not prev_rbl_rows:
                continue

            for prev_rbl in prev_rbl_rows:
                resolved_label = prev_rbl.get("label") or prev_rbl.get("label_id")
                if not resolved_label:
                    node_rows = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_NODES,
                        [None, None, node_id, None, None, None, None, True, 1, 1],
                    )
                    if node_rows and node_rows[0]:
                        resolved_label = node_rows[0][0].get("label_id")

                if not resolved_label:
                    temp_node_rows = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_TEMP_NODES,
                        [None, None, node_id, None, None, None, None, True, 1, 1],
                    )
                    if temp_node_rows and temp_node_rows[0]:
                        resolved_label = temp_node_rows[0][0].get("label_id")

                if not resolved_label:
                    logger.warning(
                        "Skipping fallback clone for node '%s' due to missing label.",
                        node_id,
                    )
                    continue

                await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_RESULT_BY_LABEL,
                    [
                        None,
                        new_assessment_version_id,
                        resolved_label,
                        node_id,
                        prev_rbl.get("status") or "completed",
                        prev_rbl.get("error_message"),
                        prev_rbl.get("is_active", True),
                        user_id,
                    ],
                )

                new_rbl_res = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_RESULTS_BY_LABEL,
                    [None, new_assessment_version_id, None, node_id, None, True, 1, 5000],
                )
                new_rbl_rows = new_rbl_res[0] if new_rbl_res and new_rbl_res[0] else []
                new_rbl = next(
                    (row for row in new_rbl_rows if str(row.get("label") or "") == str(resolved_label)),
                    new_rbl_rows[0] if new_rbl_rows else None,
                )
                if not new_rbl:
                    continue

                prev_detail_res = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_RESULTS,
                    [None, prev_rbl.get("id"), node_id, None, True, 1, 5000],
                )
                prev_detail_rows = prev_detail_res[0] if prev_detail_res and prev_detail_res[0] else []
                for detail_row in prev_detail_rows:
                    await self.db_service.call_procedure(
                        StoredProcedures.UPSERT_RESULT,
                        [
                            None,
                            new_rbl.get("id"),
                            node_id,
                            detail_row.get("adherence"),
                            detail_row.get("reason"),
                            detail_row.get("recommendation"),
                            detail_row.get("compliance"),
                            detail_row.get("is_active", True),
                            user_id,
                        ],
                    )

    async def create_assessment(self, assessment_data: AssessmentCreate, user_id: str) -> AssessmentInDB:
        """
        Creates a new assessment.

        Args:
            assessment_data (AssessmentCreate): The data for the new assessment.
            user_id (str): The ID of the user creating the assessment.

        Returns:
            AssessmentInDB: The created assessment's data.

        Raises:
            HTTPException: If the assessment cannot be created.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT,
                [
                    None,  # ID is auto-generated
                    assessment_data.name,
                    assessment_data.description,
                    "pending_creation_review",
                    assessment_data.assigned_to,
                    True,  # is_active
                    user_id
                ]
            )
            # The UPSERT_ASSESSMENT procedure returns a single result set with the ID.
            if not results or not results[0] or 'id' not in results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create assessment.")
            
            assessment_id = results[0][0]['id']
            created = await self.get_assessment_by_id(assessment_id)
            await self._enqueue_kb_task(assessment_id, "assessment_created")
            return created
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating assessment: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while creating the assessment: {e}")

    async def get_assessment_by_id(self, assessment_id: int) -> AssessmentInDB:
        """
        Retrieves an assessment by its ID.

        Args:
            assessment_id (int): The unique identifier of the assessment.

        Returns:
            AssessmentInDB: The assessment's data, including framework_ids mapped to it.

        Raises:
            HTTPException: If the assessment is not found.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENTS,
                [assessment_id, None, None, None, True, 1, 1]  # id, name, status, user, is_active, page_number, page_size
            )
            if not results or not results[0]:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Assessment with ID {assessment_id} not found.")

            assessment_data = dict(results[0][0])

            # Fetch framework IDs mapped to this assessment via assessment_labels
            label_results = await self._get_assessment_labels(assessment_id)
            framework_ids = []
            if label_results:
                framework_ids = list({row['framework_id'] for row in label_results if row.get('framework_id') is not None})

            assessment_data['framework_ids'] = framework_ids
            return AssessmentInDB(**assessment_data)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error retrieving assessment {assessment_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while retrieving the assessment: {e}")

    async def get_all_assessments(self, user: str, page_number: int = 1, page_size: int = 10) -> AssessmentListResponse:
        """
        Retrieves all assessments visible to the given user with pagination.

        A user can see an assessment if they are any of:
          - the assessment's creator / assigned_to (handled by FILTER_ASSESSMENTS user filter)
          - an assigned reviewer
          - an assigned approver
          - an assigned control owner

        Args:
            user (str): Email of the requesting user.
            page_number (int): The page number to retrieve.
            page_size (int): The number of assessments per page.

        Returns:
            AssessmentListResponse: Paginated assessments and pagination metadata.
        """
        try:
            normalized_user = (user or "").strip().lower() or None

            # 1) Assessments where user is creator/assigned_to
            primary_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENTS,
                [None, None, None, normalized_user, True, 1, 10000],
            )
            primary_rows = primary_results[0] if primary_results and primary_results[0] else []

            # 2) Assessments where user is a reviewer
            reviewer_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_REVIEWERS,
                [None, None, normalized_user, None, None, True, 1, 10000],
            )
            reviewer_rows = reviewer_results[0] if reviewer_results and reviewer_results[0] else []

            # 3) Assessments where user is an approver
            approver_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_APPROVERS,
                [None, None, normalized_user, None, None, True, 1, 10000],
            )
            approver_rows = approver_results[0] if approver_results and approver_results[0] else []

            # 4) Assessments where user is a control owner
            control_owner_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_CONTROL_OWNERS,
                [None, None, normalized_user, True, 1, 10000],
            )
            control_owner_rows = control_owner_results[0] if control_owner_results and control_owner_results[0] else []

            primary_ids = {row.get("id") for row in primary_rows if row.get("id") is not None}
            extra_ids: set = set()
            for row in reviewer_rows + approver_rows + control_owner_rows:
                aid = row.get("assessment_id")
                if aid is not None and aid not in primary_ids:
                    extra_ids.add(aid)

            # Hydrate assessments where the user is only an actioner (not creator/assignee)
            extra_rows: List[dict] = []
            for aid in extra_ids:
                hydrated = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_ASSESSMENTS,
                    [aid, None, None, None, True, 1, 1],
                )
                if hydrated and hydrated[0]:
                    extra_rows.append(dict(hydrated[0][0]))

            # Merge + dedupe (primary takes precedence on key collisions)
            merged: dict = {}
            for row in extra_rows + primary_rows:
                rid = row.get("id")
                if rid is not None:
                    merged[rid] = row

            # Sort newest first by id desc, then paginate
            sorted_rows = sorted(merged.values(), key=lambda r: r.get("id") or 0, reverse=True)
            total_records = len(sorted_rows)
            total_pages = (total_records + page_size - 1) // page_size if page_size > 0 else 0
            start = max(0, (page_number - 1) * page_size)
            paged = sorted_rows[start : start + page_size]
            items = [AssessmentInDB(**row) for row in paged]

            return AssessmentListResponse(
                items=items,
                page_number=page_number,
                page_size=page_size,
                total_records=total_records,
                total_pages=total_pages,
            )
        except Exception as e:
            logger.exception(f"Error retrieving all assessments: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while retrieving assessments: {e}")

    async def update_assessment(self, assessment_id: int, assessment_data: AssessmentUpdate, user_id: str) -> AssessmentInDB:
        """
        Updates an existing assessment.

        Args:
            assessment_id (int): The ID of the assessment to update.
            assessment_data (AssessmentUpdate): The update data for the assessment.
            user_id (str): The ID of the user performing the update.

        Returns:
            AssessmentInDB: The updated assessment's data.

        Raises:
            HTTPException: If the assessment is not found or cannot be updated.
        """
        try:
            existing_assessment = await self.get_assessment_by_id(assessment_id)
            update_data = assessment_data.model_dump(exclude_unset=True)

            # Once creation-phase is rejected at version 3, no further edits are allowed.
            if existing_assessment.status in {
                AssessmentStatus.CREATION_REVIEW_REJECTED,
                AssessmentStatus.ASSESSMENT_CREATION_REJECTED,
            }:
                versions_res = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_ASSESSMENT_VERSIONS,
                    [None, assessment_id, None, None, 1, 1000],
                )
                version_rows = versions_res[0] if versions_res and versions_res[0] else []
                max_version_number = max((row.get("version_number") or 0) for row in version_rows) if version_rows else 0
                if max_version_number >= 3:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Assessment creation phase was rejected at version 3. Further updates are not allowed.",
                    )

            target_status = update_data.get('status', existing_assessment.status)
            if existing_assessment.status in {
                AssessmentStatus.CREATION_REVIEW_REJECTED,
                AssessmentStatus.ASSESSMENT_CREATION_REJECTED,
            } and update_data:
                # Reopen rejected creation-phase assessments after user edits.
                target_status = AssessmentStatus.PENDING_CREATION_REVIEW

            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT,
                [
                    assessment_id,
                    update_data.get('name', existing_assessment.name),
                    update_data.get('description', existing_assessment.description),
                    target_status.value,
                    update_data.get('assigned_to', existing_assessment.assigned_to),
                    update_data.get('is_active', existing_assessment.is_active),
                    user_id
                ]
            )
            if not results or not results[0] or 'id' not in results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update assessment.")
            
            return await self.get_assessment_by_id(assessment_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating assessment {assessment_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while updating the assessment: {e}")

    async def add_assessment_framework(
        self,
        assessment_id: int,
        framework_data,
        user_id: str,
    ) -> AssessmentInDB:
        """Add a new framework to an assessment."""
        try:
            await self.get_assessment_by_id(assessment_id)
            existing_labels = await self._get_assessment_labels(assessment_id)

            # Check if framework already mapped
            for label in existing_labels:
                if label['framework_id'] == framework_data.framework_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Framework {framework_data.framework_id} is already mapped to assessment {assessment_id}.",
                    )

            # Get active version for the framework
            new_version_id = await self._get_active_framework_version_id(framework_data.framework_id)

            # Add the framework
            result = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT_LABEL,
                [
                    None,
                    assessment_id,
                    framework_data.framework_id,
                    new_version_id,
                    True,
                    user_id,
                ]
            )
            if not result or not result[0] or 'id' not in result[0][0]:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to add framework to assessment.",
                )

            return await self.get_assessment_by_id(assessment_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error adding framework to assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An error occurred while adding the framework: {e}",
            )

    async def bulk_add_assessment_frameworks(
        self,
        assessment_id: int,
        framework_ids: List[int],
        user_id: str,
    ) -> AssessmentInDB:
        """Add multiple frameworks to an assessment in one call.

        Skips frameworks already mapped. Fails fast on invalid framework_id
        (no active version) or DB errors per framework, but continues with
        the remaining ones, collecting per-framework failures in the log.
        """
        try:
            await self.get_assessment_by_id(assessment_id)
            existing_labels = await self._get_assessment_labels(assessment_id)
            already_mapped = {
                label['framework_id']
                for label in existing_labels
                if label.get('framework_id') is not None
            }

            unique_framework_ids = []
            seen = set()
            for fid in framework_ids:
                if fid in seen:
                    continue
                seen.add(fid)
                unique_framework_ids.append(fid)

            for framework_id in unique_framework_ids:
                if framework_id in already_mapped:
                    logger.info(
                        f"Framework {framework_id} already mapped to assessment {assessment_id}; skipping."
                    )
                    continue

                try:
                    new_version_id = await self._get_active_framework_version_id(framework_id)
                except HTTPException as e:
                    logger.warning(
                        f"Skipping framework {framework_id} for assessment {assessment_id}: {e.detail}"
                    )
                    continue

                result = await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_ASSESSMENT_LABEL,
                    [
                        None,
                        assessment_id,
                        framework_id,
                        new_version_id,
                        True,
                        user_id,
                    ]
                )
                if not result or not result[0] or 'id' not in result[0][0]:
                    logger.warning(
                        f"Failed to add framework {framework_id} to assessment {assessment_id}."
                    )
                    continue

            return await self.get_assessment_by_id(assessment_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error bulk adding frameworks to assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An error occurred while bulk adding frameworks: {e}",
            )

    async def delete_assessment_framework(
        self,
        assessment_id: int,
        framework_id: int,
        user_id: str,
    ) -> AssessmentInDB:
        """Delete/unmap a framework from an assessment."""
        try:
            await self.get_assessment_by_id(assessment_id)
            existing_labels = await self._get_assessment_labels(assessment_id)

            # Find the framework mapping
            target_label = None
            for label in existing_labels:
                if label['framework_id'] == framework_id:
                    target_label = label
                    break

            if target_label is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Framework {framework_id} is not mapped to assessment {assessment_id}.",
                )

            # Delete the framework mapping
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ASSESSMENT_LABEL,
                [target_label['id'], user_id]
            )

            return await self.get_assessment_by_id(assessment_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting framework from assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An error occurred while deleting the framework: {e}",
            )

    async def _replace_single_framework(
        self,
        assessment_id: int,
        current_framework_id: Optional[int],
        new_framework_id: int,
        user_id: str,
    ) -> None:
        """Helper to replace a single framework."""
        existing_labels = await self._get_assessment_labels(assessment_id)

        labels_by_framework = {}
        for label in existing_labels:
            labels_by_framework.setdefault(label['framework_id'], label)

        target_label = None
        if current_framework_id is not None:
            target_label = labels_by_framework.get(current_framework_id)
            if target_label is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        f"Framework {current_framework_id} is not mapped to assessment {assessment_id}."
                    ),
                )
        elif len(labels_by_framework) == 1:
            target_label = next(iter(labels_by_framework.values()))
        elif len(labels_by_framework) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="current_framework_id is required when the assessment has multiple mapped frameworks.",
            )

        if new_framework_id in labels_by_framework:
            if target_label and target_label['id'] != labels_by_framework[new_framework_id]['id']:
                await self.db_service.call_procedure(
                    StoredProcedures.DELETE_ASSESSMENT_LABEL,
                    [target_label['id'], user_id]
                )
            return

        new_version_id = await self._get_active_framework_version_id(new_framework_id)

        if target_label is not None:
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ASSESSMENT_LABEL,
                [target_label['id'], user_id]
            )

        result = await self.db_service.call_procedure(
            StoredProcedures.UPSERT_ASSESSMENT_LABEL,
            [
                None,
                assessment_id,
                new_framework_id,
                new_version_id,
                True,
                user_id,
            ]
        )
        if not result or not result[0] or 'id' not in result[0][0]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update assessment framework.",
            )

    async def update_assessment_framework(
        self,
        assessment_id: int,
        framework_data: AssessmentFrameworkUpdate,
        user_id: str,
    ) -> AssessmentInDB:
        try:
            await self.get_assessment_by_id(assessment_id)

            # Process all framework replacements
            for replacement in framework_data.replacements:
                try:
                    await self._replace_single_framework(
                        assessment_id,
                        replacement.current_framework_id,
                        replacement.new_framework_id,
                        user_id,
                    )
                except HTTPException as e:
                    logger.warning(
                        f"Failed to replace framework {replacement.current_framework_id} with "
                        f"{replacement.new_framework_id} for assessment {assessment_id}: {e.detail}"
                    )
                    # Continue processing remaining replacements

            return await self.get_assessment_by_id(assessment_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating framework for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An error occurred while updating the assessment framework: {e}",
            )

    async def _replace_single_control(
        self,
        assessment_id: int,
        current_control_id: int,
        new_control_node_id: int,
        user_id: str,
    ) -> None:
        """Helper to replace a single control mapping."""
        # Get all assessment labels (frameworks) for this assessment
        labels_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_LABELS,
            [None, assessment_id, None, None, None, 1, 1000]
        )
        if not labels_result or not labels_result[0]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No frameworks found for assessment {assessment_id}.",
            )

        # Search for the current control mapping across all labels
        target_mapping = None
        target_label_id = None
        
        for label in labels_result[0]:
            label_id = label['id']
            mappings_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_LABEL_MAPPINGS,
                [None, label_id, None, 1, 1, 1000]
            )
            if mappings_result and mappings_result[0]:
                for mapping in mappings_result[0]:
                    if mapping['id'] == current_control_id:
                        target_mapping = mapping
                        target_label_id = label_id
                        break
            if target_mapping:
                break

        if not target_mapping:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Control mapping {current_control_id} not found for assessment {assessment_id}.",
            )

        # Delete the old mapping
        await self.db_service.call_procedure(
            StoredProcedures.DELETE_ASSESSMENT_LABEL_MAPPING,
            [current_control_id, user_id]
        )

        # Create new mapping with the new control node_id
        result = await self.db_service.call_procedure(
            StoredProcedures.UPSERT_ASSESSMENT_LABEL_MAPPING,
            [
                None,  # id (auto-generated on insert)
                target_label_id,  # assessment_label_id (same label)
                new_control_node_id,  # node_id (new control)
                user_id,  # created_by
            ]
        )
        if not result or not result[0] or 'id' not in result[0][0]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create new control mapping.",
            )

    async def update_assessment_controls(
        self,
        assessment_id: int,
        control_data: AssessmentControlUpdate,
        user_id: str,
    ) -> AssessmentInDB:
        try:
            await self.get_assessment_by_id(assessment_id)

            # Process all control replacements
            for replacement in control_data.replacements:
                try:
                    await self._replace_single_control(
                        assessment_id,
                        replacement.current_control_id,
                        replacement.new_control_node_id,
                        user_id,
                    )
                except HTTPException as e:
                    logger.warning(
                        f"Failed to replace control {replacement.current_control_id} with "
                        f"{replacement.new_control_node_id} for assessment {assessment_id}: {e.detail}"
                    )
                    # Continue processing remaining replacements

            return await self.get_assessment_by_id(assessment_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating controls for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An error occurred while updating the assessment controls: {e}",
            )

    async def delete_assessment(self, assessment_id: int, user_id: str) -> None:
        """
        Deletes an assessment by marking it as inactive.

        Args:
            assessment_id (int): The ID of the assessment to delete.
            user_id (str): The ID of the user performing the deletion.

        Raises:
            HTTPException: If the assessment is not found or cannot be deleted.
        """
        try:
            await self.get_assessment_by_id(assessment_id)
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ASSESSMENT,
                [assessment_id, user_id]
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting assessment {assessment_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while deleting the assessment: {e}")


    async def get_controls_with_tags_for_assessment(self, assessment_id: int) -> List[ControlWithTagsResponse]:
        """
        Retrieves all controls (label mappings) for a given assessment,
        and for each control, retrieves all of its associated tags.
        """
        try:
            # 1. Get all assessment_labels for the assessment
            label_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_LABELS, [None, assessment_id, None, None, 1, 1, 100]
            )
            if not label_results or not label_results[0]:
                return []
            
            all_controls = []
            
            # Create a map of label_id to version_id for node lookup
            label_version_map = {label['id']: label['version_id'] for label in label_results[0]}
            assessment_label_ids = list(label_version_map.keys())

            for label_id in assessment_label_ids:
                version_id = label_version_map[label_id]
                # 2. For each label, get all its controls (mappings)
                mapping_results = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_ASSESSMENT_LABEL_MAPPINGS, [None, label_id, None, 1, 1, 1000]
                )
                if not mapping_results or not mapping_results[0]:
                    continue

                for mapping_data in mapping_results[0]:
                    mapping_id = mapping_data['id']
                    node_id = mapping_data['node_id']
                    # --- Fetch business label_id for this control ---
                    label_id_value = None
                    try:
                        node_results = await self.db_service.call_procedure(
                            StoredProcedures.FILTER_NODES, [None, None, node_id, None, None, None, None, 1, 1, 1]
                        )
                        if node_results and node_results[0]:
                            label_id_value = node_results[0][0].get('label_id')
                    except Exception as e:
                        logger.warning(f"Could not fetch label_id for node_id {node_id}: {e}")

                    # Fetch node details to get control_name
                    control_name = "Unknown"
                    try:
                        if str(node_id).isdigit():
                            node_results = await self.db_service.call_procedure(
                                StoredProcedures.FILTER_NODES, [int(node_id), None, None, None, None, None, None, 1, 1, 1]
                            )
                        else:
                            node_results = await self.db_service.call_procedure(
                                StoredProcedures.FILTER_NODES, [None, None, node_id, None, None, None, None, 1, 1, 1]
                            )
                        if node_results and node_results[0]:
                            control_name = node_results[0][0].get('name', 'Unknown')
                    except Exception as e:
                        logger.warning(f"Could not fetch node details for node_id {node_id}: {e}")

                    # 3. For each control, get all its tags
                    # Asset Tags
                    asset_tag_results = await self.db_service.call_procedure(StoredProcedures.FILTER_ASSET_ASSESSMENT_LABEL_MAPPINGS, [None, None, mapping_id, 1, 1, 100])
                    asset_tags = [AssetTagInfo(**tag) for tag in asset_tag_results[0]] if asset_tag_results and asset_tag_results[0] else []

                    # Org Unit Tags
                    org_unit_tag_results = await self.db_service.call_procedure(StoredProcedures.FILTER_ORG_UNIT_ASSESSMENT_LABEL_MAPPINGS, [None, None, mapping_id, 1, 1, 100])
                    org_unit_tags = [OrgUnitTagInfo(**tag) for tag in org_unit_tag_results[0]] if org_unit_tag_results and org_unit_tag_results[0] else []

                    # People Tags
                    people_tag_results = await self.db_service.call_procedure(StoredProcedures.FILTER_PEOPLE_ASSESSMENT_LABEL_MAPPINGS, [None, None, mapping_id, 1, 1, 100])
                    people_tags = [PeopleTagInfo(**tag) for tag in people_tag_results[0]] if people_tag_results and people_tag_results[0] else []

                    control_response = ControlWithTagsResponse(
                        id=mapping_id,
                        assessment_label_id=mapping_data['assessment_label_id'],
                        node_id=mapping_data['node_id'],
                        label_id=label_id_value,
                        control_name=control_name,
                        tags=ControlTags(
                            assets=asset_tags,
                            org_units=org_unit_tags,
                            people=people_tags
                        ),
                        evidence_files=[]
                    )
                    all_controls.append(control_response)

            evidence_map = await self._get_evidence_map_by_control(
                assessment_id,
                [control.id for control in all_controls],
            )
            for control in all_controls:
                control.evidence_files = evidence_map.get(control.id, [])

            return all_controls
        except Exception as e:
            logger.exception(f"Error getting controls with tags for assessment {assessment_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve controls and tags.")
        

    async def get_comprehensive_assessment_by_id(self, assessment_id: int) -> ComprehensiveAssessmentResponse:
        """
        Retrieves a complete, nested view of an assessment including its
        frameworks, controls, and all associated tags.
        """
        try:
            # 1. Get base assessment details
            base_assessment = await self.get_assessment_by_id(assessment_id)
            
            # 2. Get all linked frameworks (assessment_labels)
            label_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_LABELS, [None, assessment_id, None, None, 1, 1, 100]
            )
            
            frameworks_in_assessment = []
            if label_results and label_results[0]:
                for label_data in label_results[0]:
                    label_id = label_data['id']
                    framework_id = label_data['framework_id']
                    
                    # Fetch framework details to get framework_name
                    framework_name = "Unknown"
                    try:
                        framework_results = await self.db_service.call_procedure(
                            StoredProcedures.FILTER_FRAMEWORKS, [framework_id, None, 1, 1, 1]
                        )
                        if framework_results and framework_results[0]:
                            framework_name = framework_results[0][0].get('name', 'Unknown')
                    except Exception as e:
                        logger.warning(f"Could not fetch framework details for framework_id {framework_id}: {e}")
                    
                    # 3. For each framework, get its controls with their tags
                    controls_with_tags = await self.get_controls_with_tags_for_assessment(assessment_id) 
                    # Note: this is slightly inefficient as it re-gets labels. A more optimized version would pass the label_id.
                    
                    # Filter controls for the current label_id
                    relevant_controls = [c for c in controls_with_tags if c.assessment_label_id == label_id]

                    framework_files_map: Dict[int, EvidenceFileInfo] = {}
                    for control in relevant_controls:
                        for ev in (control.evidence_files or []):
                            framework_files_map[ev.id] = ev

                    framework_in_assessment = FrameworkInAssessment(
                        id=label_id,
                        framework_id=framework_id,
                        framework_name=framework_name,
                        version_id=label_data['version_id'],
                        controls=relevant_controls,
                        files=list(framework_files_map.values())
                    )
                    frameworks_in_assessment.append(framework_in_assessment)

            # 4. Assemble the final comprehensive response
            comprehensive_response = ComprehensiveAssessmentResponse(
                **base_assessment.model_dump(),
                frameworks=frameworks_in_assessment
            )
            return comprehensive_response

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error retrieving comprehensive assessment {assessment_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve comprehensive assessment details.")

    async def get_assessment_evidence_by_id(self, assessment_id: int) -> AssessmentEvidenceResponse:
        """
        Retrieves uploaded evidence files for an assessment along with the
        evidence mapped to each framework and control.
        """
        try:
            await self.get_assessment_by_id(assessment_id)

            uploaded_evidence_files = await self._get_uploaded_evidence_files(assessment_id)
            comprehensive = await self.get_comprehensive_assessment_by_id(assessment_id)

            frameworks = [
                AssessmentEvidenceFrameworkResponse(
                    id=framework.id,
                    framework_id=framework.framework_id,
                    framework_name=framework.framework_name,
                    version_id=framework.version_id,
                    controls=framework.controls,
                )
                for framework in comprehensive.frameworks
            ]

            return AssessmentEvidenceResponse(
                assessment_id=assessment_id,
                uploaded_evidence_files=uploaded_evidence_files,
                frameworks=frameworks,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error retrieving evidence for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve assessment evidence details.",
            )
  

    async def _perform_redis_operations(
        self,
        analysis_id: int,
        standard_id: int,
        version_id: int,
        files_to_enqueue: List[Dict[str, Any]],
        settings: Settings = get_settings()
    ) -> bool:
        """
        [Internal Helper] Adds an analysis session to Redis and enqueues file parsing tasks.
        """
        if not files_to_enqueue:
            logger.warning(f"Controller Helper: No files to enqueue for analysis {analysis_id}, skipping Redis.")
            return True

        try:
            logger.info(f"Controller Helper: Connecting to Redis for analysis {analysis_id}...")
            # --- 1. Add/Update Session State ---
            session_data = {
                "token": str(analysis_id),
                "total_files": len(files_to_enqueue),
                "parsed_files": 0,
                "indexed_files": 0,
            }
            session_expiry_seconds = int(settings.SESSION_EXPIRY_TIME)
            await self.redis_service.add_or_update_session(session_data, session_expiry_seconds)
            logger.info(f"Controller Helper: Created Redis session for analysis_id: {analysis_id}")

            # --- 2. Enqueue Parsing Tasks ---
            logger.info(f"Controller Helper: Enqueuing {len(files_to_enqueue)} parsing tasks for analysis {analysis_id}...")
            for file_info in files_to_enqueue:
                queue_message_payload = {
                    "analysis_id": str(analysis_id),
                    "control_label": file_info.get("control_label"),
                    "file_id": str(file_info.get("id")),
                    "file_path": file_info.get("path"),
                    "standard_id": str(standard_id),
                    "version_id": str(version_id)
                }
                await self.redis_service.send_message(queue_message_payload)
            
            logger.info(f"Controller Helper: Finished enqueuing for analysis {analysis_id}.")
            return True

        except redis_exceptions.RedisError as e:
            logger.exception(f"Controller Helper: Redis error for analysis {analysis_id}: {e}")
            return False
        except Exception as e:
            logger.exception(f"Controller Helper: Unexpected error in Redis operations for analysis {analysis_id}: {e}")
            return False

    async def _gather_enrichment_data_for_run(self, temp_file: Dict[str, Any]) -> Dict[str, Any]:
        """
        [Internal Helper for asyncio.gather]
        Concurrently fetches all required details for a single temp_file entry.
        """
        # Fetch related details concurrently
        file_details_res_task = self.db_service.call_procedure(StoredProcedures.FILTER_FILES, [temp_file['file_id'], None, None, None, 1, 1, 1])
        mapping_details_res_task = self.db_service.call_procedure(StoredProcedures.FILTER_ASSESSMENT_LABEL_MAPPINGS, [temp_file['assessment_label_mapping_id'], None, None, 1, 1, 1])
        
        # Await the initial results
        file_details_res, mapping_details_res = await asyncio.gather(file_details_res_task, mapping_details_res_task)

        if not file_details_res or not file_details_res[0]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File details not found for file_id={temp_file.get('file_id')}"
            )
        if not mapping_details_res or not mapping_details_res[0]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Assessment label mapping details not found for "
                    f"assessment_label_mapping_id={temp_file.get('assessment_label_mapping_id')}"
                )
            )
        
        file_path = file_details_res[0][0]['file_path']
        node_id = mapping_details_res[0][0]['node_id']
        node_identifier = str(node_id)
        assessment_label_id = mapping_details_res[0][0]['assessment_label_id']
        
        # Fetch label details first to get the correct version_id.
        label_details_res = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_LABELS,
            [assessment_label_id, None, None, None, True, 1, 1]
        )

        if not label_details_res or not label_details_res[0]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Assessment label details not found for assessment_label_id={assessment_label_id}"
            )

        framework_id = label_details_res[0][0]['framework_id']
        version_id = label_details_res[0][0]['version_id']
        
        version_files_res = await self.db_service.call_procedure(
            StoredProcedures.FILTER_VERSION_FILES,
            [None, version_id, None, True, 1, 1000]
        )

        if not version_files_res or not version_files_res[0]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Version files not found for version_id={version_id}"
            )

        node_details_res = []
        for version_file in version_files_res[0]:
            if node_identifier.isdigit():
                node_details_res = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_NODES,
                    [int(node_identifier), version_file['id'], None, None, None, None, None, True, 1, 1]
                )
            else:
                node_details_res = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_NODES,
                    [None, version_file['id'], node_identifier, None, None, None, None, True, 1, 1]
                )
            if node_details_res and node_details_res[0]:
                break

        # Fallback for controls that still exist only in temp nodes for the latest/draft version.
        if not node_details_res or not node_details_res[0]:
            for version_file in version_files_res[0]:
                if node_identifier.isdigit():
                    node_details_res = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_TEMP_NODES,
                        [int(node_identifier), version_file['id'], None, None, None, None, None, True, 1, 1]
                    )
                else:
                    node_details_res = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_TEMP_NODES,
                        [None, version_file['id'], node_identifier, None, None, None, None, True, 1, 1]
                    )
                if node_details_res and node_details_res[0]:
                    break

        # Final fallback: resolve by node identifier without version-file scoping.
        # Some historical mappings may point to nodes not linked with current version files.
        if not node_details_res or not node_details_res[0]:
            if node_identifier.isdigit():
                node_details_res = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_NODES,
                    [int(node_identifier), None, None, None, None, None, None, True, 1, 1]
                )
                if not node_details_res or not node_details_res[0]:
                    node_details_res = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_TEMP_NODES,
                        [int(node_identifier), None, None, None, None, None, None, True, 1, 1]
                    )
            else:
                node_details_res = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_NODES,
                    [None, None, node_identifier, None, None, None, None, True, 1, 1]
                )
                if not node_details_res or not node_details_res[0]:
                    node_details_res = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_TEMP_NODES,
                        [None, None, node_identifier, None, None, None, None, True, 1, 1]
                    )

        if not node_details_res or not node_details_res[0]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Node details not found for "
                    f"node_id={node_identifier} and version_id={version_id}"
                )
            )

        control_label_id = node_details_res[0][0]['label_id'] # CORRECTED: Get the actual label_id from node_details

        # --- Resolve the selected node to its CONTROL-level anchor(s) (config-driven). ---
        # The control level is the second-deepest framework_configuration level. When the
        # selected node is already a control this resolves to the node itself; when it is
        # above the control level the evidence fans out to every control descendant. The
        # anchor label_id is used as `control_label` so indexing and the downstream
        # criteria phase (which roots on the same control) stay aligned.
        control_types = await self._get_control_types_cached(framework_id)
        anchors = await self._resolve_control_anchors_for_node(node_id, control_types)
        if not anchors:
            anchors = [(str(node_id), control_label_id)]

        control_entries: List[Dict[str, Any]] = []
        for anchor_node_id, anchor_label_id in anchors:
            resolved_label = anchor_label_id or control_label_id
            control_entries.append({
                "node_id": str(anchor_node_id),
                "label_id": resolved_label,
                "redis_payload": {
                    "id": temp_file['file_id'],
                    "path": file_path,
                    "control_label": resolved_label,
                    "standard_id": framework_id,
                    "version_id": version_id,
                },
            })

        # Return all necessary info for later steps. `control_entries` is the fan-out of
        # this evidence file across its control-level anchor node(s).
        return {
            "assessment_label_mapping_id": temp_file['assessment_label_mapping_id'],
            "selected_node_id": str(node_id),
            "control_entries": control_entries,
        }

    async def run_assessment(self, assessment_id: int, user_id: str) -> RunAssessmentResponse:
        """
        Initiates an assessment run. This version is optimized with asyncio.gather
        and uses the correct label_id for controls.
        """
        workflow_service = AssessmentWorkflowService(self.db_service)

        # --- 0. WORKFLOW GUARDRAILS ---
        assessment_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENTS,
            [assessment_id, None, None, None, True, 1, 1],
        )
        if not assessment_result or not assessment_result[0]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found.")

        control_owner_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_CONTROL_OWNERS,
            [None, assessment_id, None, True, 1, 1],
        )
        if not control_owner_result or not control_owner_result[0]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active control owner is assigned to this assessment.",
            )

        control_owner_email = str(control_owner_result[0][0].get("email") or "").strip().lower()
        if str(user_id).strip().lower() != control_owner_email:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the assigned control owner can run or re-run this assessment.",
            )

        current_status = assessment_result[0][0].get("status")
        allowed_run_statuses = {
            AssessmentStatus.PENDING_EVIDENCE_MAPPING.value,
            AssessmentStatus.ASSESSMENT_IN_PROGRESS.value,
            AssessmentStatus.RUN_REVIEW_REJECTED.value,
            AssessmentStatus.FINAL_REJECTED.value,
        }
        if current_status not in allowed_run_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Assessment cannot be run in status '{current_status}'. "
                    "Allowed statuses: pending_evidence_mapping, assessment_in_progress, run_review_rejected, final_rejected."
                ),
            )

        # Allow re-run from assessment_in_progress only after current run processing has completed.
        if current_status == AssessmentStatus.ASSESSMENT_IN_PROGRESS.value:
            versions_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_VERSIONS,
                [None, assessment_id, None, None, 1, 1000],
            )
            version_rows = versions_res[0] if versions_res and versions_res[0] else []
            if not version_rows:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No assessment version found to re-run.",
                )

            latest_version = max(version_rows, key=lambda row: row.get("version_number") or 0)
            latest_version_id = latest_version.get("id")

            results_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_RESULTS_BY_LABEL,
                [None, latest_version_id, None, None, None, True, 1, 5000],
            )
            results_rows = results_res[0] if results_res and results_res[0] else []
            if not results_rows:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Latest run has no results yet. Wait for processing to initialize before re-running.",
                )

            await self._auto_resolve_stale_pending_rows(latest_version_id, results_rows, user_id)

            results_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_RESULTS_BY_LABEL,
                [None, latest_version_id, None, None, None, True, 1, 5000],
            )
            results_rows = results_res[0] if results_res and results_res[0] else []

            selected_control_node_ids = await self._get_selected_control_node_ids(assessment_id)

            pending_rows = self._get_control_level_pending_rows(results_rows, selected_control_node_ids)
            if pending_rows:
                logger.warning(
                    "Rerun blocked for assessment %s version %s due to pending selected-control rows: %s",
                    assessment_id,
                    latest_version_id,
                    [
                        {
                            "node_id": str(row.get("node_id") or ""),
                            "status": self._normalize_result_status(row.get("status")),
                            "label": row.get("label") or row.get("label_id"),
                        }
                        for row in pending_rows
                    ],
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Current run is still in progress. Wait for all controls to finish before re-running.",
                )

        can_run, run_validation_error = await workflow_service.validate_assessment_can_be_run(assessment_id)
        if not can_run:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=run_validation_error)

        # Rerun eligibility is governed by validate_assessment_can_be_run and
        # max version checks (run attempts); no additional fixed cap is applied
        # for final_rejected state.

        # --- 1. PREPARATION PHASE (OPTIMIZED WITH asyncio.gather) ---
        try:
            temp_files_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_TEMP_LABEL_MAPPING_WISE_FILES, 
                [None, assessment_id, None, None, True, 1, 5000]
            )
            temp_files_data = temp_files_results[0] if temp_files_results and temp_files_results[0] else []
            if not temp_files_data:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No evidence files mapped. Nothing to run.")

            # Prepare for concurrent data fetching
            tasks = [self._gather_enrichment_data_for_run(temp_file) for temp_file in temp_files_data]
            
            # Execute all data-gathering tasks concurrently
            enriched_data_list = await asyncio.gather(*tasks)

            # Process the results from asyncio.gather. Each enriched item fans its
            # evidence file out to one or more CONTROL-level anchor entries.
            files_to_enqueue = [
                entry['redis_payload']
                for data in enriched_data_list
                for entry in data['control_entries']
            ]

            # Build a stable control-node -> label map while preserving first non-null label.
            node_id_to_label_id_map: Dict[str, Optional[str]] = {}
            for data in enriched_data_list:
                for entry in data['control_entries']:
                    node_key = str(entry['node_id'])
                    if node_id_to_label_id_map.get(node_key):
                        continue
                    node_id_to_label_id_map[node_key] = entry['label_id']

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Pre-run failure while gathering data for assessment {assessment_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to gather data to start the run.")

        # --- 2. DATABASE WRITE PHASE (MUST BE SEQUENTIAL) ---
        new_assessment_version_id = -1
        new_version_number = -1
        # Tracks node_ids whose evidence is unchanged vs the previous active version.
        # For these we skip the GenAI re-run and clone forward the previous results.
        unchanged_node_ids: list = []
        changed_node_ids: set = set()
        previous_active_version_id: Optional[int] = None
        try:
            max_version_res = await self.db_service.call_procedure(StoredProcedures.FILTER_ASSESSMENT_VERSIONS, [None, assessment_id, None, None, 1, 1000])
            max_version_number = max(v['version_number'] for v in max_version_res[0]) if max_version_res and max_version_res[0] else 0
            if max_version_number >= 3:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Maximum total runs reached for this assessment (version 3). Re-run is not allowed.",
                )
            new_version_number = max_version_number + 1

            # --- MARK PREVIOUS VERSION AS DISCARDED (on rerun) ---
            # Before creating a new version, mark the previous active version as discarded
            # so that when creator reruns, the previous results are no longer the "current" version
            if max_version_number > 0 and max_version_res and max_version_res[0]:
                for version_row in max_version_res[0]:
                    if version_row.get('version_number') == max_version_number:
                        previous_active_version_id = version_row['id']
                        # Mark this version as discarded (previous version on rerun)
                        await self.db_service.call_procedure(
                            StoredProcedures.UPSERT_ASSESSMENT_VERSION,
                            [version_row['id'], assessment_id, max_version_number, True, user_id, 'discarded']
                        )

            # --- SELECTIVE RE-RUN: detect which controls have unchanged evidence ---
            # Compare current temp evidence (per assessment_label_mapping_id) with the
            # previous active version's persisted evidence. Controls with identical
            # file_id sets are considered "unchanged" and will not be re-analyzed.
            curr_evidence_by_mapping: dict = {}
            for tf in temp_files_data:
                curr_evidence_by_mapping.setdefault(tf['assessment_label_mapping_id'], set()).add(tf['file_id'])

            # Map each evidence mapping to the set of control-level anchor node_ids it feeds.
            mapping_to_anchor_ids: dict = {}
            for tf, enriched in zip(temp_files_data, enriched_data_list):
                anchor_ids = {str(entry['node_id']) for entry in enriched['control_entries']}
                mapping_to_anchor_ids.setdefault(tf['assessment_label_mapping_id'], set()).update(anchor_ids)

            unchanged_mapping_ids: set = set()
            if previous_active_version_id is not None:
                prev_files_res = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_LABEL_MAPPING_WISE_FILES,
                    [None, previous_active_version_id, None, None, True, 1, 5000]
                )
                prev_evidence_by_mapping: dict = {}
                if prev_files_res and prev_files_res[0]:
                    for row in prev_files_res[0]:
                        prev_evidence_by_mapping.setdefault(row['assessment_label_mapping_id'], set()).add(row['file_id'])

                for mid, file_set in curr_evidence_by_mapping.items():
                    if prev_evidence_by_mapping.get(mid) == file_set:
                        unchanged_mapping_ids.add(mid)

            # Resolve control-anchor node_id sets from unchanged / changed mappings.
            changed_mapping_ids = set(curr_evidence_by_mapping.keys()) - unchanged_mapping_ids
            changed_node_ids = set()
            for mid in changed_mapping_ids:
                changed_node_ids.update(mapping_to_anchor_ids.get(mid, set()))
            unchanged_node_ids = []
            for mid in unchanged_mapping_ids:
                unchanged_node_ids.extend(mapping_to_anchor_ids.get(mid, set()))
            # A control anchor shared by both a changed and an unchanged mapping must be
            # treated as changed (re-run) so stale results are not cloned over it.
            unchanged_node_ids = [
                n for n in dict.fromkeys(unchanged_node_ids) if n not in changed_node_ids
            ]

            version_res = await self.db_service.call_procedure(StoredProcedures.UPSERT_ASSESSMENT_VERSION, [None, assessment_id, new_version_number, 1, user_id, 'active'])
            new_assessment_version_id = version_res[0][0]['id']

            # Persist label_mapping_wise_file rows for the new version (all controls,
            # including unchanged ones, so the new version has the complete evidence map).
            for temp_file in temp_files_data:
                await self.db_service.call_procedure(StoredProcedures.UPSERT_LABEL_MAPPING_WISE_FILE, [None, new_assessment_version_id, temp_file['assessment_label_mapping_id'], temp_file['file_id'], 1, user_id])

            # Create results_by_label rows ONLY for changed/new controls (these will be
            # re-analyzed by the worker). Unchanged controls' results are cloned below.
            for node_id, label_id in node_id_to_label_id_map.items():
                if node_id in changed_node_ids:
                    resolved_label_id = label_id
                    if not resolved_label_id:
                        node_lookup = await self.db_service.call_procedure(
                            StoredProcedures.FILTER_NODES,
                            [None, None, node_id, None, None, None, None, True, 1, 1],
                        )
                        if node_lookup and node_lookup[0]:
                            resolved_label_id = node_lookup[0][0].get('label_id')

                    if not resolved_label_id:
                        temp_node_lookup = await self.db_service.call_procedure(
                            StoredProcedures.FILTER_TEMP_NODES,
                            [None, None, node_id, None, None, None, None, True, 1, 1],
                        )
                        if temp_node_lookup and temp_node_lookup[0]:
                            resolved_label_id = temp_node_lookup[0][0].get('label_id')

                    if not resolved_label_id:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Could not resolve control label for node_id={node_id}.",
                        )

                    await self.db_service.call_procedure(
                        StoredProcedures.UPSERT_RESULT_BY_LABEL,
                        [None, new_assessment_version_id, resolved_label_id, node_id, 'initiated', None, True, user_id]
                    )

            # Clone previous results for unchanged controls (single SP call).
            if unchanged_node_ids and previous_active_version_id is not None:
                node_ids_csv = ",".join(str(n) for n in unchanged_node_ids)
                try:
                    await self.db_service.call_procedure(
                        StoredProcedures.CLONE_UNCHANGED_CONTROL_RESULTS,
                        [previous_active_version_id, new_assessment_version_id, node_ids_csv, user_id]
                    )
                except Exception as clone_error:
                    clone_error_text = str(clone_error).lower()
                    if "clone_unchanged_control_results" in clone_error_text and (
                        "does not exist" in clone_error_text or "1305" in clone_error_text
                    ):
                        logger.warning(
                            "Stored procedure clone_unchanged_control_results not found; using fallback clone path."
                        )
                        await self._clone_unchanged_control_results_fallback(
                            previous_assessment_version_id=previous_active_version_id,
                            new_assessment_version_id=new_assessment_version_id,
                            node_ids=unchanged_node_ids,
                            user_id=user_id,
                        )
                    else:
                        raise

            await self.db_service.call_procedure(StoredProcedures.UPSERT_ASSESSMENT, [assessment_id, None, None, AssessmentStatus.ASSESSMENT_IN_PROGRESS.value, None, None, user_id])

            for temp_file in temp_files_data:
                await self.db_service.call_procedure(StoredProcedures.DELETE_TEMP_LABEL_MAPPING_WISE_FILE, [temp_file['id'], user_id])

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"CRITICAL: A database error occurred mid-run for assessment {assessment_id}. Error: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="A critical database error occurred during the run.")

        # --- 3. POST-DATABASE: TRIGGER BACKGROUND JOB ---
        # Filter the redis payload list to only control anchors that changed / are new.
        files_to_enqueue = [
            entry['redis_payload']
            for data in enriched_data_list
            for entry in data['control_entries']
            if str(entry['node_id']) in changed_node_ids
        ]

        # If everything was unchanged, there is nothing to analyze. The assessment
        # is already in 'assessment_in_progress' (set during the DB write phase) and
        # results have been cloned forward; the creator can directly submit for review.
        if not files_to_enqueue:
            await self._enqueue_kb_task(assessment_id, "run_started", version_number=new_version_number)
            return RunAssessmentResponse(
                message="Re-run completed: no evidence changes detected. Previous results were carried over.",
                assessment_id=assessment_id,
                new_assessment_version_id=new_assessment_version_id,
                new_version_number=new_version_number,
                enqueued_files_count=0,
                re_run_controls_count=0,
                carried_over_controls_count=len(unchanged_node_ids),
            )

        redis_success = False
        try:
            first_file = files_to_enqueue[0]
            redis_success = await self._perform_redis_operations(
                analysis_id=new_assessment_version_id,
                standard_id=first_file['standard_id'],
                version_id=first_file['version_id'],
                files_to_enqueue=files_to_enqueue
            )
        except Exception as e:
            logger.exception(f"Redis operation failed for assessment {assessment_id}, version {new_assessment_version_id}: {e}")
            redis_success = False

        # --- 4. HANDLE REDIS FAILURE: Log the failure to the database ---
        if not redis_success:
            logger.critical(f"Redis enqueue FAILED for assessment {assessment_id}, version {new_assessment_version_id}. Updating result statuses to 'failed'.")
            error_message = "Failed to enqueue for background processing. Please contact support to retry the run."
            try:
                results_to_update_res = await self.db_service.call_procedure(StoredProcedures.FILTER_RESULTS_BY_LABEL, [None, new_assessment_version_id, None, None, 'initiated', 1, 1, 5000])
                if results_to_update_res and results_to_update_res[0]:
                    for result in results_to_update_res[0]:
                        await self.db_service.call_procedure(
                            StoredProcedures.UPSERT_RESULT_BY_LABEL,
                            [
                                result['id'],
                                result.get('assessment_version_id', new_assessment_version_id),
                                result.get('label_id'),
                                result.get('node_id'),
                                'failed',
                                error_message,
                                result.get('is_active', True),
                                user_id,
                            ],
                        )

                return RunAssessmentResponse(
                    message="Assessment run initiated, but failed to trigger background processing. The run has been marked as failed.",
                    assessment_id=assessment_id,
                    new_assessment_version_id=new_assessment_version_id,
                    new_version_number=new_version_number,
                    enqueued_files_count=0,
                    re_run_controls_count=len(changed_node_ids),
                    carried_over_controls_count=len(unchanged_node_ids),
                )
            except Exception as db_e:
                logger.critical(f"DOUBLE FAULT: Failed to update result statuses to 'failed' for version {new_assessment_version_id} after Redis failure. DB Error: {db_e}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Run initiated but failed to log the job trigger failure. Please check system logs.")

        # --- 5. SUCCESS CASE ---
        await self._enqueue_kb_task(assessment_id, "run_started", version_number=new_version_number)
        return RunAssessmentResponse(
            message="Assessment run initiated successfully.",
            assessment_id=assessment_id,
            new_assessment_version_id=new_assessment_version_id,
            new_version_number=new_version_number,
            enqueued_files_count=len(files_to_enqueue),
            re_run_controls_count=len(changed_node_ids),
            carried_over_controls_count=len(unchanged_node_ids),
        )

    async def submit_assessment_for_review(self, assessment_id: int, user_id: str) -> SubmitAssessmentResponse:
        """
        Explicit creator action after run completion to move assessment into run-review.
        """
        assessment_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENTS,
            [assessment_id, None, None, None, True, 1, 1],
        )
        if not assessment_result or not assessment_result[0]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found.")

        assessment_row = assessment_result[0][0]

        control_owner_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_CONTROL_OWNERS,
            [None, assessment_id, None, True, 1, 1],
        )
        if not control_owner_result or not control_owner_result[0]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active control owner is assigned to this assessment.",
            )

        control_owner_email = str(control_owner_result[0][0].get("email") or "").strip().lower()
        if str(user_id).strip().lower() != control_owner_email:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the assigned control owner can submit assessment results for review.",
            )

        current_status = str(assessment_row.get("status") or "").strip().lower()
        allowed_submit_statuses = {
            AssessmentStatus.ASSESSMENT_IN_PROGRESS.value,
            AssessmentStatus.RUN_REVIEW_REJECTED.value,
            AssessmentStatus.FINAL_REJECTED.value,
            "active",
            "in_progress",
            "in-progress",
            "assessment-in-progress",
        }
        if current_status not in allowed_submit_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Assessment is in '{current_status}' status. "
                    "Submission is allowed only after a run state (active/in-progress)."
                ),
            )

        versions_res = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_VERSIONS,
            [None, assessment_id, None, None, 1, 1000],
        )
        version_rows = versions_res[0] if versions_res and versions_res[0] else []
        if not version_rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No assessment version found to submit.",
            )

        latest_version = max(version_rows, key=lambda row: row.get("version_number") or 0)
        latest_version_id = latest_version.get("id")
        latest_version_number = latest_version.get("version_number")

        results_res = await self.db_service.call_procedure(
            StoredProcedures.FILTER_RESULTS_BY_LABEL,
            [None, latest_version_id, None, None, None, True, 1, 5000],
        )
        results_rows = results_res[0] if results_res and results_res[0] else []
        if not results_rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No run results found for the latest version. Submit is not allowed.",
            )

        await self._auto_resolve_stale_pending_rows(latest_version_id, results_rows, user_id)

        results_res = await self.db_service.call_procedure(
            StoredProcedures.FILTER_RESULTS_BY_LABEL,
            [None, latest_version_id, None, None, None, True, 1, 5000],
        )
        results_rows = results_res[0] if results_res and results_res[0] else []

        selected_control_node_ids = await self._get_selected_control_node_ids(assessment_id)
        pending_rows = self._get_control_level_pending_rows(results_rows, selected_control_node_ids)
        if pending_rows:
            logger.warning(
                "Submit blocked for assessment %s version %s due to pending selected-control rows: %s",
                assessment_id,
                latest_version_id,
                [
                    {
                        "node_id": str(row.get("node_id") or ""),
                        "status": self._normalize_result_status(row.get("status")),
                        "label": row.get("label") or row.get("label_id"),
                    }
                    for row in pending_rows
                ],
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Run is not complete yet. Wait for all controls to finish before submitting.",
            )

        workflow_service = AssessmentWorkflowService(self.db_service)
        new_status = await workflow_service.transition_to_run_review(assessment_id, user_id)

        # --- MARK VERSION AS SUBMITTED ---
        # Update the latest version status to 'submitted' to indicate it has been submitted for review
        try:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT_VERSION,
                [latest_version_id, assessment_id, latest_version_number, True, user_id, 'submitted']
            )
        except Exception as e:
            logger.warning(f"Could not mark version {latest_version_id} as submitted: {e}")

        await self._enqueue_kb_task(assessment_id, "run_completed", version_number=latest_version_number)

        return SubmitAssessmentResponse(
            message="Assessment submitted for run review successfully.",
            assessment_id=assessment_id,
            current_status=new_status,
            latest_version_id=latest_version_id,
            latest_version_number=latest_version_number,
        )

    
    async def fetch_assessment_details_by_id(self, assessment_id: int) -> AssessmentLLMDetailsResponse:
        """
        Fetches assessment details along with the latest LLM-run results
        persisted in the database for that assessment.
        """
        try:
            assessment = await self.get_assessment_by_id(assessment_id)
            comprehensive = await self.get_comprehensive_assessment_by_id(assessment_id)
            control_owner_email = await self._get_assessment_control_owner_email(assessment_id)

            versions_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_VERSIONS,
                [None, assessment_id, None, None, 1, 1000],
            )
            latest_version_id: Optional[int] = None
            latest_version_number: Optional[int] = None
            results_by_label: List[Dict[str, Any]] = []
            detailed_results: List[Dict[str, Any]] = []

            if versions_res and versions_res[0]:
                latest_row = max(versions_res[0], key=lambda v: v.get("version_number", 0))
                latest_version_id = latest_row.get("id")
                latest_version_number = latest_row.get("version_number")

                if latest_version_id is not None:
                    results_by_label, detailed_results = await self._get_version_result_bundle(latest_version_id)

            evidence_mappings: List[EvidenceFileInfo] = []
            seen_evidence_mapping_ids: set[int] = set()
            for framework in comprehensive.frameworks:
                for control in framework.controls:
                    for ev in (control.evidence_files or []):
                        if ev.id in seen_evidence_mapping_ids:
                            continue
                        seen_evidence_mapping_ids.add(ev.id)
                        evidence_mappings.append(ev)

            return AssessmentLLMDetailsResponse(
                assessment=assessment,
                control_owner_email=control_owner_email,
                latest_version_id=latest_version_id,
                latest_version_number=latest_version_number,
                frameworks=comprehensive.frameworks,
                evidence_mappings=evidence_mappings,
                results_by_label=results_by_label,
                detailed_results=detailed_results,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error fetching assessment details for {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch assessment details.",
            )


    async def fetch_assessment_version_details(self, assessment_id: int, version_number: int) -> AssessmentLLMDetailsResponse:
        """
        Fetches details for a specific assessment version.
        """
        try:
            version_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_VERSIONS,
                [None, assessment_id, version_number, None, 1, 1],
            )
            if not version_res or not version_res[0]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Assessment version {version_number} for assessment {assessment_id} not found.",
                )

            version_data = version_res[0][0]
            assessment = await self.get_assessment_by_id(assessment_id)
            comprehensive = await self.get_comprehensive_assessment_by_id(assessment_id)
            control_owner_email = await self._get_assessment_control_owner_email(assessment_id)

            assessment_version_id = version_data.get("id")
            results_by_label, detailed_results = await self._get_version_result_bundle(assessment_version_id)

            evidence_mappings: List[EvidenceFileInfo] = []
            seen_evidence_mapping_ids: set[int] = set()
            for framework in comprehensive.frameworks:
                for control in framework.controls:
                    for ev in (control.evidence_files or []):
                        if ev.id in seen_evidence_mapping_ids:
                            continue
                        seen_evidence_mapping_ids.add(ev.id)
                        evidence_mappings.append(ev)

            return AssessmentLLMDetailsResponse(
                assessment=assessment,
                control_owner_email=control_owner_email,
                latest_version_id=assessment_version_id,
                latest_version_number=version_data.get("version_number"),
                frameworks=comprehensive.frameworks,
                evidence_mappings=evidence_mappings,
                results_by_label=results_by_label,
                detailed_results=detailed_results,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error fetching details for assessment version {version_number}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch version details",
            )

    async def fetch_assessment_results(
        self, assessment_id: int, version_number: Optional[int] = None
    ) -> "AssessmentResultsResponse":
        """
        Returns assessment results in a structured format: selected control nodes
        as parent items with their leaf criteria as children, split into
        completed / failed / in_progress buckets.
        """
        from app.models.assessment import (
            AssessmentResultsResponse,
            AssessmentResultsV1,
            AnalysisSplit,
            SubControlResultItem,
            CriteriaResultItem,
        )

        try:
            # --- local helpers ---
            async def _results_for_rbl(rbl_id: int) -> tuple:
                res = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_RESULTS,
                    [None, rbl_id, None, None, True, 1, 100],
                )
                return rbl_id, (res[0] if res and res[0] else [])

            async def _node_meta_lookup(node_id_str: str) -> tuple:
                try:
                    temp_nd = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_TEMP_NODES,
                        [None, None, node_id_str, None, None, None, None, True, 1, 1],
                    )
                    if temp_nd and temp_nd[0]:
                        row = temp_nd[0][0]
                        text = row.get("description") or row.get("name") or ""
                        return node_id_str, {
                            "text": text,
                            "source": "temp",
                            "parent_node_id": row.get("parent_node_id"),
                            "label_id": row.get("label_id"),
                        }

                    nd = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_NODES,
                        [None, None, node_id_str, None, None, None, None, True, 1, 1],
                    )
                    if nd and nd[0]:
                        row = nd[0][0]
                        text = row.get("description") or row.get("name") or ""
                        return node_id_str, {
                            "text": text,
                            "source": "node",
                            "parent_node_id": row.get("parent_node_id"),
                            "label_id": row.get("label_id"),
                        }
                except Exception as ex:
                    logger.warning("Node lookup failed for %s: %s", node_id_str, ex)
                return node_id_str, {
                    "text": "",
                    "source": "none",
                    "parent_node_id": None,
                    "label_id": None,
                }

            # --- 1. assessment + version ---
            assessment = await self.get_assessment_by_id(assessment_id)

            versions_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_VERSIONS,
                [None, assessment_id, None, None, 1, 1000],
            )
            if not versions_res or not versions_res[0]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No assessment version found.",
                )

            if version_number is not None:
                version_row = next(
                    (v for v in versions_res[0] if v.get("version_number") == version_number),
                    None,
                )
                if not version_row:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Version {version_number} not found for assessment {assessment_id}.",
                    )
            else:
                version_row = max(versions_res[0], key=lambda v: v.get("version_number") or 0)

            assessment_version_id: int = version_row["id"]
            current_version_number: int = int(version_row.get("version_number") or 1)
            previous_version_id: Optional[int] = None
            if current_version_number > 1:
                prev_row = next(
                    (
                        v for v in versions_res[0]
                        if int(v.get("version_number") or 0) == current_version_number - 1
                    ),
                    None,
                )
                if prev_row and prev_row.get("id") is not None:
                    previous_version_id = int(prev_row["id"])
            response_version_key = f"v{version_row.get('version_number') or 1}"
            version_status: str = str(version_row.get("status") or "")

            # --- 2. assessment label id string + selected node ids ---
            assessment_labels = await self._get_assessment_labels(assessment_id)
            assessment_label_id_str = (
                str(assessment_labels[0]["id"]) if assessment_labels else str(assessment_id)
            )

            # Resolve selected nodes to their CONTROL-level anchors so the parent result
            # item is always the control (config-driven), even when a node above the
            # control level was selected. Results are therefore reported at control level.
            selected_node_ids: set = await self._get_control_anchor_node_ids(assessment_id)

            # --- 3. all results_by_label rows for this version ---
            rbl_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_RESULTS_BY_LABEL,
                [None, assessment_version_id, None, None, None, True, 1, 5000],
            )
            rbl_rows: List[Dict] = rbl_res[0] if rbl_res and rbl_res[0] else []

            if not rbl_rows:
                return AssessmentResultsResponse(
                    data={
                        response_version_key: AssessmentResultsV1(
                            status=version_status or str(assessment.status),
                            assessment_id=str(assessment_id),
                            assessment_label_id=assessment_label_id_str,
                            analysis=AnalysisSplit(),
                        )
                    }
                )

            # --- 4. load detailed results for every rbl concurrently ---
            results_gathered = await asyncio.gather(
                *[_results_for_rbl(rbl["id"]) for rbl in rbl_rows]
            )
            results_by_rbl: Dict[int, List[Dict]] = dict(results_gathered)

            # --- 5. group rbl rows by label (each label = one selected control subtree) ---
            rbl_by_label: Dict[str, List[Dict]] = {}
            for rbl in rbl_rows:
                rbl_by_label.setdefault(str(rbl.get("label") or ""), []).append(rbl)

            # --- 6. build output lists ---
            completed: List[SubControlResultItem] = []
            failed: List[SubControlResultItem] = []
            in_progress: List[SubControlResultItem] = []

            for label, group in rbl_by_label.items():
                # Identify parent rbl: its node_id is one of the selected controls
                parent_rbl = next(
                    (rbl for rbl in group if str(rbl.get("node_id") or "") in selected_node_ids),
                    None,
                )
                # Fallback: node whose result has compliance set is a summary (parent)
                if parent_rbl is None:
                    parent_rbl = next(
                        (
                            rbl for rbl in group
                            if (results_by_rbl.get(rbl["id"]) or [{}])[0].get("compliance")
                        ),
                        None,
                    )
                if parent_rbl is None:
                    logger.warning(
                        "Cannot identify parent rbl for label '%s' in version %s, skipping.",
                        label,
                        assessment_version_id,
                    )
                    continue

                parent_nid = str(parent_rbl.get("node_id") or "")
                parent_result = (results_by_rbl.get(parent_rbl["id"]) or [{}])[0]
                rbl_status = str(parent_rbl.get("status") or "").strip().lower()

                # Candidate children under this selected control label.
                candidate_pairs = [
                    (rbl, (results_by_rbl.get(rbl["id"]) or [{}])[0])
                    for rbl in group
                    if str(rbl.get("node_id") or "") != parent_nid
                    and results_by_rbl.get(rbl["id"])  # has at least one result row
                ]

                # Rerun carry-over can leave current version with only parent summary rows.
                # If so, pull child rows from previous version for the same label to preserve
                # hierarchy visibility (T3.1, T3.1.1, criteria leaves).
                if not candidate_pairs and previous_version_id is not None:
                    prev_rbl_res = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_RESULTS_BY_LABEL,
                        [None, previous_version_id, label, None, None, True, 1, 5000],
                    )
                    prev_rbl_rows: List[Dict[str, Any]] = prev_rbl_res[0] if prev_rbl_res and prev_rbl_res[0] else []
                    if prev_rbl_rows:
                        prev_results_gathered = await asyncio.gather(
                            *[_results_for_rbl(rbl["id"]) for rbl in prev_rbl_rows]
                        )
                        prev_results_by_rbl: Dict[int, List[Dict]] = dict(prev_results_gathered)
                        candidate_pairs = [
                            (rbl, (prev_results_by_rbl.get(rbl["id"]) or [{}])[0])
                            for rbl in prev_rbl_rows
                            if str(rbl.get("node_id") or "") != parent_nid
                            and prev_results_by_rbl.get(rbl["id"])
                        ]
                        if candidate_pairs:
                            logger.info(
                                "Using previous version %s child rows for label '%s' in version %s.",
                                previous_version_id,
                                label,
                                assessment_version_id,
                            )

                # Resolve node metadata once for all candidates.
                candidate_node_ids = [str(rbl.get("node_id") or "") for rbl, _ in candidate_pairs]
                node_meta_lookup: Dict[str, Dict[str, Any]] = {}
                if candidate_node_ids:
                    lookup_rows = await asyncio.gather(
                        *[_node_meta_lookup(nid) for nid in candidate_node_ids]
                    )
                    node_meta_lookup = {nid: meta for nid, meta in lookup_rows}

                # Include all evaluated non-parent nodes (leaf criteria + intermediate
                # summary nodes) so clients can see full in-between hierarchy details.
                criteria_pairs = candidate_pairs

                candidate_node_set = {str(rbl.get("node_id") or "") for rbl, _ in criteria_pairs}
                children_by_parent: Dict[str, List[str]] = {}
                for child_node_id in candidate_node_set:
                    parent_node_id = str(node_meta_lookup.get(child_node_id, {}).get("parent_node_id") or "")
                    if parent_node_id:
                        children_by_parent.setdefault(parent_node_id, []).append(child_node_id)

                result_by_node_id: Dict[str, Dict[str, Any]] = {
                    str(rbl.get("node_id") or ""): result
                    for rbl, result in criteria_pairs
                }

                def _build_child_item(node_id: str) -> CriteriaResultItem:
                    meta = node_meta_lookup.get(node_id, {})
                    node_result = result_by_node_id.get(node_id, {})
                    nested_children_ids = sorted(children_by_parent.get(node_id, []))
                    nested_children = [_build_child_item(child_id) for child_id in nested_children_ids]
                    node_type = "sub_control" if nested_children else "criteria"
                    criteria_text = (meta.get("text") or "") if node_type == "criteria" else None
                    return CriteriaResultItem(
                        node_id=node_id,
                        label_id=meta.get("label_id"),
                        criteria=criteria_text,
                        adherence=node_result.get("adherence"),
                        reason=node_result.get("reason"),
                        recommendation=node_result.get("recommendation"),
                        node_type=node_type,
                        children=nested_children,
                    )

                root_child_ids = sorted(
                    [
                        node_id
                        for node_id in candidate_node_set
                        if str(node_meta_lookup.get(node_id, {}).get("parent_node_id") or "") == parent_nid
                    ]
                )
                if not root_child_ids:
                    root_child_ids = sorted(candidate_node_set)

                children = [_build_child_item(node_id) for node_id in root_child_ids]

                parent_adherence = parent_result.get("adherence")
                if parent_adherence is None:
                    child_adherence_values = [
                        c.adherence for c in children
                        if isinstance(c.adherence, (int, float))
                    ]
                    if child_adherence_values:
                        parent_adherence = round(sum(child_adherence_values) / len(child_adherence_values), 4)

                item = SubControlResultItem(
                    node_id=parent_nid,
                    label_id=label,
                    adherence=parent_adherence,
                    reason=parent_result.get("reason"),
                    compliance=self._derive_compliance_from_adherence(parent_adherence),
                    recommendation=parent_result.get("recommendation"),
                    children=children,
                )

                normalized_rbl_status = self._normalize_result_status(rbl_status)
                if normalized_rbl_status == "completed":
                    completed.append(item)
                elif normalized_rbl_status in {"failed", "error"}:
                    failed.append(item)
                else:
                    in_progress.append(item)

            # --- 7. overall status ---
            if in_progress:
                overall_status = "in_progress"
            elif failed and not completed:
                overall_status = "failed"
            else:
                overall_status = version_status or str(assessment.status)

            return AssessmentResultsResponse(
                data={
                    response_version_key: AssessmentResultsV1(
                        status=overall_status,
                        assessment_id=str(assessment_id),
                        assessment_label_id=assessment_label_id_str,
                        analysis=AnalysisSplit(
                            completed=completed,
                            failed=failed,
                            in_progress=in_progress,
                        ),
                    )
                }
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Error fetching assessment results for %s: %s", assessment_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch assessment results.",
            )