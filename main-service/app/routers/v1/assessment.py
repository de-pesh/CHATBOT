from typing import List, Optional

from fastapi import APIRouter, Depends, status, Query

from app.controllers.assessment_controller import AssessmentController
from app.dependencies import get_db_service, get_parser_queue, get_assessment_kb_queue, CurrentUserDep, has_permission
from app.models.assessment import(
    AssessmentCreate,
    AssessmentListResponse,
    AssessmentResponse,
    AssessmentUpdate,
    AssessmentFrameworkAdd,
    AssessmentFrameworkBulkAdd,
    AssessmentFrameworkUpdate,
    AssessmentControlUpdate,
    ControlWithTagsResponse,
    ComprehensiveAssessmentResponse,
    AssessmentEvidenceResponse,
    AssessmentLLMDetailsResponse,
    RunAssessmentResponse,
    SubmitAssessmentResponse,
    AssessmentResultsResponse,
    )
from app.services.database_service import DatabaseService
from app.services.redis_service import RedisQueueService
from app.core.permissions import Permission


router = APIRouter()


@router.post(
    "/",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new assessment",
    description="Creates a new assessment with the provided details.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_CREATE]))]
)
async def create_assessment(
    assessment_data: AssessmentCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    kb_queue: RedisQueueService = Depends(get_assessment_kb_queue)
) -> AssessmentResponse:
    """Endpoint to create a new assessment."""
    controller = AssessmentController(db_service, kb_queue_service=kb_queue)
    return await controller.create_assessment(assessment_data, user_id=current_user.email)


@router.get(
    "/{assessment_id}",
    response_model=AssessmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve an assessment by ID",
    description="Retrieves the details of a single assessment using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def get_assessment_by_id(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentResponse:
    """Endpoint to retrieve an assessment by its ID."""
    controller = AssessmentController(db_service)
    return await controller.get_assessment_by_id(assessment_id)


@router.get(
    "/",
    response_model=AssessmentListResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all assessments",
    description="Retrieves a list of all assessments currently in the system with pagination.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def get_all_assessments(
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(1, ge=1, description="Page number to retrieve"),
    page_size: int = Query(10, ge=1, le=100, description="Number of assessments per page")
) -> AssessmentListResponse:
    """Endpoint to retrieve all assessments with pagination."""
    controller = AssessmentController(db_service)
    return await controller.get_all_assessments(
        page_number=page_number, 
        page_size=page_size,
        user=current_user.email
    )


@router.patch(
    "/{assessment_id}",
    response_model=AssessmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an existing assessment",
    description="Updates the details of an existing assessment using its unique ID. Partial updates are supported.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def update_assessment(
    assessment_id: int,
    assessment_data: AssessmentUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentResponse:
    """Endpoint to update an existing assessment."""
    controller = AssessmentController(db_service)
    return await controller.update_assessment(assessment_id, assessment_data, user_id=current_user.email)


@router.patch(
    "/{assessment_id}/framework",
    response_model=AssessmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update framework(s) mapped to an assessment",
    description="Replace framework mapping(s) on an assessment. Use replacements array with 1 item for single replacement or multiple items for bulk replacement.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def update_assessment_framework(
    assessment_id: int,
    framework_data: AssessmentFrameworkUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentResponse:
    controller = AssessmentController(db_service)
    return await controller.update_assessment_framework(assessment_id, framework_data, user_id=current_user.email)


@router.post(
    "/{assessment_id}/framework",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a framework to an assessment",
    description="Maps a new framework to an assessment.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def add_assessment_framework(
    assessment_id: int,
    framework_data: AssessmentFrameworkAdd,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentResponse:
    controller = AssessmentController(db_service)
    return await controller.add_assessment_framework(assessment_id, framework_data, user_id=current_user.email)


@router.post(
    "/{assessment_id}/frameworks/bulk",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bulk add frameworks to an assessment",
    description="Maps multiple frameworks to an assessment in a single call. Skips frameworks already mapped.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def bulk_add_assessment_frameworks(
    assessment_id: int,
    payload: AssessmentFrameworkBulkAdd,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentResponse:
    controller = AssessmentController(db_service)
    return await controller.bulk_add_assessment_frameworks(
        assessment_id, payload.framework_ids, user_id=current_user.email
    )


@router.delete(
    "/{assessment_id}/framework/{framework_id}",
    response_model=AssessmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a framework from an assessment",
    description="Removes a framework mapping from an assessment.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def delete_assessment_framework(
    assessment_id: int,
    framework_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentResponse:
    controller = AssessmentController(db_service)
    return await controller.delete_assessment_framework(assessment_id, framework_id, user_id=current_user.email)


@router.patch(
    "/{assessment_id}/controls",
    response_model=AssessmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update control(s) mapped to an assessment",
    description="Replace control mapping(s) on an assessment. Use replacements array with 1 item for single replacement or multiple items for bulk replacement.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def update_assessment_controls(
    assessment_id: int,
    control_data: AssessmentControlUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentResponse:
    controller = AssessmentController(db_service)
    return await controller.update_assessment_controls(assessment_id, control_data, user_id=current_user.email)


@router.delete(
    "/{assessment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an assessment",
    description="Deletes an assessment from the system by marking it as inactive.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_DELETE]))]
)
async def delete_assessment(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    """Endpoint to delete an assessment."""
    controller = AssessmentController(db_service)
    await controller.delete_assessment(assessment_id, user_id=current_user.email)
    return None


@router.get(
    "/{assessment_id}/controls",
    response_model=List[ControlWithTagsResponse],
    status_code=status.HTTP_200_OK,
    summary="Retrieve all controls and their tags for an assessment",
    description="Fetches a complete list of all controls within an assessment, along with all associated asset, org unit, and people tags for each control.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def get_controls_with_tags_for_assessment(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[ControlWithTagsResponse]:
    """Endpoint to retrieve all controls and their tags for an assessment."""
    controller = AssessmentController(db_service)
    return await controller.get_controls_with_tags_for_assessment(assessment_id)


@router.get(
    "/{assessment_id}/all",
    response_model=ComprehensiveAssessmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a comprehensive view of an assessment by ID",
    description="Retrieves the details of a single assessment, including all its linked frameworks, controls, and tags.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def get_comprehensive_assessment_by_id(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> ComprehensiveAssessmentResponse:
    """Endpoint to retrieve a comprehensive view of an assessment by its ID."""
    controller = AssessmentController(db_service)
    return await controller.get_comprehensive_assessment_by_id(assessment_id)


@router.get(
    "/{assessment_id}/evidence",
    response_model=AssessmentEvidenceResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve uploaded and mapped evidence for an assessment",
    description=(
        "Returns the list of evidence files uploaded for the assessment and "
        "the evidence mapped under each framework and control."
    ),
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def get_assessment_evidence_by_id(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentEvidenceResponse:
    """Endpoint to retrieve uploaded and mapped evidence details by assessment ID."""
    controller = AssessmentController(db_service)
    return await controller.get_assessment_evidence_by_id(assessment_id)


@router.post(
    "/{assessment_id}/run",
    response_model=RunAssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run an assessment",
    description="Runs a new assessment",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_CREATE]))]
)
async def run_assessment(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    parser_queue: RedisQueueService = Depends(get_parser_queue),
    kb_queue: RedisQueueService = Depends(get_assessment_kb_queue)
) -> RunAssessmentResponse:
    """Endpoint to run an existing assessment by ID."""
    controller = AssessmentController(db_service, parser_queue, kb_queue_service=kb_queue)
    return await controller.run_assessment(assessment_id, user_id=current_user.email)


@router.post(
    "/{assessment_id}/submit",
    response_model=SubmitAssessmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit assessment run for reviewer cycle",
    description="Assessment creator explicitly submits completed run results for post-run reviewer cycle.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def submit_assessment_for_review(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    kb_queue: RedisQueueService = Depends(get_assessment_kb_queue)
) -> SubmitAssessmentResponse:
    controller = AssessmentController(db_service, kb_queue_service=kb_queue)
    return await controller.submit_assessment_for_review(assessment_id, user_id=current_user.email)

@router.get(
    "/fetch_assessment_details/{assessment_id}",
    response_model=AssessmentLLMDetailsResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch assessment details and LLM results by ID",
    description=(
        "Retrieves the core assessment record and the latest run's LLM-backed "
        "results (as saved by the LLM service) for the given assessment ID."
    ),
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def fetch_assessment_details_by_id(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentLLMDetailsResponse:
    """Endpoint to retrieve assessment details plus latest LLM-run results by assessment ID."""
    controller = AssessmentController(db_service)
    return await controller.fetch_assessment_details_by_id(assessment_id)

@router.get(
    "/fetch_version_details/{assessment_id}/{version_number}",
    response_model=AssessmentLLMDetailsResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch assessment version details",
    description=(
        "Retrieves the details of a specific version of an assessment"
    ),
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)

async def fetch_assessment_version_details(
    assessment_id: int,
    version_number: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentLLMDetailsResponse:
    """Endpoint to retrieve details of a specific version of an assessment."""
    controller = AssessmentController(db_service)
    return await controller.fetch_assessment_version_details(assessment_id, version_number)


@router.get(
    "/fetch_results/{assessment_id}",
    response_model=AssessmentResultsResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch assessment results in structured format",
    description="Returns selected control nodes with their leaf criteria children, split into completed / failed / in_progress.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def fetch_assessment_results(
    assessment_id: int,
    version_number: Optional[int] = Query(None, description="Specific version number; omit for latest"),
    current_user: CurrentUserDep = None,
    db_service: DatabaseService = Depends(get_db_service),
) -> AssessmentResultsResponse:
    """Fetch structured assessment results with control-criteria hierarchy."""
    controller = AssessmentController(db_service)
    return await controller.fetch_assessment_results(assessment_id, version_number)