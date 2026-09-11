import logging
from fastapi import HTTPException, status
from app.dependencies import DatabaseService
from app.core.stored_procedures import StoredProcedures
from app.models.assessment import AssessmentLabelCreate, AssessmentLabelInDB, AssessmentLabelRead
from app.controllers.framework_controller import FrameworkController

logger = logging.getLogger(__name__)

class AssessmentLabelController:
    """
    Controller for managing assessment_label-related operations.
    """
    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    
    async def _get_assessment_label_by_id(self, assessment_label_id: int) -> AssessmentLabelInDB:
        result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_LABELS,
            [
                assessment_label_id, # assessment_label_id
                None, # framework_id
                None, # assessment_id
                None, # version_id
                None, # is_active
                1, # page_number
                1 # page_size
            ]
        )
        if not result or not result[0]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment label record not found.")
        return AssessmentLabelInDB(**result[0][0])


    async def add_assessment_label(self, assessment_label_data: AssessmentLabelCreate, user_id: str) -> AssessmentLabelInDB:
        """
        Adds a framework to an assessment.

        Args:
            assessment_label_data (AssessmentLabelCreate): The data for the new assessment label.
            user_id (str): The ID of the user adding the assessment label.

        Returns:
            AssessmentLabelInDB: The created assessment's data.

        Raises:
            HTTPException: If the assessment cannot be created.
        """
        try:
            assessment_id = assessment_label_data.assessment_id
            framework_id = assessment_label_data.framework_id
            # Check for latest active version of framework
            version_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_VERSIONS,
                [
                    None, # id
                    framework_id, # framework_id
                    None, # number
                    True, # in_use
                    True, # is_active
                    1, # page_number
                    1 # page_size
                ]
            )
            if not version_result or not version_result[0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No active version exists.")
            version_id = version_result[0][0]['id']
            result = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT_LABEL,
                [
                    None, # id
                    assessment_id, # assessment_id
                    framework_id, # framework_id
                    version_id, # version_id
                    True, # is_active
                    user_id # user_id
                ]
            )
            if not result or not result[0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add framework to assessment.")
            return await self._get_assessment_label_by_id(result[0][0]['id'])
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating assessment: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while creating the assessment label: {e}")


    async def get_assessment_label(self, assessment_label_id: int) -> AssessmentLabelRead:
        """
        Fetches the framework details for an assessment.

        Args:
            assessment_label_id (int): The unique identifier of the assesment label to be deleted.

        Returns: AssessmentLabelRead

        Raises:
            HTTPException: If the assessment label is not found or cannot be deleted.
        """
        assessment_label_data = await self._get_assessment_label_by_id(assessment_label_id)
        assessment_id = assessment_label_data.assessment_id
        framework_id = assessment_label_data.framework_id
        version_id = assessment_label_data.version_id       
        # get framework name and description
        framework_controller = FrameworkController(self.db_service)
        framework_details = await framework_controller.get_frameworks(
            framework_id=framework_id,
            page_number=1,
            page_size=1
        )
        # get version number
        version_details = await self.db_service.call_procedure(
                StoredProcedures.FILTER_VERSIONS,
                [version_id, framework_id, None, True, True, 1, 1]
            )
        if not version_details or not version_details[0]:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No versions exists.")
        version_number = version_details[0][0]['number']
        response_data = assessment_label_data.model_dump()
        #response_data['framework_name'] = framework_details.name
        #response_data['framework_description'] = framework_details.description

                
        if not framework_details:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Framework not found.")

        framework = framework_details[0]
        response_data['framework_name'] = framework.name
        response_data['framework_description'] = framework.description


        response_data['version_number'] = version_number
        return AssessmentLabelRead(**response_data)
        


    async def delete_assessment_label(self, assessment_label_id: int, user_id: str) -> None:
        """
        Deletes a framework from an assessment.

        Args:
            assessment_label_id (int): The unique identifier of the assesment label to be deleted.
            user_id (str): The ID of the user adding the assessment label.

        Returns: None

        Raises:
            HTTPException: If the assessment label is not found or cannot be deleted.
        """
        try:
            await self._get_assessment_label_by_id(assessment_label_id)
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ASSESSMENT_LABEL,
                [assessment_label_id, user_id]
            )
            return None
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting assessment label {assessment_label_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
