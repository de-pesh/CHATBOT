from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
import logging

from app.controllers.file_controller import FileController
from app.controllers.assessment_controller import AssessmentController
from app.core.stored_procedures import StoredProcedures
from app.dependencies import get_db_service
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

router = APIRouter()


class AnalysisByControlItem(BaseModel):
    control_label: str = Field(..., description="Root control label identifier")
    node_id: str | None = Field(default=None, description="Root control node identifier")


class AnalysisReadResponse(BaseModel):
    analysis_id: int = Field(..., description="Assessment version ID used as analysis ID")
    analysis_by_control: list[AnalysisByControlItem] = Field(default_factory=list)


@router.get(
    "/{analysis_id}",
    response_model=AnalysisReadResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve analysis configuration for worker processing",
)
async def get_analysis_by_id(
    analysis_id: int,
    db_service: DatabaseService = Depends(get_db_service),
):
    results = await db_service.call_procedure(
        StoredProcedures.FILTER_RESULTS_BY_LABEL,
        [None, analysis_id, None, None, None, True, 1, 5000],
    )

    rows = results[0] if results and results[0] else []
    logger.info(f"GET /analysis/{analysis_id}: Found {len(rows)} rows from FILTER_RESULTS_BY_LABEL")
    if rows:
        logger.info(f"GET /analysis/{analysis_id} first row keys: {list(rows[0].keys()) if rows[0] else 'empty dict'}")
        logger.info(f"GET /analysis/{analysis_id} first row: {rows[0]}")
    
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Analysis {analysis_id} not found.",
        )

    seen_control_labels: set[str] = set()
    analysis_by_control: list[AnalysisByControlItem] = []
    for row in rows:
        control_label = row.get("label")
        if control_label is None:
            logger.debug(f"Skipping row with no label_id: {row.keys()}")
            continue
        control_label_str = str(control_label)
        if control_label_str in seen_control_labels:
            continue
        seen_control_labels.add(control_label_str)
        analysis_by_control.append(
            AnalysisByControlItem(
                control_label=control_label_str,
                node_id=str(row.get("node_id")) if row.get("node_id") is not None else None,
            )
        )

    logger.info(f"GET /analysis/{analysis_id}: Returning {len(analysis_by_control)} unique control labels")
    return AnalysisReadResponse(
        analysis_id=analysis_id,
        analysis_by_control=analysis_by_control,
    )


@router.get(
    "/assessment-snapshot/{assessment_id}",
    status_code=status.HTTP_200_OK,
    summary="Internal: full assessment snapshot (comprehensive details + run results)",
    description=(
        "Unauthenticated, service-to-service endpoint used by the assessment "
        "knowledge-base worker to build the chatbot's per-assessment vector store. "
        "Returns the comprehensive assessment (overview, frameworks, controls, "
        "evidence mappings) together with the structured run results."
    ),
)
async def get_assessment_snapshot(
    assessment_id: int,
    version_number: int | None = None,
    db_service: DatabaseService = Depends(get_db_service),
):
    controller = AssessmentController(db_service)

    comprehensive = await controller.get_comprehensive_assessment_by_id(assessment_id)
    assessment_payload = comprehensive.model_dump(mode="json")

    results_payload = None
    try:
        results = await controller.fetch_assessment_results(assessment_id, version_number)
        results_payload = results.model_dump(mode="json")
    except HTTPException as exc:
        # Results may not exist yet (e.g. pre-run snapshot); that is not an error here.
        logger.info(
            f"No run results available for assessment {assessment_id}: {exc.detail}"
        )
    except Exception as exc:  # noqa: BLE001 - snapshot must not fail on optional results
        logger.warning(
            f"Could not fetch results for assessment {assessment_id}: {exc}", exc_info=True
        )

    return {"assessment": assessment_payload, "results": results_payload}


@router.get(
    "/assessments-by-framework/{framework_id}",
    status_code=status.HTTP_200_OK,
    summary="Internal: list assessments that use a given framework",
    description=(
        "Unauthenticated, service-to-service endpoint used by the chatbot to "
        "discover peer assessments that share a framework, enabling cross-"
        "assessment answers. Returns a list of {assessment_id, name, framework_id}."
    ),
)
async def get_assessments_by_framework(
    framework_id: int,
    db_service: DatabaseService = Depends(get_db_service),
):
    controller = AssessmentController(db_service)
    return await controller.get_assessments_by_framework(framework_id)


class ParsedPathUpdatePayload(BaseModel):
    parsed_file_path: str = Field(..., description="Path of the parsed text blob")


@router.patch(
    "/{analysis_id}/controls/{control_label}/files/{file_id}/parsed-path",
    status_code=status.HTTP_200_OK,
    summary="Update parsed file path for analysis worker callback",
)
async def update_parsed_file_path(
    analysis_id: int,
    control_label: str,
    file_id: int,
    payload: ParsedPathUpdatePayload,
    db_service: DatabaseService = Depends(get_db_service),
):
    controller = FileController(db_service)
    updated_file = await controller.update_file_parsed_path(file_id, payload.parsed_file_path, user_id="SYSTEM")
    return {
        "message": "Parsed file path updated successfully",
        "analysis_id": analysis_id,
        "control_label": control_label,
        "file_id": file_id,
        "parsed_file_path": updated_file.parsed_path,
    }