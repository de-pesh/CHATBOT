"""
Defines constants for stored procedure names.

This module centralizes the names of stored procedures used throughout
the application, ensuring consistency and reducing the chance of typos.
"""

class StoredProcedures:
    """
    A collection of constants representing stored procedure names.

    Add new stored procedure names as class attributes.
    Example: GET_USER_BY_ID = "GetUserById"
    """

    # -------------------------------
    # Organization Stored Procedures
    # -------------------------------
    DELETE_ORGANIZATION = "delete_organization"
    DELETE_ORG_SETTING = "delete_org_setting"
    DELETE_ORG_GATE_SETTING = "delete_org_gate_setting"
    DELETE_ORG_UNITS = "delete_org_units"
    DELETE_ORG_UNIT_VALUES = "delete_org_unit_values"
    DELETE_ORG_ACTIONER = "delete_org_fixed_reviewers_approvers"

    FILTER_ORGANIZATIONS = "filter_organizations"
    FILTER_ORG_SETTINGS = "filter_org_settings"
    FILTER_ORG_GATE_SETTINGS = "filter_org_gate_settings"
    FILTER_ORG_UNITS = "filter_org_units"
    FILTER_ORG_UNIT_VALUES = "filter_org_unit_values"
    FILTER_ORG_ACTIONER = "filter_org_fixed_reviewers_approvers"

    UPSERT_ORGANIZATION = "upsert_organization"
    UPSERT_ORG_SETTING = "upsert_org_setting"
    UPSERT_ORG_GATE_SETTING = "upsert_org_gate_setting"
    UPSERT_ORG_UNIT = "upsert_org_unit"
    UPSERT_ORG_UNIT_VALUE = "upsert_org_unit_value"
    UPSERT_ORG_ACTIONER = "upsert_org_fixed_reviewers_approvers"

    # -------------------------------
    # Status Master Stored Procedures
    # -------------------------------
    FILTER_STATUS_MASTER = "filter_status_master"

    # -------------------------------
    # Asset Stored Procedures
    # -------------------------------
    DELETE_ASSET = "delete_asset"
    FILTER_ASSETS = "filter_assets"
    UPSERT_ASSET = "upsert_asset"

    # -------------------------------
    # Asset Type Stored Procedures
    # -------------------------------
    DELETE_ASSET_TYPE = "delete_asset_type"
    FILTER_ASSET_TYPES = "filter_asset_types"
    UPSERT_ASSET_TYPE = "upsert_asset_type"

    # -------------------------------
    # User Stored Procedures
    # -------------------------------
    DELETE_USER = "delete_user"
    FILTER_USERS = "filter_users"
    UPSERT_USER = "upsert_user"
    FILTER_USERS_USER_MANAGEMENT = "filter_users_user_management"

    # -------------------------------
    # Role Stored Procedures
    # -------------------------------
    DELETE_ROLE = "delete_role"
    FILTER_ROLES = "filter_roles"
    GET_ROLE_BY_ID = "get_role_by_id"
    UPSERT_ROLE = "upsert_role"

    # -------------------------------
    # Permission Stored Procedures
    # -------------------------------
    DELETE_PERMISSION = "delete_permission"
    FILTER_PERMISSIONS = "filter_permissions"
    GET_PERMISSION_BY_ID = "get_permission_by_id"
    UPSERT_PERMISSION = "upsert_permission"
    GET_PERMISSIONS_BY_ROLE = "get_permissions_by_role"

    # -------------------------------
    # Role Permission Stored Procedures
    # -------------------------------
    DELETE_ROLE_PERMISSION = "delete_role_permission"
    FILTER_ROLE_PERMISSIONS = "filter_role_permissions"
    GET_ROLE_PERMISSION_BY_ID = "get_role_permission_by_id"
    UPSERT_ROLE_PERMISSION = "upsert_role_permission"
    REPLACE_ROLE_PERMISSIONS = "replace_role_permissions"

    # -------------------------------
    # User Role Assignment Permission Stored Procedures
    # -------------------------------
    DELETE_USER_ROLE_ASSIGNMENT_PERMISSION = "delete_user_role_assignment_permission"
    FILTER_USER_ROLE_ASSIGNMENT_PERMISSIONS = "filter_user_role_assignment_permissions"
    GET_USER_ROLE_ASSIGNMENT_PERMISSION_BY_ID = "get_user_role_assignment_permission_by_id"
    UPSERT_USER_ROLE_ASSIGNMENT_PERMISSION = "upsert_user_role_assignment_permission"
    GET_USER_ROLES_AND_PERMISSIONS = "get_user_roles_and_permissions"

    # -------------------------------
    # RBAC Specific Stored Procedures
    # -------------------------------
    GET_USER_PERMISSIONS = "filter_user_permissions"

    # -------------------------------
    # Framework Stored Procedures
    # -------------------------------
    DELETE_FRAMEWORK = "delete_framework"
    FILTER_FRAMEWORKS = "filter_frameworks"
    FILTER_FRAMEWORKS_WITH_ORGANIZATION = "filter_frameworks_with_organization"
    FILTER_FRAMEWORK_CONFIGURATIONS = "filter_framework_configurations"
    UPSERT_FRAMEWORK = "upsert_framework"
    UPSERT_FRAMEWORK_CONFIGURATION = "upsert_framework_configuration"

    # -------------------------------
    # Assessment Stored Procedures
    # -------------------------------
    DELETE_ASSESSMENT = "delete_assessment"
    FILTER_ASSESSMENTS = "filter_assessments"
    UPSERT_ASSESSMENT = "upsert_assessment"
    UPSERT_ASSESSMENT_FILE = "upsert_assessment_file"
    FILTER_ASSESSMENT_FILES = "filter_assessment_files"
    DELETE_ASSESSMENT_FILE = "delete_assessment_file"

    # -------------------------------
    # Assessment Version Stored Procedures
    # -------------------------------
    DELETE_ASSESSMENT_VERSION = "delete_assessment_version"
    FILTER_ASSESSMENT_VERSIONS = "filter_assessment_versions"
    UPSERT_ASSESSMENT_VERSION = "upsert_assessment_version"

    # -------------------------------
    # Assessment Label Stored Procedures
    # -------------------------------
    DELETE_ASSESSMENT_LABEL = "delete_assessment_label"
    FILTER_ASSESSMENT_LABELS = "filter_assessment_labels"
    UPSERT_ASSESSMENT_LABEL = "upsert_assessment_label"

    # -------------------------------
    # Assessment Label Mapping Stored Procedures
    # -------------------------------
    DELETE_ASSESSMENT_LABEL_MAPPING = "delete_assessment_label_mapping"
    FILTER_ASSESSMENT_LABEL_MAPPINGS = "filter_assessment_label_mappings"
    UPSERT_ASSESSMENT_LABEL_MAPPING = "upsert_assessment_label_mapping"

    # -------------------------------
    # Result By Label Stored Procedures
    # -------------------------------
    DELETE_RESULT_BY_LABEL = "delete_result_by_label"
    FILTER_RESULTS_BY_LABEL = "filter_results_by_label"
    UPSERT_RESULT_BY_LABEL = "upsert_result_by_label"

    FILTER_RESULTS = "filter_results"
    UPSERT_RESULT = "upsert_result"
    CLONE_UNCHANGED_CONTROL_RESULTS = "clone_unchanged_control_results"

    # -------------------------------
    # [DEPRECATED] Tagging for Assessment Label Mapping (CONTROL level) Stored Procedures
    # -------------------------------
    # NOTE: These tag tagging entities to a CONTROL (assessment_label_mapping_id).
    # Superseded by the assessment-level procedures below. Kept for backward
    # compatibility with the legacy control-level tagging endpoints.
    # Asset
    DELETE_ASSET_ASSESSMENT_LABEL_MAPPING = "delete_asset_assessment_label_mapping"
    FILTER_ASSET_ASSESSMENT_LABEL_MAPPINGS = "filter_asset_assessment_label_mappings"
    UPSERT_ASSET_ASSESSMENT_LABEL_MAPPING = "upsert_asset_assessment_label_mapping"
    # Org_unit
    DELETE_ORG_UNIT_ASSESSMENT_LABEL_MAPPING = "delete_org_unit_assessment_label_mapping"
    FILTER_ORG_UNIT_ASSESSMENT_LABEL_MAPPINGS = "filter_org_unit_assessment_label_mappings"
    UPSERT_ORG_UNIT_ASSESSMENT_LABEL_MAPPING = "upsert_org_unit_assessment_label_mapping"
    # People
    DELETE_PEOPLE_ASSESSMENT_LABEL_MAPPING = "delete_people_assessment_label_mapping"
    FILTER_PEOPLE_ASSESSMENT_LABEL_MAPPINGS = "filter_people_assessment_label_mappings"
    UPSERT_PEOPLE_ASSESSMENT_LABEL_MAPPING = "upsert_people_assessment_label_mapping"

    # -------------------------------
    # Evidence Scope Tagging at ASSESSMENT level Stored Procedures
    # -------------------------------
    # These tag tagging entities directly to an ASSESSMENT (assessment_id),
    # backing the consolidated "Evidence Scope" step. See
    # scripts/evidence_scope_assessment_mapping.sql for the DDL + procedures.
    # Asset
    DELETE_ASSET_ASSESSMENT_MAPPING = "delete_asset_assessment_mapping"
    FILTER_ASSET_ASSESSMENT_MAPPINGS = "filter_asset_assessment_mappings"
    UPSERT_ASSET_ASSESSMENT_MAPPING = "upsert_asset_assessment_mapping"
    # Org_unit
    DELETE_ORG_UNIT_ASSESSMENT_MAPPING = "delete_org_unit_assessment_mapping"
    FILTER_ORG_UNIT_ASSESSMENT_MAPPINGS = "filter_org_unit_assessment_mappings"
    UPSERT_ORG_UNIT_ASSESSMENT_MAPPING = "upsert_org_unit_assessment_mapping"
    # People
    DELETE_PEOPLE_ASSESSMENT_MAPPING = "delete_people_assessment_mapping"
    FILTER_PEOPLE_ASSESSMENT_MAPPINGS = "filter_people_assessment_mappings"
    UPSERT_PEOPLE_ASSESSMENT_MAPPING = "upsert_people_assessment_mapping"

    # -------------------------------
    # Label Mapping Wise Files Stored Procedures
    # -------------------------------
    DELETE_TEMP_LABEL_MAPPING_WISE_FILE = "delete_temp_label_mapping_wise_file"
    FILTER_TEMP_LABEL_MAPPING_WISE_FILES = "filter_temp_label_mapping_wise_files"
    UPSERT_TEMP_LABEL_MAPPING_WISE_FILE = "upsert_temp_label_mapping_wise_file"
    DELETE_LABEL_MAPPING_WISE_FILE = "delete_label_mapping_wise_file"
    FILTER_LABEL_MAPPING_WISE_FILES = "filter_label_mapping_wise_files"
    UPSERT_LABEL_MAPPING_WISE_FILE = "upsert_label_mapping_wise_file"
    FILTER_ASSESSMENT_EVIDENCE_FILES = "filter_assessment_evidence_files"

    # -------------------------------
    # Aggregated Result Stored Procedures
    # -------------------------------
    FILTER_VERSION_RESULT_BUNDLE = "filter_version_result_bundle"

    # -------------------------------
    # File Stored Procedures
    # -------------------------------
    DELETE_FILE = "delete_file"
    FILTER_FILES = "filter_files"
    GET_FILES_BY_ENTITY = "get_files_by_entity"
    UPSERT_FILE = "upsert_file"

    # -------------------------------
    # Version Stored Procedures
    # -------------------------------
    FILTER_VERSIONS = "filter_versions"
    FILTER_VERSION_FILES = "filter_version_files"
    DELETE_VERSION_FILE = "delete_version_file"
    UPSERT_VERSION = "upsert_version"
    UPSERT_VERSION_FILE = "upsert_version_file"

    # -------------------------------
    # Change Stored Procedures
    # -------------------------------
    FILTER_CHANGE_REQUESTS = "filter_change_requests"
    FILTER_CHANGES = "filter_changes"
    UPSERT_CHANGE = "upsert_change"
    UPSERT_CHANGE_REQUEST = "upsert_change_request"

    # -------------------------------
    # Node Change Stored Procedures
    # -------------------------------
    FILTER_NODE_CHANGES = "filter_node_changes"
    FILTER_NODES = "filter_node_details"
    FILTER_TEMP_NODES = "filter_temp_node_details"
    UPSERT_NODE_CHANGE = "upsert_node_change"
    UPSERT_NODE = "upsert_node_detail"
    UPSERT_TEMP_NODES = "upsert_temp_node_detail"
    RESOLVE_NODE_FOR_RUN = "resolve_node_for_run"

    # ─────────────────────────────────────────────────────────────
    # Assessment Workflow & Actioner Stored Procedures (NEW DESIGN)
    # ─────────────────────────────────────────────────────────────
    # Three separate tables for reviewers, approvers, and control owners
    # Each with stage-aware tracking (creation_review, run_review, etc.)

    # Assessment Reviewer Stored Procedures
    DELETE_ASSESSMENT_REVIEWER = "delete_assessment_reviewer"
    FILTER_ASSESSMENT_REVIEWERS = "filter_assessment_reviewers"
    UPSERT_ASSESSMENT_REVIEWER = "upsert_assessment_reviewer"
    DEACTIVATE_ASSESSMENT_REVIEWERS = "deactivate_assessment_reviewers"

    # Assessment Approver Stored Procedures
    DELETE_ASSESSMENT_APPROVER = "delete_assessment_approver"
    FILTER_ASSESSMENT_APPROVERS = "filter_assessment_approvers"
    UPSERT_ASSESSMENT_APPROVER = "upsert_assessment_approver"
    DEACTIVATE_ASSESSMENT_APPROVERS = "deactivate_assessment_approvers"

    # Assessment Control Owner Stored Procedures
    DELETE_ASSESSMENT_CONTROL_OWNER = "delete_assessment_control_owner"
    FILTER_ASSESSMENT_CONTROL_OWNERS = "filter_assessment_control_owners"
    UPSERT_ASSESSMENT_CONTROL_OWNER = "upsert_assessment_control_owner"
    DEACTIVATE_ASSESSMENT_CONTROL_OWNERS = "deactivate_assessment_control_owners"

    # ─────────────────────────────────────────────────────────────
    # Legacy Assessment Workflow Stored Procedures (DEPRECATED)
    # ─────────────────────────────────────────────────────────────
    # These are kept for backward compatibility and audit trails
    DELETE_ASSESSMENT_ACTIONER = "delete_assessment_actioner"
    FILTER_ASSESSMENT_ACTIONERS = "filter_assessment_actioners"
    UPSERT_ASSESSMENT_ACTIONER = "upsert_assessment_actioner"

    DELETE_ASSESSMENT_WORKFLOW = "delete_assessment_workflow"
    FILTER_ASSESSMENT_WORKFLOW = "filter_assessment_workflow"
    UPSERT_ASSESSMENT_WORKFLOW = "upsert_assessment_workflow"

    ##############NEW##################################
    FILTER_ORG_GATE_SETTING = "filter_org_gate_settings"