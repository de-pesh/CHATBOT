import logging
from typing import List, Dict

from fastapi import HTTPException, status

from app.services.database_service import DatabaseService
from app.core.stored_procedures import StoredProcedures
from app.models.assessment import (
    EvidenceScopeUpdate,
    EvidenceScopeResponse,
    AssetAssessmentMappingResponse,
    OrgUnitAssessmentMappingResponse,
    PeopleAssessmentMappingResponse,
)

logger = logging.getLogger(__name__)

# Page size used when reading the full set of active tags for an assessment.
_MAX_PAGE_SIZE = 1000


class EvidenceScopeController:
    """Manages asset / people / org-unit tagging at the ASSESSMENT level.

    Backs the consolidated "Evidence Scope" step: a single Save & Continue
    call replaces the org-unit, people and asset tags for an assessment, and
    a GET re-hydrates the dropdowns when the user revisits the screen.
    """

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    # ------------------------------------------------------------------
    # Validation helper
    # ------------------------------------------------------------------
    async def _ensure_assessment_exists(self, assessment_id: int) -> None:
        results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENTS,
            [assessment_id, None, None, None, True, 1, 1],
        )
        if not results or not results[0]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Assessment with ID {assessment_id} not found.",
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    async def _fetch_active_assets(self, assessment_id: int) -> List[dict]:
        res = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSET_ASSESSMENT_MAPPINGS,
            [None, None, assessment_id, True, 1, _MAX_PAGE_SIZE],
        )
        return res[0] if res and res[0] else []

    async def _fetch_active_org_units(self, assessment_id: int) -> List[dict]:
        res = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ORG_UNIT_ASSESSMENT_MAPPINGS,
            [None, None, assessment_id, True, 1, _MAX_PAGE_SIZE],
        )
        return res[0] if res and res[0] else []

    async def _fetch_active_people(self, assessment_id: int) -> List[dict]:
        res = await self.db_service.call_procedure(
            StoredProcedures.FILTER_PEOPLE_ASSESSMENT_MAPPINGS,
            [None, None, assessment_id, True, 1, _MAX_PAGE_SIZE],
        )
        return res[0] if res and res[0] else []

    async def get_evidence_scope(self, assessment_id: int) -> EvidenceScopeResponse:
        """Return all org-unit, people and asset tags for an assessment."""
        await self._ensure_assessment_exists(assessment_id)
        try:
            asset_rows = await self._fetch_active_assets(assessment_id)
            org_unit_rows = await self._fetch_active_org_units(assessment_id)
            people_rows = await self._fetch_active_people(assessment_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error fetching evidence scope for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch assessment evidence scope.",
            )

        return EvidenceScopeResponse(
            assessment_id=assessment_id,
            assets=[AssetAssessmentMappingResponse(**row) for row in asset_rows],
            org_units=[OrgUnitAssessmentMappingResponse(**row) for row in org_unit_rows],
            people=[PeopleAssessmentMappingResponse(**row) for row in people_rows],
        )

    # ------------------------------------------------------------------
    # Write (replace / sync)
    # ------------------------------------------------------------------
    async def _sync_assets(self, assessment_id: int, asset_ids: List[int], user_id: str) -> None:
        current_rows = await self._fetch_active_assets(assessment_id)
        current_map: Dict[int, int] = {row["asset_id"]: row["id"] for row in current_rows}
        current_ids = set(current_map.keys())
        desired_ids = set(asset_ids)

        for asset_id in current_ids - desired_ids:
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ASSET_ASSESSMENT_MAPPING,
                [current_map[asset_id], user_id],
            )
        for asset_id in desired_ids - current_ids:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSET_ASSESSMENT_MAPPING,
                [None, asset_id, assessment_id, True, user_id],
            )

    async def _sync_org_units(self, assessment_id: int, org_unit_ids: List[int], user_id: str) -> None:
        current_rows = await self._fetch_active_org_units(assessment_id)
        current_map: Dict[int, int] = {row["org_unit_id"]: row["id"] for row in current_rows}
        current_ids = set(current_map.keys())
        desired_ids = set(org_unit_ids)

        for org_unit_id in current_ids - desired_ids:
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ORG_UNIT_ASSESSMENT_MAPPING,
                [current_map[org_unit_id], user_id],
            )
        for org_unit_id in desired_ids - current_ids:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_UNIT_ASSESSMENT_MAPPING,
                [None, org_unit_id, assessment_id, True, user_id],
            )

    async def _sync_people(self, assessment_id: int, emails: List[str], user_id: str) -> None:
        current_rows = await self._fetch_active_people(assessment_id)
        current_map: Dict[str, int] = {row["email"]: row["id"] for row in current_rows}
        current_emails = set(current_map.keys())
        desired_emails = set(emails)

        for email in current_emails - desired_emails:
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_PEOPLE_ASSESSMENT_MAPPING,
                [current_map[email], user_id],
            )
        for email in desired_emails - current_emails:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_PEOPLE_ASSESSMENT_MAPPING,
                [None, email, assessment_id, True, user_id],
            )

    async def replace_evidence_scope(
        self,
        assessment_id: int,
        data: EvidenceScopeUpdate,
        user_id: str,
    ) -> EvidenceScopeResponse:
        """Replace the assessment's evidence scope in a single call.

        For each category, ``None`` leaves it unchanged while a provided list
        (including ``[]``) replaces it. Returns the resulting scope so the
        frontend can refresh its dropdowns.
        """
        await self._ensure_assessment_exists(assessment_id)
        try:
            if data.org_unit_ids is not None:
                await self._sync_org_units(assessment_id, data.org_unit_ids, user_id)
            if data.emails is not None:
                await self._sync_people(assessment_id, [str(e) for e in data.emails], user_id)
            if data.asset_ids is not None:
                await self._sync_assets(assessment_id, data.asset_ids, user_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating evidence scope for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update assessment evidence scope.",
            )

        return await self.get_evidence_scope(assessment_id)
