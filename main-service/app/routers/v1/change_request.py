from typing import List

from fastapi import APIRouter, Depends, status, Query

from app.controllers.change_request_controller import ChangeRequestController
from app.dependencies import CurrentUserDep, get_db_service, has_permission
from app.models.change import ChangeRequestResponse
from app.services.database_service import DatabaseService
from app.core.permissions import Permission

router = APIRouter()


@router.get(
    "/version/{version_id}",
    response_model=List[ChangeRequestResponse],
    status_code=status.HTTP_200_OK,
    summary="Retrieve change requests by version ID",
    description="Retrieves a list of all change requests associated to version id currently in the system.",
    dependencies=[Depends(has_permission([Permission.CHANGE_REQUEST_READ]))]
)
async def get_change_requests_by_version_id(
    version_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(1, ge=1, description="Page number to retrieve"),
    page_size: int = Query(10, ge=1, le=100, description="Number of change requests per page")
) -> List[ChangeRequestResponse]:
    """Endpoint to retrieve all change requests by version ID."""
    controller = ChangeRequestController(db_service)
    return await controller.get_change_requests_by_filters(version_id=version_id, page_number=page_number, page_size=page_size)


@router.patch(
    "/{change_request_id}",
    response_model=ChangeRequestResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an existing change request",
    description="Updates the details of an existing change request using its unique ID. Partial updates are supported.",
    dependencies=[Depends(has_permission([Permission.CHANGE_REQUEST_UPDATE]))]
)
async def update_change_request(
    change_request_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> ChangeRequestResponse:
    """Endpoint to update an existing change request."""
    controller = ChangeRequestController(db_service)
    return await controller.update_change_request(change_request_id, current_user.email)
