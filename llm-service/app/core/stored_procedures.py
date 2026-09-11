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
    # Organization Stored Procedures
    GET_ORGANIZATION_BY_ID = "get_organization_by_id"
    DELETE_ORGANIZATION = "delete_organization"
    UPSERT_ORGANIZATION = "upsert_organization"
    FILTER_ORGANIZATIONS = "filter_organizations"

    # Asset Stored Procedures
    
    DELETE_ASSET = "delete_asset"
    UPSERT_ASSET = "upsert_asset"
    FILTER_ASSETS = "filter_assets"

    # Asset Type Stored Procedures
    DELETE_ASSET_TYPE = "delete_asset_type"
    UPSERT_ASSET_TYPE = "upsert_asset_type"
    FILTER_ASSET_TYPES = "filter_asset_types"

    # User Stored Procedures
    GET_USER_BY_ID = "get_user_by_id"
    DELETE_USER = "delete_user"
    UPSERT_USER = "upsert_user"
    FILTER_USERS = "filter_users"

    # Role Stored Procedures
    GET_ROLE_BY_ID = "get_role_by_id"
    DELETE_ROLE = "delete_role"
    UPSERT_ROLE = "upsert_role"
    FILTER_ROLES = "filter_roles"

    # Framework Stored Procedures
    GET_FRAMEWORK_BY_ID = "get_framework_by_id"
    DELETE_FRAMEWORK = "delete_framework"
    UPSERT_FRAMEWORK = "upsert_framework"
    FILTER_FRAMEWORKS = "filter_frameworks"
    FILTER_FRAMEWORK_CONFIGURATIONS = "filter_framework_configurations"
    GET_FRAMEWORK_WITH_DETAILS = "get_framework_with_details"

    # Assessment Stored Procedures
    GET_ASSESSMENT_BY_ID = "get_assessment_by_id"
    DELETE_ASSESSMENT = "delete_assessment"
    UPSERT_ASSESSMENT = "upsert_assessment"
    FILTER_ASSESSMENTS = "filter_assessments"

    # File Stored Procedures
    GET_FILE_BY_ID = "get_file_by_id"
    DELETE_FILE = "delete_file"
    UPSERT_FILE = "upsert_file"
    FILTER_FILES = "filter_files"

    # Permission Stored Procedures
    GET_PERMISSION_BY_ID = "get_permission_by_id"
    DELETE_PERMISSION = "delete_permission"
    UPSERT_PERMISSION = "upsert_permission"
    FILTER_PERMISSIONS = "filter_permissions"

    # Role Permission Stored Procedures
    GET_ROLE_PERMISSION_BY_ID = "get_role_permission_by_id"
    DELETE_ROLE_PERMISSION = "delete_role_permission"
    UPSERT_ROLE_PERMISSION = "upsert_role_permission"
    FILTER_ROLE_PERMISSIONS = "filter_role_permissions"

    # User Role Assignment Permission Stored Procedures
    GET_USER_ROLE_ASSIGNMENT_PERMISSION_BY_ID = "get_user_role_assignment_permission_by_id"
    DELETE_USER_ROLE_ASSIGNMENT_PERMISSION = "delete_user_role_assignment_permission"
    UPSERT_USER_ROLE_ASSIGNMENT_PERMISSION = "upsert_user_role_assignment_permission"
    FILTER_USER_ROLE_ASSIGNMENT_PERMISSIONS = "filter_user_role_assignment_permissions"

    # RBAC Specific Stored Procedures
    GET_USER_PERMISSIONS = "filter_user_permissions"

    # Additional Stored Procedures from user input
    DELETE_ASSESSMENT_FILE = "delete_assessment_file"
    DELETE_CHANGE = "delete_change"
    DELETE_CONFIGURATION = "delete_configuration"
    DELETE_FRAMEWORK_CONFIGURATION = "delete_framework_configuration"
    DELETE_LOCATION = "delete_location"
    DELETE_ORG_UNIT = "delete_org_unit"
    DELETE_USER_ORG_UNIT = "delete_user_org_unit"
    DELETE_VERSION = "delete_version"

    FILTER_CHANGE_REQUESTS = "filter_change_requests"
    FILTER_CHANGES = "filter_changes"
    FILTER_CONFIGURATIONS = "filter_configurations"
    FILTER_LOCATIONS = "filter_locations"
    FILTER_ORG_UNITS = "filter_org_units"
    FILTER_VERSIONS = "filter_versions"

    GET_CHANGE_BY_ID = "get_change_by_id"
    GET_CONFIGURATION_BY_ID = "get_configuration_by_id"
    GET_LOCATION_BY_ID = "get_location_by_id"
    GET_ORG_UNIT_BY_ID = "get_org_unit_by_id"
    GET_VERSION_BY_ID = "get_version_by_id"

    UPSERT_ASSESSMENT_FILE = "upsert_assessment_file"
    UPSERT_CHANGE = "upsert_change"
    UPSERT_CHANGE_REQUEST = "upsert_change_request"
    UPSERT_CONFIGURATION = "upsert_configuration"
    UPSERT_FRAMEWORK_CONFIGURATION = "upsert_framework_configuration"
    UPSERT_LOCATION = "upsert_location"
    UPSERT_ORG_UNIT = "upsert_org_unit"
    UPSERT_USER_ORG_UNIT = "upsert_user_org_unit"
    UPSERT_VERSION = "upsert_version"

    # Node Change Stored Procedures
    UPSERT_NODE_CHANGE = "upsert_node_change"
    FILTER_NODE_CHANGES = "filter_node_changes"

    # Get Files By Entity
    GET_FILES_BY_ENTITY = "get_files_by_entity"

    # Version File Stored Procedures
    UPSERT_VERSION_FILE = "upsert_version_file"
    DELETE_VERSION_FILE = "delete_version_file"
    FILTER_VERSION_FILES = "filter_version_files"

    # Result By Labels Stored Procedures
    UPSERT_RESULT_BY_LABEL = "upsert_result_by_label"
    FILTER_RESULTS_BY_LABEL = "filter_results_by_label"
    DELETE_RESULTS_BY_LABEL = "delete_results_by_label"

    # Results Stored Procedures
    UPSERT_RESULT = "upsert_result"
    FILTER_RESULTS = "filter_results"
    DELETE_RESULT = "delete_result"

    # Temp Node Details Stored Procedures
    UPSERT_TEMP_NODE_DETAIL = "upsert_temp_node_detail"
    FILTER_TEMP_NODE_DETAILS = "filter_temp_node_details"
