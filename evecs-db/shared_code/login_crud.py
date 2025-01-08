# shared_code/login_crud.py 

import logging
import json
import uuid
import re

# Example password validation helper:
def validate_password_strength(password: str) -> bool:
    """
    Checks that 'password' is at least 8 characters
    and contains at least 2 special symbols from a certain set.
    """
    if len(password) < 8:
        return False

    special_chars = set("!@#$%^&*()_+-={}[]|\\:;\"'<>,.?/~`")
    count_special = sum(ch in special_chars for ch in password)
    return count_special >= 2

def register_user(req, UsersContainerProxy):
    """
    Registers a new user in the system.
    Input (JSON):
      - email (unique)
      - password (min 8 chars + 2 special chars)
      - auth (optional, default False)
    Output: { status_code: int, body: dict }
    """
    try:
        body = req.get_json()
        email = body.get("email")
        password = body.get("password")
        auth = body.get("auth", False)
        groups = body.get("groups", [])

        # Check mandatory fields
        if not email or not password:
            return {
                "status_code": 400,
                "body": {"error": "email and password are required."}
            }

        # Validate email uniqueness
        check_query = "SELECT * FROM c WHERE c.email = @em"
        params = [{"name": "@em", "value": email}]
        existing_users = list(UsersContainerProxy.query_items(
            query=check_query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        if existing_users:
            return {
                "status_code": 400,
                "body": {"error": f"Email '{email}' is already in use."}
            }

        # Validate password strength
        if not validate_password_strength(password):
            return {
                "status_code": 400,
                "body": {"error": "Password must be at least 8 characters and contain at least 2 special symbols."}
            }

        # Generate user_id
        user_id = str(uuid.uuid4())

        # For demonstration, we are ignoring the IP field. We must provide a default to satisfy the user schema
        user_doc = {
            "id": user_id,
            "user_id": user_id,
            "IP": "0.0.0.0",
            "email": email,
            "auth": auth,
            "password": password,
            "groups": groups 
        }

        # Insert into Cosmos DB
        UsersContainerProxy.create_item(user_doc)

        return {
            "status_code": 201,
            "body": {"result": f"User '{email}' has been registered."}
        }

    except Exception as e:
        logging.error(f"Error registering user: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

def login_user(req, UsersContainerProxy):
    """
    Logs in a user.
    Input (JSON):
      - email
      - password
    Output: { status_code: int, body: dict }
    """
    try:
        body = req.get_json()
        email = body.get("email")
        password = body.get("password")

        if not email or not password:
            return {
                "status_code": 400,
                "body": {"error": "Email and password are required."}
            }

        # Query user by email
        query = "SELECT * FROM c WHERE c.email = @em"
        params = [{"name": "@em", "value": email}]
        users = list(UsersContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if not users:
            return {
                "status_code": 400,
                "body": {"error": f"User with email '{email}' not found."}
            }

        user_doc = users[0]

        # Check password
        if user_doc["password"] != password:
            return {
                "status_code": 400,
                "body": {"error": "Password is incorrect."}
            }

        return {
            "status_code": 200,
            "body": {"result": f"User '{email}' has been logged in.",
                     "id": user_doc["user_id"],
                     "auth": user_doc["auth"]
                }
        }

    except Exception as e:
        logging.error(f"Error logging in user: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

def update_user(req, UsersContainerProxy):
    """
    Updates a user's info.
    Updatable fields: email, password, auth, groups
    Input (JSON):
      - user_id OR current email to locate user
      - (any of the updatable fields)
    At least one updatable field must be present.
    Output: { status_code: int, body: dict }
    """
    try:
        body = req.get_json()

        user_id = body.get("user_id")
        email_identifier = body.get("email")  # might be new or old
        new_email = body.get("new_email")     # if user wants to change it
        new_password = body.get("password")
        new_auth = body.get("auth", None)
        new_groups = body.get("groups", None)

        # Must identify user by either user_id or email
        if not user_id and not email_identifier:
            return {
                "status_code": 400,
                "body": {"error": "Must provide either user_id or email to locate user."}
            }

        # Build query
        if user_id:
            query = "SELECT * FROM c WHERE c.user_id = @uid"
            params = [{"name": "@uid", "value": user_id}]
        else:
            # Identify user by current email
            query = "SELECT * FROM c WHERE c.email = @em"
            params = [{"name": "@em", "value": email_identifier}]

        users = list(UsersContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        if not users:
            return {
                "status_code": 404,
                "body": {"error": "User not found."}
            }

        user_doc = users[0]

        # Keep track if anything changed
        updated_anything = False

        # 1) Check new_email
        if new_email is not None:
            if not isinstance(new_email, str):
                return {
                    "status_code": 400,
                    "body": {"error": "email must be a string."}
                }

            # Check uniqueness
            query_email = "SELECT * FROM c WHERE c.email = @em AND c.user_id != @uid"
            params_email = [
                {"name": "@em",  "value": new_email},
                {"name": "@uid", "value": user_doc["user_id"]}
            ]
            existing_same_email = list(UsersContainerProxy.query_items(
                query=query_email,
                parameters=params_email,
                enable_cross_partition_query=True
            ))
            if existing_same_email:
                return {
                    "status_code": 400,
                    "body": {"error": f"Email '{new_email}' is already in use by another user."}
                }
            user_doc["email"] = new_email
            updated_anything = True

        # 2) Check new_password
        if new_password is not None:
            if not isinstance(new_password, str):
                return {
                    "status_code": 400,
                    "body": {"error": "password must be a string."}
                }
            if not validate_password_strength(new_password):
                return {
                    "status_code": 400,
                    "body": {"error": "Password must be >= 8 chars and have >= 2 special chars."}
                }
            user_doc["password"] = new_password
            updated_anything = True

        # 3) Check new_auth
        if new_auth is not None:
            if not isinstance(new_auth, bool):
                return {
                    "status_code": 400,
                    "body": {"error": "auth must be a boolean (true/false)."}
                }
            user_doc["auth"] = new_auth
            updated_anything = True

        # 4) Check groups
        if new_groups is not None:
            if not isinstance(new_groups, list):
                return {
                    "status_code": 400,
                    "body": {"error": "groups must be an array."}
                }
            user_doc["groups"] = new_groups
            updated_anything = True

        if not updated_anything:
            return {
                "status_code": 400,
                "body": {"error": "No valid field to update. Provide at least one of: new_email, password, auth."}
            }

        # Replace user doc in DB
        UsersContainerProxy.replace_item(user_doc, user_doc)

        return {
            "status_code": 200,
            "body": {"result": f"User '{user_doc['email']}' has been updated."}
        }

    except Exception as e:
        logging.error(f"Error updating user: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

def delete_user(req, UsersContainerProxy):
    """
    Deletes a user by email + password verification.
    Input (JSON):
      - email
      - password
    Output: { status_code: int, body: dict }
    """
    try:
        body = req.get_json()
        email = body.get("email")
        password = body.get("password")

        if not email or not password:
            return {
                "status_code": 400,
                "body": {"error": "email and password are required to delete user."}
            }

        # Query user by email
        query = "SELECT * FROM c WHERE c.email = @em"
        params = [{"name": "@em", "value": email}]
        users = list(UsersContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        if not users:
            return {
                "status_code": 404,
                "body": {"error": f"User '{email}' not found."}
            }

        user_doc = users[0]

        # Check password
        if user_doc["password"] != password:
            return {
                "status_code": 400,
                "body": {"error": "Invalid password."}
            }

        # Partition key might be user_id or email, adapt accordingly
        UsersContainerProxy.delete_item(item=user_doc, partition_key=user_doc["user_id"])

        return {
            "status_code": 200,
            "body": {"result": f"User '{email}' has been deleted."}
        }

    except Exception as e:
        logging.error(f"Error deleting user: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

def get_account_details(req, UsersContainerProxy, EventsContainerProxy, TicketsContainerProxy):
    """
    Gets all details associated with a user account.
    Input: user_id (via query param or POST body)
    Output: User details, events created, tickets held
    """
    try:
        # Get user_id from either query params or POST body
        if req.method == 'POST':
            body = req.get_json()
            user_id = body.get("user_id")
        else:
            user_id = req.params.get("user_id")

        if not user_id:
            return {
                "status_code": 400,
                "body": {"error": "user_id is required"}
            }

        # 1. Get user details
        user_query = "SELECT * FROM c WHERE c.user_id = @uid"
        user_params = [{"name": "@uid", "value": user_id}]
        users = list(UsersContainerProxy.query_items(
            query=user_query,
            parameters=user_params,
            enable_cross_partition_query=True
        ))

        if not users:
            return {
                "status_code": 404,
                "body": {"error": "User not found"}
            }

        user_doc = users[0]
        # Remove sensitive information
        if "password" in user_doc:
            del user_doc["password"]

        # 2. Get events created by user
        events_query = "SELECT * FROM c WHERE ARRAY_CONTAINS(c.creator_id, @uid)"
        events = list(EventsContainerProxy.query_items(
            query=events_query,
            parameters=user_params,
            enable_cross_partition_query=True
        ))

        # 3. Get tickets held by user
        tickets_query = "SELECT * FROM c WHERE c.user_id = @uid"
        tickets = list(TicketsContainerProxy.query_items(
            query=tickets_query,
            parameters=user_params,
            enable_cross_partition_query=True
        ))

        # 4. Get groups associated with user
        groups = user_doc.get("groups", [])

        return {
            "status_code": 200,
            "body": {
                "user": user_doc,
                "events_created": events,
                "tickets": tickets,
                "groups": groups
            }
        }

    except Exception as e:
        logging.error(f"Error in get_account_details: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

def get_user_id_from_email(req, UsersContainerProxy):
    """
    Gets user_id(s) associated with provided email(s).
    Input (JSON):
      - emails: string or array of strings
    Output: { status_code: int, body: dict }
    """
    try:
        # Get emails from either POST body or GET params
        if req.method == 'POST':
            body = req.get_json()
            emails = body.get("emails")
        else:  # GET
            emails = req.params.get("emails")

        # Validate input
        if not emails:
            return {
                "status_code": 400,
                "body": {"error": "emails parameter is required"}
            }

        # Convert single email to list for consistent processing
        if isinstance(emails, str):
            emails = [emails]
        
        if not isinstance(emails, list):
            return {
                "status_code": 400,
                "body": {"error": "emails must be a string or array of strings"}
            }

        # Initialize results dictionary
        results = {}

        # Query each email
        for email in emails:
            if not isinstance(email, str):
                continue

            query = "SELECT c.user_id, c.email FROM c WHERE c.email = @em"
            params = [{"name": "@em", "value": email}]
            users = list(UsersContainerProxy.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))

            if users:
                results[email] = users[0]["user_id"]
            else:
                results[email] = None

        return {
            "status_code": 200,
            "body": {
                "email_to_user_id": results,
                "count": len(results)
            }
        }

    except Exception as e:
        logging.error(f"Error getting user_ids from emails: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }