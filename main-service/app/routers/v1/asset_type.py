from typing import List, Optional

from fastapi import APIRouter, Depends, status

from app.controllers.asset_type_controller import AssetTypeController
from app.dependencies import CurrentUserDep, get_db_service, has_permission
from app.models.files import AssetTypeCreate, AssetTypeInDB, AssetTypeResponse, AssetTypeUpdate
from app.services.database_service import DatabaseService
from app.core.permissions import Permission


router = APIRouter()


@router.post(
    "/",
    response_model=List[AssetTypeInDB],
    status_code=status.HTTP_201_CREATED,
    summary="Create new asset types",
    description="Creates new asset types with the provided details.",
    dependencies=[Depends(has_permission([Permission.ASSET_TYPE_CREATE]))]
)
async def create_asset_type(
    asset_type_data: AssetTypeCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[AssetTypeInDB]:
    """Endpoint to create a new asset types."""
    controller = AssetTypeController(db_service)
    return await controller.create_asset_type(asset_type_data, current_user.email)


@router.get(
    "/{asset_type_id}",
    response_model=List[AssetTypeInDB],
    status_code=status.HTTP_200_OK,
    summary="Retrieve an asset type by ID",
    description="Retrieves the details of a single asset type using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ASSET_TYPE_READ]))]
)
async def get_asset_type_by_id(
    asset_type_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[AssetTypeInDB]:
    """Endpoint to retrieve an asset type by its ID."""
    controller = AssetTypeController(db_service)
    return await controller.get_asset_types(asset_type_id=asset_type_id, page_number=1, page_size=1)


@router.get(
    "/organization/{org_id}",
    response_model=AssetTypeResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all asset types",
    description="Retrieves a list of all asset types currently in the system.",
    dependencies=[Depends(has_permission([Permission.ASSET_TYPE_READ]))]
)
async def get_all_asset_types_by_org_id(
    org_id: int,
    current_user: CurrentUserDep,
    page_number: Optional[int] = None,
    page_size: Optional[int] = None,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssetTypeResponse:
    """Endpoint to retrieve all asset types by organization ID."""
    controller = AssetTypeController(db_service)
    return await controller.get_asset_types(org_id=org_id, page_number=page_number, page_size=page_size, count_required=True)


@router.patch(
    "/{asset_type_id}",
    response_model=List[AssetTypeInDB],
    status_code=status.HTTP_200_OK,
    summary="Update an existing asset type",
    description="Updates the details of an existing asset type using its unique ID. Partial updates are supported.",
    dependencies=[Depends(has_permission([Permission.ASSET_TYPE_UPDATE]))]
)
async def update_asset_type(
    asset_type_id: int,
    asset_type_data: AssetTypeUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[AssetTypeInDB]:
    """Endpoint to update an existing asset type."""
    controller = AssetTypeController(db_service)
    return await controller.update_asset_type(asset_type_id, asset_type_data, current_user.email)


@router.delete(
    "/{asset_type_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an asset type",
    description="Deletes an asset type from the system using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ASSET_TYPE_DELETE]))]
)
async def delete_asset_type(
    asset_type_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    """Endpoint to delete an asset type."""
    controller = AssetTypeController(db_service)
    await controller.delete_asset_type(asset_type_id, current_user.email)
    return None
