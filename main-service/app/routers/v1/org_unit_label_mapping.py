"""[DEPRECATED] Control-level org-unit tagging endpoints.

These endpoints tag org units to an individual CONTROL (assessment_label_mapping_id)
and are retained for backward compatibility only. New clients should use the
assessment-level evidence scope API instead:
    GET/PUT /assessments/{assessment_id}/evidence-scope
"""
from typing import List
from fastapi import APIRouter, status, Depends
from app.models.org import OrgUnitLabelMappingCreate, OrgUnitLabelMappingResponse
from app.dependencies import get_db_service, CurrentUserDep, has_permission
from app.services.database_service import DatabaseService
from app.controllers.org_unit_label_mappings_controller import OrgUnitLabelMappingsController
from app.core.permissions import Permission

router = APIRouter()

@router.post(
    path="/{assessment_label_mapping_id}/org_unit",
    response_model=List[OrgUnitLabelMappingResponse],
    status_code= status.HTTP_201_CREATED,
    summary="Add org_unit tagging to an assessment label mapping",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def add_org_unit_label_mapping(
    assessment_label_mapping_id: int,
    data: OrgUnitLabelMappingCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
):
    controller = OrgUnitLabelMappingsController(db_service)
    return await controller.add_org_unit_label_mapping(
        assessment_label_mapping_id,
        data,
        current_user.email
    )


@router.delete(
    path="/{label_mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete org_unit tagging for an assessment label mapping",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def delete_org_unit_label_mapping(
    label_mapping_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    controller = OrgUnitLabelMappingsController(db_service)
    await controller.delete_org_unit_label_mapping(label_mapping_id, current_user.email)


@router.put(
    path="/{assessment_label_mapping_id}/org_unit",
    response_model=List[OrgUnitLabelMappingResponse],
    status_code=status.HTTP_200_OK,
    summary="Update and replace all org unit tags for a control",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def update_org_unit_label_mapping(
    assessment_label_mapping_id: int,
    data: OrgUnitLabelMappingCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
):
    """
    Replaces the entire set of org unit tags for a given control.
    Any existing tags not in the payload will be removed.
    Any new org unit IDs in the payload will be added.
    """
    controller = OrgUnitLabelMappingsController(db_service)
    return await controller.update_org_unit_label_mapping(
        assessment_label_mapping_id,
        data,
        current_user.email
    )
