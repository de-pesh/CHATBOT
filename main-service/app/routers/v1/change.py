from typing import List

from app.services.redis_service import RedisQueueService
from fastapi import APIRouter, Depends, status

from app.config import Settings, get_settings

from app.controllers.change_controller import ChangeController
from app.dependencies import CurrentUserDep, get_db_service, get_llm_queue, has_permission
from app.models.change import ChangeCreatePayload, ChangeDetailResponse, ChangeResponse, ChangeUpdate, NodeDetailsInDB
from app.services.database_service import DatabaseService
from app.core.permissions import Permission

router = APIRouter()


@router.post(
    "/",
    response_model=ChangeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new change to a version",
    description="Adds a new change to an existing version.",
    dependencies=[Depends(has_permission([Permission.CHANGE_CREATE]))]
)
async def add_change_to_request(
    change_data: ChangeCreatePayload,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> ChangeResponse:
    """Endpoint to add a new change to a change request."""
    controller = ChangeController(db_service)
    return await controller.add_change_to_change_request(change_data, current_user.email)


@router.get(
    "/version/{version_id}",
    response_model=List[ChangeResponse],
    status_code=status.HTTP_200_OK,
    summary="Retrieve changes in version",
    description="Retrieves changes of a version using its unique ID.",
    dependencies=[Depends(has_permission([Permission.CHANGE_READ]))]
)
async def get_changes_by_version_id(
    version_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[ChangeController]:
    """Endpoint to retrieve a change request by its ID."""
    controller = ChangeController(db_service)
    return await controller.get_changes_by_version_id(version_id)


@router.patch(
    "/{change_id}",
    response_model=ChangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an existing change",
    description="Updates the details of an existing change using its unique ID. Partial updates are supported.",
    dependencies=[Depends(has_permission([Permission.CHANGE_UPDATE]))]
)
async def update_change(
    change_id: int,
    change_data: ChangeUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    llm_queue: RedisQueueService = Depends(get_llm_queue),
    settings: Settings = Depends(get_settings)
) -> ChangeResponse:
    """Endpoint to update an existing change."""
    controller = ChangeController(db_service, llm_queue)
    return await controller.update_change(change_id, change_data, current_user.email, settings)


@router.get(
    "/{change_id}",
    response_model=ChangeDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve change details by change ID",
    description="Retrieves change details of the change using its unique ID.",
    dependencies=[Depends(has_permission([Permission.CHANGE_READ]))]
)
async def get_change_details_by_id(
    change_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> ChangeDetailResponse:
    """Endpoint to retrieve the change details by its ID."""
    controller = ChangeController(db_service)
    return await controller.get_change_details_by_id(change_id)


@router.get(
    "/node/{node_id}",
    response_model=NodeDetailsInDB,
    status_code=status.HTTP_200_OK,
    summary="Retrieve node details by ID",
    description="Retrieves details of a node using its unique ID.",
    dependencies=[Depends(has_permission([Permission.CHANGE_READ]))]
)
async def get_node_details_by_id(
    node_id: str,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> NodeDetailsInDB:
    """Endpoint to retrieve the change details by its ID."""
    controller = ChangeController(db_service)
    return await controller.get_node_details_by_id(node_id)


@router.get(
    "/label/{label_id}",
    response_model=List[NodeDetailsInDB],
    status_code=status.HTTP_200_OK,
    summary="Retrieve items by label ID",
    description="Retrieves list of items for a specific label id.",
    dependencies=[Depends(has_permission([Permission.CHANGE_READ]))]
)
async def get_parent_label_items_by_id(
    label_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[NodeDetailsInDB]:
    """Endpoint to retrieve a list of items for the parent of a specific label ID."""
    controller = ChangeController(db_service)
    return await controller.get_parent_label_items_by_id(label_id)
