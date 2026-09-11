"""[DEPRECATED] Control-level people tagging endpoints.

These endpoints tag people to an individual CONTROL (assessment_label_mapping_id)
and are retained for backward compatibility only. New clients should use the
assessment-level evidence scope API instead:
    GET/PUT /assessments/{assessment_id}/evidence-scope
"""
from typing import List
from fastapi import APIRouter, status, Depends
from app.models.files import PeopleLabelMappingCreate, PeopleLabelMappingResponse
from app.dependencies import get_db_service, CurrentUserDep, has_permission
from app.services.database_service import DatabaseService
from app.controllers.people_label_mappings_controller import PeopleLabelMappingsController
from app.core.permissions import Permission

router = APIRouter()

@router.post(
    path="/{assessment_label_mapping_id}/people",
    response_model=List[PeopleLabelMappingResponse],
    status_code= status.HTTP_201_CREATED,
    summary="Add people tagging to an assessment label mapping",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def add_people_label_mapping(
    assessment_label_mapping_id: int,
    data: PeopleLabelMappingCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
):
    controller = PeopleLabelMappingsController(db_service)
    return await controller.create_people_label_mapping(
        assessment_label_mapping_id,
        data,
        current_user.email
    )


@router.delete(
    path="/{label_mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete people tagging for an assessment label mapping",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def delete_people_label_mapping(
    label_mapping_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    controller = PeopleLabelMappingsController(db_service)
    await controller.delete_people_label_mapping(label_mapping_id, current_user.email)


@router.put(
    path="/{assessment_label_mapping_id}/people",
    response_model=List[PeopleLabelMappingResponse],
    status_code=status.HTTP_200_OK,
    summary="Update and replace all people tags for a control",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def update_people_label_mapping(
    assessment_label_mapping_id: int,
    data: PeopleLabelMappingCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
):
    """
    Replaces the entire set of people tags for a given control.
    Any existing tags not in the payload will be removed.
    Any new emails in the payload will be added.
    """
    controller = PeopleLabelMappingsController(db_service)
    return await controller.update_people_label_mapping(
        assessment_label_mapping_id,
        data,
        current_user.email
    )