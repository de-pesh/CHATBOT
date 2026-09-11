import logging
from typing import List, Dict

from fastapi import HTTPException, status

from app.core.stored_procedures import StoredProcedures
from app.models.org import (
    OrgGateSettingsCreate,
    OrgGateSettingsUpdate,
    OrgGateSettingsInDB,
    OrgAndGateSettingsUpsertRequest,
    OrgAndGateSettingsUpsertResponse,
)
from app.models.org import OrgSettingsCreate
from app.services.database_service import DatabaseService

from app.controllers.org_settings_controller import OrgSettingsController

logger = logging.getLogger(__name__)


class OrgGateSettingsController:
    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    @staticmethod
    def _normalize_gate_setting_row(raw_gate_setting: dict) -> dict:
        row = dict(raw_gate_setting)
        # Backward/forward compatibility: DB/SP may return either key.
        if "org_settings_id" not in row and "org_setting_id" in row:
            row["org_settings_id"] = row["org_setting_id"]
        return row

    @staticmethod
    def _normalize_email_list(emails: List[str]) -> List[str]:
        normalized_emails = []
        seen = set()
        for email in emails:
            normalized_email = str(email).strip().lower()
            if not normalized_email or normalized_email in seen:
                continue
            seen.add(normalized_email)
            normalized_emails.append(normalized_email)
        return normalized_emails

    async def _validate_fixed_actioner_payload(
        self,
        is_fixed_reviewer: bool,
        reviewer_emails: List[str],
        is_fixed_approver: bool,
        approver_emails: List[str],
    ) -> Dict[str, List[str]]:
        normalized_reviewers = self._normalize_email_list(reviewer_emails)
        normalized_approvers = self._normalize_email_list(approver_emails)

        if is_fixed_reviewer and not normalized_reviewers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="reviewer_emails is required when is_fixed_reviewer is true.",
            )
        if not is_fixed_reviewer and normalized_reviewers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="reviewer_emails can only be provided when is_fixed_reviewer is true.",
            )

        if is_fixed_approver and not normalized_approvers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="approver_emails is required when is_fixed_approver is true.",
            )
        if not is_fixed_approver and normalized_approvers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="approver_emails can only be provided when is_fixed_approver is true.",
            )

        return {
            "reviewer_emails": normalized_reviewers,
            "approver_emails": normalized_approvers,
        }

    async def _get_fixed_actioner_rows(self, org_settings_id: int) -> List[dict]:
        results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ORG_ACTIONER,
            [None, None, org_settings_id, None, None, True, 1, 1000],
        )

        if not results or not results[0]:
            return []

        filtered_rows = []
        for row in results[0]:
            row_org_settings_id = row.get("org_settings_id", row.get("org_setting_id"))
            if row_org_settings_id == org_settings_id:
                filtered_rows.append(row)
        return filtered_rows

    async def _get_org_gate_setting_rows_by_org_settings_id(self, org_settings_id: int) -> List[dict]:
        results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ORG_GATE_SETTINGS,
            [None, org_settings_id, None, True, 1, 1000],
        )
        if not results or not results[0]:
            return []
        return [self._normalize_gate_setting_row(row) for row in results[0]]

    async def _create_fixed_actioners(
        self,
        org_settings_id: int,
        reviewer_emails: List[str],
        approver_emails: List[str],
        user_id: str,
    ) -> None:
        for email in reviewer_emails:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_ACTIONER,
                [
                    None,
                    org_settings_id,
                    email,
                    True,
                    False,
                    None,
                    True,
                    user_id,
                ],
            )

        for email in approver_emails:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_ACTIONER,
                [
                    None,
                    org_settings_id,
                    email,
                    False,
                    True,
                    None,
                    True,
                    user_id,
                ],
            )

    async def _replace_fixed_actioners(
        self,
        org_settings_id: int,
        reviewer_emails: List[str],
        approver_emails: List[str],
        user_id: str,
    ) -> None:
        existing_actioners = await self._get_fixed_actioner_rows(org_settings_id)

        for actioner in existing_actioners:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_ACTIONER,
                [
                    actioner.get("id"),
                    org_settings_id,
                    actioner.get("email"),
                    actioner.get("is_reviewer"),
                    actioner.get("is_approver"),
                    None,
                    False,
                    user_id,
                ],
            )

        if reviewer_emails or approver_emails:
            await self._create_fixed_actioners(
                org_settings_id,
                reviewer_emails,
                approver_emails,
                user_id,
            )

    async def _get_fixed_actioner_emails(self, org_settings_id: int) -> Dict[str, List[str]]:
        try:
            rows = await self._get_fixed_actioner_rows(org_settings_id)

            reviewer_emails: List[str] = []
            approver_emails: List[str] = []
            seen_reviewers = set()
            seen_approvers = set()

            for row in rows:
                email = str(row.get("email") or "").strip().lower()
                if not email:
                    continue

                if row.get("is_reviewer") and email not in seen_reviewers:
                    seen_reviewers.add(email)
                    reviewer_emails.append(email)

                if row.get("is_approver") and email not in seen_approvers:
                    seen_approvers.add(email)
                    approver_emails.append(email)

            return {
                "reviewer_emails": reviewer_emails,
                "approver_emails": approver_emails,
            }
        except Exception as e:
            logger.exception(f"Error retrieving fixed actioner emails for org_settings_id={org_settings_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def _hydrate_gate_setting_response(self, raw_gate_setting: dict) -> OrgGateSettingsInDB:
        normalized_gate_setting = self._normalize_gate_setting_row(raw_gate_setting)
        fixed_actioners = await self._get_fixed_actioner_emails(normalized_gate_setting["org_settings_id"])
        return OrgGateSettingsInDB(
            **normalized_gate_setting,
            reviewer_emails=fixed_actioners["reviewer_emails"],
            approver_emails=fixed_actioners["approver_emails"],
        )

    async def create_org_gate_settings(self, org_gate_settings_data: OrgGateSettingsCreate, user_id: str) -> OrgGateSettingsInDB:
        org_controller = OrgSettingsController(self.db_service)
        await org_controller.get_org_settings_by_id(org_gate_settings_data.org_settings_id)

        existing_settings = await self._get_org_gate_setting_rows_by_org_settings_id(org_gate_settings_data.org_settings_id)
        if existing_settings:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Gate settings for this organization setting already exist.")

        fixed_actioners = await self._validate_fixed_actioner_payload(
            org_gate_settings_data.is_fixed_reviewer,
            org_gate_settings_data.reviewer_emails,
            org_gate_settings_data.is_fixed_approver,
            org_gate_settings_data.approver_emails,
        )

        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_GATE_SETTING,
                [
                    None,
                    org_gate_settings_data.org_settings_id,
                    org_gate_settings_data.reviewer_count,
                    org_gate_settings_data.approver_count,
                    org_gate_settings_data.is_fixed_reviewer,
                    org_gate_settings_data.is_fixed_approver,
                    True,
                    user_id,
                ],
            )
            if not results or not results[0] or "id" not in results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create organization gate settings.")

            if fixed_actioners["reviewer_emails"] or fixed_actioners["approver_emails"]:
                await self._create_fixed_actioners(
                    org_gate_settings_data.org_settings_id,
                    fixed_actioners["reviewer_emails"],
                    fixed_actioners["approver_emails"],
                    user_id,
                )

            return await self.get_org_gate_settings_by_id(results[0][0]["id"])
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating organization gate settings: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def get_org_gate_settings_by_id(self, org_gate_settings_id: int) -> OrgGateSettingsInDB:
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_GATE_SETTINGS,
                [org_gate_settings_id, None, None, True, 1, 1],
            )
            if not results or not results[0]:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization gate settings not found.")

            return await self._hydrate_gate_setting_response(results[0][0])

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error retrieving organization gate settings {org_gate_settings_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def get_org_gate_settings_by_organization_id(self, organization_id: int) -> List[OrgGateSettingsInDB]:
        org_controller = OrgSettingsController(self.db_service)
        org_settings_list = await org_controller.get_org_settings_by_organization_id(organization_id)

        if not org_settings_list:
            return []

        org_setting = org_settings_list[0]
        return await self.get_org_gate_settings_by_org_settings_id(org_setting.id)

    async def get_org_gate_settings_by_org_settings_id(self, org_settings_id: int) -> List[OrgGateSettingsInDB]:
        try:
            raw_rows = await self._get_org_gate_setting_rows_by_org_settings_id(org_settings_id)
            if not raw_rows:
                return []

            hydrated_settings = []
            for settings in raw_rows:
                hydrated_settings.append(await self._hydrate_gate_setting_response(settings))
            return hydrated_settings

        except Exception as e:
            logger.exception(f"Error retrieving organization gate settings for organization setting {org_settings_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def update_org_gate_settings(self, org_gate_settings_id: int, org_gate_settings_data: OrgGateSettingsUpdate, user_id: str) -> OrgGateSettingsInDB:
        existing_settings = await self.get_org_gate_settings_by_id(org_gate_settings_id)

        updated_is_fixed_reviewer = (
            org_gate_settings_data.is_fixed_reviewer
            if org_gate_settings_data.is_fixed_reviewer is not None
            else existing_settings.is_fixed_reviewer
        )
        updated_is_fixed_approver = (
            org_gate_settings_data.is_fixed_approver
            if org_gate_settings_data.is_fixed_approver is not None
            else existing_settings.is_fixed_approver
        )
        updated_reviewer_emails = (
            org_gate_settings_data.reviewer_emails
            if org_gate_settings_data.reviewer_emails is not None
            else existing_settings.reviewer_emails
        )
        updated_approver_emails = (
            org_gate_settings_data.approver_emails
            if org_gate_settings_data.approver_emails is not None
            else existing_settings.approver_emails
        )

        fixed_actioners = await self._validate_fixed_actioner_payload(
            updated_is_fixed_reviewer,
            updated_reviewer_emails,
            updated_is_fixed_approver,
            updated_approver_emails,
        )

        update_params = {
            "id": org_gate_settings_id,
            "org_settings_id": existing_settings.org_settings_id,
            "reviewer_count": org_gate_settings_data.reviewer_count if org_gate_settings_data.reviewer_count is not None else existing_settings.reviewer_count,
            "approver_count": org_gate_settings_data.approver_count if org_gate_settings_data.approver_count is not None else existing_settings.approver_count,
            "is_fixed_reviewer": updated_is_fixed_reviewer,
            "is_fixed_approver": updated_is_fixed_approver,
            "is_active": org_gate_settings_data.is_active if org_gate_settings_data.is_active is not None else existing_settings.is_active,
            "user_id": user_id,
        }

        try:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_GATE_SETTING,
                list(update_params.values()),
            )

            await self._replace_fixed_actioners(
                existing_settings.org_settings_id,
                fixed_actioners["reviewer_emails"],
                fixed_actioners["approver_emails"],
                user_id,
            )

            return await self.get_org_gate_settings_by_id(org_gate_settings_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating organization gate settings {org_gate_settings_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def delete_org_gate_settings(self, org_gate_settings_id: int, user_id: str) -> dict:
        try:
            await self.get_org_gate_settings_by_id(org_gate_settings_id)
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ORG_GATE_SETTING,
                [org_gate_settings_id, user_id],
            )
            return {"message": "Organization gate settings deleted successfully."}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting organization gate settings {org_gate_settings_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def upsert_org_and_gate_settings(
        self,
        organization_id: int,
        payload: OrgAndGateSettingsUpsertRequest,
        user_id: str,
    ) -> OrgAndGateSettingsUpsertResponse:
        org_settings_controller = OrgSettingsController(self.db_service)

        org_settings_list = await org_settings_controller.get_org_settings_by_organization_id(organization_id)
        previous_org_settings = org_settings_list[0] if org_settings_list else None

        # Versioning behavior: deactivate current active records first, then create new rows.
        if previous_org_settings:
            previous_gate_settings = await self._get_org_gate_setting_rows_by_org_settings_id(previous_org_settings.id)
            for previous_gate_setting in previous_gate_settings:
                await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_ORG_GATE_SETTING,
                    [
                        previous_gate_setting["id"],
                        previous_org_settings.id,
                        previous_gate_setting.get("reviewer_count"),
                        previous_gate_setting.get("approver_count"),
                        previous_gate_setting.get("is_fixed_reviewer"),
                        previous_gate_setting.get("is_fixed_approver"),
                        False,
                        user_id,
                    ],
                )

            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_SETTING,
                [
                    previous_org_settings.id,
                    previous_org_settings.organization_id,
                    previous_org_settings.retry_count,
                    previous_org_settings.is_notification_required,
                    False,
                    user_id,
                ],
            )

        org_settings = await org_settings_controller.create_org_settings(
            OrgSettingsCreate(
                organization_id=organization_id,
                retry_count=payload.retry_count,
                is_notification_required=payload.is_notification_required,
            ),
            user_id,
        )

        fixed_actioners = await self._validate_fixed_actioner_payload(
            payload.is_fixed_reviewer,
            payload.reviewer_emails,
            payload.is_fixed_approver,
            payload.approver_emails,
        )

        results = await self.db_service.call_procedure(
            StoredProcedures.UPSERT_ORG_GATE_SETTING,
            [
                None,
                org_settings.id,
                payload.reviewer_count,
                payload.approver_count,
                payload.is_fixed_reviewer,
                payload.is_fixed_approver,
                True,
                user_id,
            ],
        )
        if not results or not results[0] or "id" not in results[0][0]:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to upsert organization gate settings.")

        org_gate_settings_id = results[0][0]["id"]
        if fixed_actioners["reviewer_emails"] or fixed_actioners["approver_emails"]:
            await self._create_fixed_actioners(
                org_settings.id,
                fixed_actioners["reviewer_emails"],
                fixed_actioners["approver_emails"],
                user_id,
            )

        org_gate_settings = await self.get_org_gate_settings_by_id(org_gate_settings_id)

        return OrgAndGateSettingsUpsertResponse(
            org_settings=org_settings,
            org_gate_settings=org_gate_settings,
        )
