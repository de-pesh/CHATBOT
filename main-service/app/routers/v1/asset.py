from typing import List, Optional

from fastapi import APIRouter, Depends, status

from app.controllers.asset_controller import AssetController
from app.dependencies import CurrentUserDep, get_db_service, has_permission
from app.models.files import AssetCreate, AssetInDB, AssetResponse, AssetUpdate
from app.services.database_service import DatabaseService
from app.core.permissions import Permission


router = APIRouter()


@router.post(
    "/",
    response_model=List[AssetInDB],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new asset",
    description="Creates a new asset with the provided details.",
    dependencies=[Depends(has_permission([Permission.ASSET_CREATE]))]
)
async def create_asset(
    asset_data: AssetCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[AssetInDB]:
    """Endpoint to create new assets."""
    controller = AssetController(db_service)
    return await controller.create_asset(asset_data, current_user.email)


@router.get(
    "/{asset_id}",
    response_model=List[AssetInDB],
    status_code=status.HTTP_200_OK,
    summary="Retrieve an asset by ID",
    description="Retrieves the details of a single asset using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ASSET_READ]))]
)
async def get_asset_by_id(
    asset_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[AssetInDB]:
    """Endpoint to retrieve an asset by its ID."""
    controller = AssetController(db_service)
    return await controller.get_all_assets(asset_id=asset_id, page_number=1, page_size=1)


@router.get(
    "/organization/{org_id}",
    response_model=AssetResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all assets by organization.",
    description="Retrieves a list of all assets by organization currently in the system.",
)
async def get_all_assets_by_org_id(
    org_id: int,
    page_number: Optional[int] = None,
    page_size: Optional[int] = None,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssetResponse:
    """Endpoint to retrieve all assets by organization."""
    controller = AssetController(db_service)
    return await controller.get_all_assets(
        org_id=org_id,
        page_number=page_number,
        page_size=page_size,
        count_required=True
    )


@router.get(
    "/organization/{org_id}/asset-type/{asset_type_id}",
    response_model=AssetResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all assets",
    description="Retrieves a list of all assets currently in the system.",
    dependencies=[Depends(has_permission([Permission.ASSET_READ]))]
)
async def get_all_assets_by_org_id_asset_type_id(
    org_id: int,
    asset_type_id: int,
    current_user: CurrentUserDep,
    page_number: Optional[int] = None,
    page_size: Optional[int] = None,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssetResponse:
    """Endpoint to retrieve all assets by organization by asset type ID."""
    controller = AssetController(db_service)
    return await controller.get_all_assets(
        org_id=org_id,
        asset_type_id=asset_type_id,
        page_number=page_number,
        page_size=page_size,
        count_required=True
    )


@router.patch(
    "/{asset_id}",
    response_model=List[AssetInDB],
    status_code=status.HTTP_200_OK,
    summary="Update an existing asset",
    description="Updates the details of an existing asset using its unique ID. Partial updates are supported.",
    dependencies=[Depends(has_permission([Permission.ASSET_UPDATE]))]
)
async def update_asset(
    asset_id: int,
    asset_data: AssetUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[AssetInDB]:
    """Endpoint to update an existing asset."""
    controller = AssetController(db_service)
    return await controller.update_asset(asset_id, asset_data, current_user.email)


@router.delete(
    "/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an asset",
    description="Deletes an asset from the system using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ASSET_DELETE]))]
)
async def delete_asset(
    asset_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    """Endpoint to delete an asset."""
    controller = AssetController(db_service)
    await controller.delete_asset(asset_id, current_user.email)
    return None
