# app/routers/evidence_mapping_router.py

from typing import List
from fastapi import APIRouter, Depends, status
from app.controllers.label_mapping_wise_files_controller import EvidenceMappingController
from app.dependencies import CurrentUserDep, get_db_service, has_permission
from app.models.files import (
    EvidenceMappingsCreatePayload,
    EvidenceMappingsDeletePayload,
    EvidenceMappingResponse
)
from app.services.database_service import DatabaseService
from app.core.permissions import Permission

router = APIRouter(prefix="/evidence-mappings", tags=["Evidence Mappings"])


@router.post(
    "/",
    response_model=List[EvidenceMappingResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Map one or more evidence files to a control",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def map_evidence_to_control(
    data: EvidenceMappingsCreatePayload,
    user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
):
    """
    Creates mappings between a list of evidence file IDs and a single assessment control,
    specified by the `assessment_id` and `assessment_label_mapping_id`.
    """
    controller = EvidenceMappingController(db_service)
    return await controller.create_evidence_mappings(data, user.email)

@router.delete(
    "/",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one or more evidence-to-control mappings",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def delete_evidence_to_control_mappings(
    data: EvidenceMappingsDeletePayload,
    user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
):
    """
    Deletes one or more evidence-to-control mappings based on a list of their
    unique mapping IDs (`label_wise_files.id`).
    """
    controller = EvidenceMappingController(db_service)
    await controller.delete_evidence_mappings(data, user.email)
