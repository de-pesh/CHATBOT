from typing import List, Optional

from fastapi import APIRouter, Depends, status, Query, HTTPException

from app.controllers.framework_controller import FrameworkController
from app.controllers.framework_tree_controller import FrameworkTreeController

from app.dependencies import get_db_service , CurrentUserDep, has_permission
from app.models.framework import FrameworkCreate, FrameworkResponse, FrameworkUpdate, FrameworkInDB
from app.models.framework import FrameworkTreeResponse
from app.models.files import VersionResponse
from app.models.files import FileResponse
from app.services.database_service import DatabaseService
from app.core.permissions import Permission

router = APIRouter()

@router.post(
    "/",
    response_model=FrameworkInDB,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new framework",
    description="Creates a new framework with the provided details.",
    dependencies=[Depends(has_permission([Permission.FRAMEWORK_CREATE]))]
)
async def create_framework(
    framework_data: FrameworkCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> FrameworkInDB:
    """Endpoint to create a new framework."""
    controller = FrameworkController(db_service)
    return await controller.create_framework(framework_data, user_id=current_user.email)


@router.get(
    "/{framework_id}",
    response_model=FrameworkInDB,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a framework by ID",
    description="Retrieves the details of a single framework using its unique ID.",
    dependencies=[Depends(has_permission([Permission.FRAMEWORK_READ]))]
)
async def get_framework_by_id(
    framework_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> FrameworkInDB:
    """Endpoint to retrieve a framework by its ID."""
    controller = FrameworkController(db_service)
    response = await controller.get_frameworks(framework_id=framework_id, page_number=1, page_size=1)
    return response[0]


@router.get(
    "/",
    response_model=FrameworkResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all frameworks",
    description="Retrieves a list of all frameworks currently in the system with pagination.",
    dependencies=[Depends(has_permission([Permission.FRAMEWORK_READ]))]
)
async def get_all_frameworks(
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(None, ge=1, description="Page number to retrieve"),
    page_size: int = Query(None, ge=1, le=100, description="Number of frameworks per page")
) -> FrameworkResponse:
    """Endpoint to retrieve all frameworks with pagination."""
    controller = FrameworkController(db_service)
    return await controller.get_frameworks_by_organization(email=current_user.email, page_number=page_number, page_size=page_size, count_required=True)


@router.patch(
    "/{framework_id}",
    response_model=FrameworkInDB,
    status_code=status.HTTP_200_OK,
    summary="Update an existing framework",
    description="Updates the details of an existing framework using its unique ID. Partial updates are supported.",
    dependencies=[Depends(has_permission([Permission.FRAMEWORK_UPDATE]))]
)
async def update_framework(
    framework_id: int,
    framework_data: FrameworkUpdate,
    current_user: CurrentUserDep, # Assuming a mock user for now
    db_service: DatabaseService = Depends(get_db_service)
) -> FrameworkInDB:
    """Endpoint to update an existing framework."""
    controller = FrameworkController(db_service)
    return await controller.update_framework(framework_id, framework_data, user_id=current_user.email)


@router.delete(
    "/{framework_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a framework",
    description="Deletes a framework from the system by marking it as inactive.",
    dependencies=[Depends(has_permission([Permission.FRAMEWORK_DELETE]))]
)
async def delete_framework(
    framework_id: int,
    current_user: CurrentUserDep, # Assuming a mock user for now
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    """Endpoint to delete a framework."""
    controller = FrameworkController(db_service)
    await controller.delete_framework(framework_id, user_id=current_user.email)
    return None


@router.get(
    "/{framework_id}/files",
    response_model=List[FileResponse],
    status_code=status.HTTP_200_OK,
    summary="Retrieve all files for framework",
    description="Retrieves a list of all files for the framework currently in the system."
)
async def get_files_by_framework_id(
    framework_id: int,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[FileResponse]:
    """Endpoint to retrieve all files by framework ID."""
    controller = FrameworkController(db_service)
    return await controller.get_files_by_framework_id(framework_id=framework_id)


@router.get(
    "/{framework_id}/versions",
    response_model=VersionResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all versions for framework",
    description="Retrieves a list of all versions for the framework currently in the system with pagination."
)
async def get_versions_by_framework_id(
    framework_id: int,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(None, ge=1, description="Page number to retrieve"),
    page_size: int = Query(None, ge=1, le=100, description="Number of frameworks per page")
) -> VersionResponse:
    """Endpoint to retrieve all versions by framework ID."""
    controller = FrameworkController(db_service)
    return await controller.get_versions_by_framework_id(framework_id=framework_id, page_number=page_number, page_size=page_size, count_required=True)


@router.get(
    "/{framework_id}/version/active/tree",
    response_model=List[FrameworkTreeResponse],
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
    summary="Retrieve version tree for framework (latest active version)",
    description="Retrieves the version tree for the latest active version of the framework currently in the system with level."
)
async def get_active_version_tree_by_framework_id(
    framework_id: int,
    db_service: DatabaseService = Depends(get_db_service),
    start_node_id: Optional[str] = Query(None, description="Start node ID to retrieve"),
    level: Optional[int] = Query(None, ge=1, description="Depth level for tree retrieval")
) -> List[FrameworkTreeResponse]:
    """Endpoint to retrieve version tree for the latest active version by framework ID."""
    from app.core.stored_procedures import StoredProcedures
    
    # Get the latest active version for the framework
    versions = await db_service.call_procedure(
        StoredProcedures.FILTER_VERSIONS,
        [None, framework_id, None, True, True, 1, 1]
    )
    if not versions or not versions[0]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active versions found for this framework.")
    
    version_id = versions[0][0]['id']
    
    controller = FrameworkTreeController(db_service)
    return await controller.get_framework_tree(framework_id=framework_id, version_id=version_id, start_node_id=start_node_id, level=level)


@router.get(
    "/{framework_id}/version/{version_id}/tree",
    response_model=List[FrameworkTreeResponse],
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
    summary="Retrieve version tree for framework",
    description="Retrieves a version tree for the framework currently in the system with level."
)
async def get_version_tree_by_framework_id(
    framework_id: int,
    version_id: int,
    db_service: DatabaseService = Depends(get_db_service),
    start_node_id: Optional[str] = Query(None, description="Page number to retrieve"),
    level: Optional[int] = Query(None, ge=1, description="Number of frameworks per page")
) -> List[FrameworkTreeResponse]:
    """Endpoint to retrieve all version tree by framework ID."""
    controller = FrameworkTreeController(db_service)
    return await controller.get_framework_tree(framework_id=framework_id, version_id=version_id, start_node_id=start_node_id, level=level)


@router.patch(
    "/{framework_id}/version/{version_id}/in-use",
    response_model=List[VersionResponse],
    status_code=status.HTTP_200_OK,
    summary="Update the version in the framework to be in use.",
    description="Updates the version in the framework to be in use."
)
async def framework_version_in_use(
    current_user: CurrentUserDep,
    framework_id: int,
    version_id: int,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[VersionResponse]:
    """Endpoint to update in use version for a framework."""
    controller = FrameworkController(db_service)
    return await controller.framework_version_in_use(framework_id=framework_id, version_id=version_id, user_id=current_user.email)
