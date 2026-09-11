from fastapi import APIRouter, status, Depends

from app.models.assessment import EvidenceScopeUpdate, EvidenceScopeResponse
from app.dependencies import get_db_service, CurrentUserDep, has_permission
from app.services.database_service import DatabaseService
from app.controllers.evidence_scope_controller import EvidenceScopeController
from app.core.permissions import Permission

router = APIRouter()


@router.get(
    path="/{assessment_id}/evidence-scope",
    response_model=EvidenceScopeResponse,
    status_code=status.HTTP_200_OK,
    summary="Get the evidence scope (org units, people, assets) for an assessment",
    description=(
        "Returns all organization unit, people and asset tags applied to the "
        "assessment. Used to re-hydrate the Evidence Scope dropdowns when the "
        "user revisits the screen."
    ),
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))],
)
async def get_evidence_scope(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
) -> EvidenceScopeResponse:
    controller = EvidenceScopeController(db_service)
    return await controller.get_evidence_scope(assessment_id)


@router.put(
    path="/{assessment_id}/evidence-scope",
    response_model=EvidenceScopeResponse,
    status_code=status.HTTP_200_OK,
    summary="Save the evidence scope for an assessment (single Save & Continue API)",
    description=(
        "Replaces the assessment's evidence scope in one call. `Software` and "
        "`Hardware` selections are both assets and are sent merged in `asset_ids`. "
        "Each category is replaced when provided and left unchanged when omitted "
        "(null), so the same endpoint handles both the initial save and updates "
        "when the user navigates back and changes values."
    ),
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))],
)
async def save_evidence_scope(
    assessment_id: int,
    data: EvidenceScopeUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
) -> EvidenceScopeResponse:
    controller = EvidenceScopeController(db_service)
    return await controller.replace_evidence_scope(assessment_id, data, current_user.email)
