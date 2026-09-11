from fastapi import APIRouter, Depends, status

from app.controllers.assessment_label_controller import AssessmentLabelController
from app.dependencies import get_db_service, CurrentUserDep, has_permission
from app.models.assessment import AssessmentLabelCreate, AssessmentLabelResponse, AssessmentLabelRead
from app.services.database_service import DatabaseService
from app.core.permissions import Permission

router = APIRouter()

@router.post(
    "/",
    response_model=AssessmentLabelResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add framework to an assessment",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_CREATE]))]
)
async def add_assessment_label(
    assessment_label_data: AssessmentLabelCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentLabelResponse:
    """Endpoint to add a framework to an assessment."""
    controller = AssessmentLabelController(db_service)
    return await controller.add_assessment_label(assessment_label_data, user_id=current_user.email)


@router.get(
    "/{assessment_label_id}",
    response_model=AssessmentLabelRead,
    status_code=status.HTTP_200_OK,
    summary="View framework in an assessment",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def get_assessment_label(
    assessment_label_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentLabelRead:
    controller = AssessmentLabelController(db_service)
    return await controller.get_assessment_label(assessment_label_id)


@router.delete(
    "/{assessment_label_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an assessment label",
    description="Deletes a framework from an assessment using its unique assessment_label ID.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_DELETE]))]
)
async def delete_assessment_label(
    assessment_label_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    """Endpoint to remove a framework from an assessment."""
    controller = AssessmentLabelController(db_service)
    await controller.delete_assessment_label(assessment_label_id, user_id=current_user.email)
    return None