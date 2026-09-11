# CCPM Main Service

This is the main service for the CCPM application. It provides a RESTful API for managing users, roles, permissions, organizations, assets, assessments, and more.

## Getting Started

These instructions will get you a copy of the project up and running on your local machine for development and testing purposes.

### Prerequisites

* Python 3.9+
* Poetry

### Installing

1. Clone the repository:
   ```bash
   git clone -b main-service https://eygs@dev.azure.com/eygs/ey-india-emerging-technologies/_git/CCPM
   ```
2. Navigate to the project directory:
   ```bash
   cd ccpm-main-service
   ```
3. Install the dependencies using Poetry:
   ```bash
   poetry install
   ```
4. Create a `.env` file from the `.env.sample` and populate it with your configuration.

### Running the Application

To run the application, use the following command:

```bash
poetry run uvicorn app.main:app --reload
```

## API Endpoints

The following is a list of the available API endpoints.

### Monitoring

*   **GET /** - Check Service Status
*   **GET /health** - Health Check

### Authentication

*   **POST /auth/token** - Authenticate user and get access token
*   **POST /auth/change-password** - Change user password

### Assessments

*   **POST /assessments/** - Create a new assessment
*   **GET /assessments/{assessment_id}** - Retrieve an assessment by ID
*   **GET /assessments/** - Retrieve all assessments
*   **PATCH /assessments/{assessment_id}** - Update an existing assessment
*   **DELETE /assessments/{assessment_id}** - Delete an assessment

### Assets

*   **POST /assets/** - Create a new asset
*   **GET /assets/{asset_id}** - Retrieve an asset by ID
*   **GET /assets/** - Retrieve all assets
*   **PATCH /assets/{asset_id}** - Update an existing asset
*   **DELETE /assets/{asset_id}** - Delete an asset

### Asset Types

*   **POST /asset-types/** - Create a new asset type
*   **GET /asset-types/{asset_type_id}** - Retrieve an asset type by ID
*   **GET /asset-types/** - Retrieve all asset types
*   **PATCH /asset-types/{asset_type_id}** - Update an existing asset type
*   **DELETE /asset-types/{asset_type_id}** - Delete an asset type

### Change Requests

*   **POST /change-requests/{change_request_id}/changes** - Add a new change to a change request
*   **GET /change-requests/{change_request_id}** - Retrieve a change request by ID
*   **GET /change-requests/** - Retrieve all change requests
*   **PATCH /change-requests/{change_request_id}** - Update an existing change request
*   **PATCH /change-requests/{change_request_id}/changes/{change_id}** - Update an existing change
*   **DELETE /change-requests/{change_request_id}/changes/{change_id}** - Delete a change

### Files

*   **POST /files/** - Upload files and create file records
*   **GET /files/{file_id}** - Download a file by ID
*   **GET /files/** - Retrieve all file records
*   **DELETE /files/{file_id}** - Delete a file record

### Frameworks

*   **POST /frameworks/** - Create a new framework
*   **GET /frameworks/{framework_id}** - Retrieve a framework by ID
*   **GET /frameworks/{framework_id}/configurations** - Retrieve all configurations for a framework
*   **GET /frameworks/{framework_id}/versions** - Retrieve all versions for a framework
*   **GET /frameworks/** - Retrieve all frameworks
*   **PATCH /frameworks/{framework_id}** - Update an existing framework
*   **DELETE /frameworks/{framework_id}** - Delete a framework

### Organizations

*   **POST /organizations/** - Create a new organization
*   **GET /organizations/{org_id}** - Retrieve an organization by ID
*   **GET /organizations/{org_id}/units** - Retrieve all organizational units for an organization
*   **GET /organizations/** - Retrieve all organizations
*   **PATCH /organizations/{org_id}** - Update an existing organization
*   **DELETE /organizations/{org_id}** - Delete an organization

### Organizational Unit Values

*   **GET /org-unit-values/{org_id}/hierarchy** - Get the complete organizational unit value hierarchy for an organization
*   **POST /org-unit-values/create-hierarchy** - Create a hierarchy of organizational unit values

### Organization Settings

*   **POST /org-settings/** - Create new organization settings
*   **GET /org-settings/{org_settings_id}** - Get organization settings by ID
*   **GET /org-settings/by-organization/{organization_id}** - Get organization settings by organization ID
*   **PATCH /org-settings/{org_settings_id}** - Update organization settings
*   **DELETE /org-settings/{org_settings_id}** - Delete organization settings

### Permissions

*   **POST /permissions/** - Create a new permission
*   **GET /permissions/{permission_id}** - Retrieve a permission by ID
*   **GET /permissions/** - Retrieve all permissions
*   **PATCH /permissions/{permission_id}** - Update an existing permission
*   **DELETE /permissions/{permission_id}** - Delete a permission

### Roles

*   **POST /roles/** - Create a new role
*   **GET /roles/{role_id}** - Retrieve a role by ID
*   **GET /roles/** - Retrieve all roles
*   **PATCH /roles/{role_id}** - Update an existing role
*   **DELETE /roles/{role_id}** - Delete a role

### Role-Permission Assignments

*   **POST /role-permissions/** - Create a new role-permission assignment
*   **GET /role-permissions/{rp_id}** - Retrieve a role-permission assignment by ID
*   **GET /role-permissions/** - Retrieve all role-permission assignments
*   **PATCH /role-permissions/{rp_id}** - Update an existing role-permission assignment
*   **DELETE /role-permissions/{rp_id}** - Delete a role-permission assignment

### User-Role-Permission Assignments

*   **POST /user-role-assignments/** - Create a new user-role-permission assignment
*   **GET /user-role-assignments/{urap_id}** - Retrieve a user-role-permission assignment by ID
*   **GET /user-role-assignments/** - Retrieve all user-role-permission assignments
*   **PATCH /user-role-assignments/{urap_id}** - Update an existing user-role-permission assignment
*   **DELETE /user-role-assignments/{urap_id}** - Delete a user-role-permission assignment

### Users

*   **POST /users/** - Create a new user
*   **GET /users/{user_id}** - Retrieve a user by ID
*   **GET /users/** - Retrieve all users
*   **PATCH /users/{user_id}** - Update an existing user
*   **DELETE /users/{user_id}** - Delete a user
