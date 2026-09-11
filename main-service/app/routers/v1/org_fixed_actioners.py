from fastapi import APIRouter, Depends, status

from app.controllers.org_fixed_actioner_controller import OrgActionerController
from app.dependencies import get_db_service, has_permission, CurrentUserDep
from app.models.org import OrgActionerCreate, OrgActionerUpdate, OrgActionersResponse, OrgGateSettingActionersResponse
from app.services.database_service import DatabaseService
from app.core.permissions import Permission


router = APIRouter()


@router.post(
    "/",
    response_model=OrgGateSettingActionersResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new organization actioners",
    description="Creates new organization actioners with the provided details.",
    dependencies=[Depends(has_permission([Permission.ORG_FIXED_ACTIONERS_CREATE]))]
)
async def create_org_actioners(
    org_actioner_data: OrgActionerCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgGateSettingActionersResponse:
    """Endpoint to create new organization actioners."""
    controller = OrgActionerController(db_service)
    return await controller.create_org_actioners(org_actioner_data, current_user.email)


@router.get(
    "/organization/{org_id}",
    response_model=OrgActionersResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve organization actioners by organization ID",
    description="Retrieves the details of organization actioners using its organization ID.",
    dependencies=[Depends(has_permission([Permission.ORG_FIXED_ACTIONERS_READ]))]
)
async def get_org_actioners_by_org_id(
    org_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgActionersResponse:
    """Endpoint to retrieve organization actioners by its organization ID."""
    controller = OrgActionerController(db_service)
    return await controller.get_org_actioners_by_org_id(org_id)


@router.patch(
    "/{org_actioner_id}",
    response_model=OrgGateSettingActionersResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an existing organization actioner.",
    description="Updates the details of an existing organization actioner using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ORG_FIXED_ACTIONERS_UPDATE]))]
)
async def update_org_actioner(
    org_actioner_id: int,
    org_actioner_data: OrgActionerUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgGateSettingActionersResponse:
    """Endpoint to update an existing organization actioner."""
    controller = OrgActionerController(db_service)
    return await controller.update_org_actioner(org_actioner_id, org_actioner_data, current_user.email)


@router.delete(
    "/organization/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete organizational actioners by organization",
    description="Deletes organizational actioners by organization.",
    dependencies=[Depends(has_permission([Permission.ORG_FIXED_ACTIONERS_DELETE]))]
)
async def delete_org_actioners_by_org_id(
    org_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    controller = OrgActionerController(db_service)
    await controller.delete_org_actioners_by_org_id(org_id, current_user.email)
    return None
