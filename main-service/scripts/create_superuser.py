import asyncio
import getpass
import os
import sys

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security import get_password_hash
from app.core.stored_procedures import StoredProcedures
from app.models.user_models import UserCreate
from app.services.mysql_service import MySQLService
from app.config import get_settings

async def create_superuser():
    """
    Creates a superuser with the provided details.
    """
    settings = get_settings()
    db_service = MySQLService(settings.DB_CONNECTION_STRING.get_secret_value())

    try:
        await db_service.connect()

        print("Creating a new superuser...")

        # Handle organization
        results = await db_service.call_procedure(
            procedure_name=StoredProcedures.FILTER_ORGANIZATIONS,
            parameters=[
                None,
                None,
                None,
                None,
                None
            ]
        )
        organizations = results[0] if results else []
        organization_id = None

        if not organizations:
            print("No organizations found. Please create one.")
            org_name = input("Organization Name: ")
            # Assume the UPSERT_ORGANIZATION procedure returns the new ID
            results = await db_service.call_procedure(
                StoredProcedures.UPSERT_ORGANIZATION,
                [None, org_name, None, True, "system"]  # Pass None for the ID
            )
            if results and results[0] and 'id' in results[0][0]:
                organization_id = results[0][0]['id']
                print(f"Organization '{org_name}' created successfully with ID: {organization_id}")
            else:
                print("Could not retrieve the new organization ID. Aborting.")
                return
        else:
            print("Please select an organization:")
            for i, org in enumerate(organizations):
                print(f"{i + 1}. {org['name']}")
            
            while True:
                try:
                    choice = int(input("Enter the number of the organization: "))
                    if 1 <= choice <= len(organizations):
                        organization_id = organizations[choice - 1]['id']
                        break
                    else:
                        print("Invalid choice. Please try again.")
                except ValueError:
                    print("Invalid input. Please enter a number.")

        first_name = input("First Name: ")
        last_name = input("Last Name: ")
        email = input("Email: ")
        password = getpass.getpass("Password: ")
        confirm_password = getpass.getpass("Confirm Password: ")

        if password != confirm_password:
            print("Passwords do not match.")
            return

        hashed_password = get_password_hash(password)
        
        # For the initial superuser, we set created_by to the user's own email.
        created_by = email

        user_data = UserCreate(
            first_name=first_name,
            last_name=last_name,
            email=email,
            hashed_password=hashed_password,
            phone_number=None,
            location_id=None,
            organization_id=organization_id,
            is_active=True,
            created_by=created_by
        )

        user_results = await db_service.call_procedure(
            StoredProcedures.UPSERT_USER,
            [
                None,  # Pass None for the ID
                user_data.first_name,
                user_data.last_name,
                user_data.email,
                user_data.hashed_password,
                user_data.phone_number,
                user_data.location_id,
                user_data.organization_id,
                user_data.is_active,
                user_data.created_by
            ]
        )

        if not user_results or not user_results[0] or 'id' not in user_results[0][0]:
            print("Failed to create superuser or retrieve user ID. Aborting.")
            return

        user_id = user_results[0][0]['id']
        print(f"Superuser '{email}' created successfully with ID: {user_id}.")

        # 1. Create/Retrieve Superuser Role
        superuser_role_name = "superuser"
        results = await db_service.call_procedure(
            procedure_name=StoredProcedures.FILTER_ROLES,
            parameters=[None, superuser_role_name, None, None, None, None]
        )
        
        roles = results[0] if results else []
        
        superuser_role_id = None
        if roles:
            superuser_role_id = roles[0]['id']
            print(f"Found existing '{superuser_role_name}' role with ID: {superuser_role_id}.")
        else:
            print(f"'{superuser_role_name}' role not found. Creating it...")
            role_results = await db_service.call_procedure(
                StoredProcedures.UPSERT_ROLE,
                [None, superuser_role_name, "System superuser role with all permissions", True, "system"]
            )
            if role_results and role_results[0] and 'id' in role_results[0][0]:
                superuser_role_id = role_results[0][0]['id']
                print(f"'{superuser_role_name}' role created with ID: {superuser_role_id}.")
            else:
                print(f"Failed to create '{superuser_role_name}' role. Aborting.")
                return

        # 2. Retrieve All Permissions
        results = await db_service.call_procedure(
            procedure_name=StoredProcedures.FILTER_PERMISSIONS,
            parameters=[None, None, None, None, None, None]
        )
        all_permissions = results[0] if results else []

        if not all_permissions:
            print("No permissions found in the database. Superuser will have no permissions assigned.")
        else:
            print(f"Assigning {len(all_permissions)} permissions to '{superuser_role_name}' role...")
            # 3. Assign Permissions to Superuser Role
            for perm in all_permissions:
                await db_service.call_procedure(
                    StoredProcedures.UPSERT_ROLE_PERMISSION,
                    [None, superuser_role_id, perm['id'], True, "system"]
                )
            print(f"All permissions assigned to '{superuser_role_name}' role.")

            # 4. Assign Role-Permissions to User
            # Retrieve all role_permission_assignment_ids for the superuser role
            role_permissions_for_superuser_results = await db_service.call_procedure(
                procedure_name=StoredProcedures.FILTER_ROLE_PERMISSIONS,
                parameters=[None, superuser_role_id, None, None, None, None]
            )
            role_permissions_for_superuser = role_permissions_for_superuser_results[0] if role_permissions_for_superuser_results else []

            if role_permissions_for_superuser:
                print(f"Assigning {len(role_permissions_for_superuser)} role-permissions to superuser '{email}'...")
                for rp in role_permissions_for_superuser:
                    await db_service.call_procedure(
                        StoredProcedures.UPSERT_USER_ROLE_ASSIGNMENT_PERMISSION,
                        [None, rp['id'], user_id, True, "system"]
                    )
                print(f"All role-permissions assigned to superuser '{email}'.")
            else:
                print(f"No role-permissions found for '{superuser_role_name}' role. User will not have specific assignments.")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if db_service.pool:
            await db_service.disconnect()

if __name__ == "__main__":
    asyncio.run(create_superuser())
