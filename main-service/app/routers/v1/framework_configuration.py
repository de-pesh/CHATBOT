from fastapi import APIRouter, Depends, status

from app.controllers.framework_configuration_controller import FrameworkConfigurationController
from app.dependencies import CurrentUserDep, get_db_service, has_permission
from app.models.framework import FrameworkConfigurationCreatePayload, FrameworkConfigurationUpdatePayload, FrameworkConfigurationsResponse
from app.services.database_service import DatabaseService
from app.core.permissions import Permission

router = APIRouter()


@router.post(
    "/",
    response_model=FrameworkConfigurationsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new framework configuration",
    description="Creates new framework configuration with the provided details.",
    dependencies=[Depends(has_permission([Permission.FRAMEWORK_CONFIGURATION_CREATE]))]
)
async def create_framework_configuration(
    framework_configuration_data: FrameworkConfigurationCreatePayload,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> FrameworkConfigurationsResponse:
    """Endpoint to create new framework configuration."""
    controller = FrameworkConfigurationController(db_service)
    return await controller.create_framework_configuration(framework_configuration_data, current_user.email)


@router.get(
    "/framework/{framework_id}",
    response_model=FrameworkConfigurationsResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve framework configuration by framework ID",
    description="Retrieves the details of framework configuration using its framework ID.",
    dependencies=[Depends(has_permission([Permission.FRAMEWORK_CONFIGURATION_READ]))]
)
async def get_framework_configuration_by_framework_id(
    framework_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> FrameworkConfigurationsResponse:
    """Endpoint to retrieve framework configuration by its framework ID."""
    controller = FrameworkConfigurationController(db_service)
    return await controller.get_framework_configuration_by_framework_id(framework_id)


@router.patch(
    "/{framework_configuration_id}",
    response_model=FrameworkConfigurationsResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an existing framework configuration.",
    description="Updates the details of an existing framework configuration using its unique ID.",
    dependencies=[Depends(has_permission([Permission.FRAMEWORK_CONFIGURATION_UPDATE]))]
)
async def update_framework_configuration(
    framework_configuration_id: int,
    framework_configuration_data: FrameworkConfigurationUpdatePayload,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> FrameworkConfigurationUpdatePayload:
    """Endpoint to update an existing framework configuration."""
    controller = FrameworkConfigurationController(db_service)
    return await controller.update_framework_configuration(framework_configuration_id, framework_configuration_data, current_user.email)
