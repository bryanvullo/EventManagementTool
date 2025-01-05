# shared_code/ticket_crud.py

import logging
import json
import jsonschema
import uuid
from urllib.parse import urlparse

def load_ticket_schema():
    # Load ticket schema for validation
    with open('schemas/ticket.json', 'r') as f:
        return json.load(f)

TICKET_SCHEMA = load_ticket_schema()

def create_ticket(req, TicketsContainerProxy, UsersContainerProxy, EventsContainerProxy):
    """
    Create a new ticket.
    """
    try:
        body = req.get_json()

        # ---- 0) Check mandatory fields ----
        mandatory_fields = ["user_id", "event_id", "email"]
        for field in mandatory_fields:
            if field not in body:
                return {
                    "status_code": 400,
                    "body": {"error": f"Missing mandatory field: {field}"}
                }

        # ---- 1) Validate email, for both type and format ----
        email = body["email"]
        if not isinstance(email, str):
            return {
                "status_code": 400,
                "body": {"error": "Email must be a string."}
            }
        
        email_pattern = TICKET_SCHEMA["properties"]["email"]["pattern"]
        
        email_schema = {
            "type": "object",
            "properties": {
                "email": TICKET_SCHEMA["properties"]["email"]
            },
            "required": ["email"]
        }
        try:
            jsonschema.validate(instance={"email": email}, schema=email_schema)
        except jsonschema.exceptions.ValidationError:
            return {
                "status_code": 400,
                "body": {"error": "Invalid email format."}
            }

        # ---- 2) Check if user_id exists ----
        user_query = "SELECT * FROM c WHERE c.user_id = @user_id"
        user_params = [{"name": "@user_id", "value": body["user_id"]}]
        user_items = list(UsersContainerProxy.query_items(
            query=user_query,
            parameters=user_params,
            enable_cross_partition_query=True
        ))
        if not user_items:
            return {
                "status_code": 400,
                "body": {"error": f"User '{body['user_id']}' not found in the users database."}
            }

        # ---- 3) Check if event_id exists ----
        event_query = "SELECT * FROM c WHERE c.event_id = @event_id"
        event_params = [{"name": "@event_id", "value": body["event_id"]}]
        event_items = list(EventsContainerProxy.query_items(
            query=event_query,
            parameters=event_params,
            enable_cross_partition_query=True
        ))
        if not event_items:
            return {
                "status_code": 400,
                "body": {"error": f"Event '{body['event_id']}' not found in the events database."}
            }

        # ---- 4) Generate unique ticket_id ----
        ticket_id = str(uuid.uuid4())
        body["ticket_id"] = ticket_id

        # ---- 5) Build the ticket document ----
        ticket_doc = {
            "ticket_id": ticket_id,
            "user_id": body["user_id"],
            "event_id": body["event_id"],
            "email": email
        }

        # ---- 6) Validate with JSON Schema ----
        jsonschema.validate(instance=ticket_doc, schema=TICKET_SCHEMA)

        # ---- 7) Insert into Cosmos DB ----
        TicketsContainerProxy.create_item(ticket_doc)

        return {
            "status_code": 201,
            "body": {"result": "success", "ticket_id": ticket_id}
        }

    except jsonschema.exceptions.ValidationError as e:
        return {
            "status_code": 400,
            "body": {"error": f"JSON schema validation error: {str(e)}"}
        }
    except Exception as e:
        logging.error(f"Error creating ticket: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

def get_ticket(req, TicketsContainerProxy):
    """
    READ a ticket by ticket_id.
    """
    try:
        if req.method == 'POST':
            data = req.get_json()
            ticket_id = data.get("ticket_id")
        else:  # 'GET'
            ticket_id = req.params.get("ticket_id")

        if not ticket_id:
            return {
                "status_code": 400,
                "body": {"error": "Missing ticket_id"}
            }

        # Query the ticket by ticket_id
        query = "SELECT * FROM c WHERE c.ticket_id = @ticket_id"
        params = [{"name": "@ticket_id", "value": ticket_id}]
        items = list(TicketsContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if not items:
            return {
                "status_code": 404,
                "body": {"error": "Ticket not found"}
            }

        ticket_doc = items[0]

        return {
            "status_code": 200,
            "body": ticket_doc
        }

    except Exception as e:
        logging.error(f"Error retrieving ticket: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }
    
def delete_ticket(req, TicketsContainerProxy):
    """
    DELETE a ticket by ticket_id.
    """
    try:
        if req.method == 'POST':
            data = req.get_json()
            ticket_id = data.get("ticket_id")
        else:
            ticket_id = req.params.get("ticket_id")

        if not ticket_id:
            return {
                "status_code": 400,
                "body": {"error": "Missing ticket_id"}
            }

        # Query the ticket by ticket_id
        query = "SELECT * FROM c WHERE c.ticket_id = @ticket_id"
        params = [{"name": "@ticket_id", "value": ticket_id}]
        items = list(TicketsContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if not items:
            return {
                "status_code": 404,
                "body": {"error": "Ticket not found"}
            }

        ticket_doc = items[0]

        # Delete the document
        TicketsContainerProxy.delete_item(item=ticket_doc, partition_key=ticket_doc["ticket_id"])

        return {
            "status_code": 200,
            "body": {"result": "success"}
        }

    except Exception as e:
        logging.error(f"Error deleting ticket: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }