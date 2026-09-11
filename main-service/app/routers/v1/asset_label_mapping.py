"""[DEPRECATED] Control-level asset tagging endpoints.

These endpoints tag assets to an individual CONTROL (assessment_label_mapping_id)
and are retained for backward compatibility only. New clients should use the
assessment-level evidence scope API instead:
    GET/PUT /assessments/{assessment_id}/evidence-scope
"""
from typing import List
from fastapi import APIRouter, status, Depends
from app.models.files import AssetLabelMappingCreate, AssetLabelMappingResponse
from app.dependencies import get_db_service, CurrentUserDep, has_permission
from app.services.database_service import DatabaseService
from app.controllers.asset_label_mappings_controller import AssetLabelMappingsController
from app.core.permissions import Permission

router = APIRouter()

@router.post(
    path="/{assessment_label_mapping_id}/asset",
    response_model=List[AssetLabelMappingResponse],
    status_code= status.HTTP_201_CREATED,
    summary="Add asset tagging to an assessment label mapping",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def add_asset_label_mapping(
    assessment_label_mapping_id: int,
    data: AssetLabelMappingCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
):
    controller = AssetLabelMappingsController(db_service)
    return await controller.add_asset_label_mapping(
        assessment_label_mapping_id,
        data,
        current_user.email
    )


@router.delete(
    path="/{label_mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete asset tagging for an assessment label mapping",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def delete_asset_label_mapping(
    label_mapping_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    controller = AssetLabelMappingsController(db_service)
    await controller.delete_asset_label_mapping(label_mapping_id, current_user.email)


@router.put(
    path="/{assessment_label_mapping_id}/asset",
    response_model=List[AssetLabelMappingResponse],
    status_code=status.HTTP_200_OK,
    summary="Update and replace all asset tags for a control",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def update_asset_label_mapping(
    assessment_label_mapping_id: int,
    data: AssetLabelMappingCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
):
    """
    Replaces the entire set of asset tags for a given control.
    Any existing asset tags not in the payload will be removed.
    Any new asset IDs in the payload will be added.
    """
    controller = AssetLabelMappingsController(db_service)
    return await controller.update_asset_label_mapping(
        assessment_label_mapping_id,
        data,
        current_user.email
    )