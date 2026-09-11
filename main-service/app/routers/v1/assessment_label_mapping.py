from typing import List
from fastapi import APIRouter, status, Depends
from app.models.assessment import (
    LabelMappingInDB,
    LabelMappingReplaceRequest,
    BulkLabelMappingCreatePayload,
    BulkLabelMappingReplacePayload,
    ControlTaggingResponse,
)
from app.dependencies import CurrentUserDep, get_db_service, has_permission
from app.services.database_service import DatabaseService
from app.controllers.assessment_label_mappings_controller import LabelMappingsController
from app.core.permissions import Permission

router = APIRouter()

@router.post(
    path="/{assessment_label_id}/node-id/{node_id}/map",
    response_model=LabelMappingInDB,
    status_code= status.HTTP_201_CREATED,
    summary="Map control to an assessment label",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def initiate_assessment_label_mapping(
    assessment_label_id: int,
    node_id: str,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> LabelMappingInDB:
    controller = LabelMappingsController(db_service)
    return await controller.add_assessment_label_mapping(
        assessment_label_id, 
        node_id, 
        user_id=current_user.email
    )


@router.patch(
    path="/{label_mapping_id}",
    response_model=LabelMappingInDB,
    status_code=status.HTTP_200_OK,
    summary="Replace mapped control on an assessment label",
    description="Updates the node_id for an existing assessment control mapping while preserving the same mapping record.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def replace_assessment_label_mapping(
    label_mapping_id: int,
    mapping_data: LabelMappingReplaceRequest,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> LabelMappingInDB:
    controller = LabelMappingsController(db_service)
    return await controller.replace_assessment_label_mapping(label_mapping_id, mapping_data, current_user.email)


@router.get(
    "/{label_mapping_id}/tagging",
    response_model=ControlTaggingResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch control mapped to an assessment label and its tagging",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def fetch_control_and_tagging(
    label_mapping_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> ControlTaggingResponse:
    controller = LabelMappingsController(db_service)
    return await controller.fetch_control_and_tagging(label_mapping_id)


@router.post(
    path="/bulk/map",
    response_model=List[LabelMappingInDB],
    status_code=status.HTTP_201_CREATED,
    summary="Bulk map controls to an assessment label",
    description="Maps multiple controls to a single assessment label in one request.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def bulk_add_assessment_label_mappings(
    payload: BulkLabelMappingCreatePayload,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[LabelMappingInDB]:
    controller = LabelMappingsController(db_service)
    return await controller.bulk_add_assessment_label_mappings(payload, current_user.email)


@router.patch(
    path="/bulk/replace",
    response_model=List[LabelMappingInDB],
    status_code=status.HTTP_200_OK,
    summary="Bulk update mapped controls",
    description="Updates multiple control mappings in a single request.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def bulk_replace_assessment_label_mappings(
    payload: BulkLabelMappingReplacePayload,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[LabelMappingInDB]:
    controller = LabelMappingsController(db_service)
    return await controller.bulk_replace_assessment_label_mappings(payload, current_user.email)


@router.delete(
    path="/{label_mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete control from an assessment label",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def delete_assessment_label_mapping(
    label_mapping_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    controller = LabelMappingsController(db_service)
    await controller.delete_assessment_label_mapping(label_mapping_id, current_user.email)
