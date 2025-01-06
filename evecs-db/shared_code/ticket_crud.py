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
        
        # Create email validation schema
        email_schema = {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "format": "email"
                }
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

        # ---- 2) Check if email is already used for this event ----
        email_query = "SELECT * FROM c WHERE c.event_id = @event_id AND c.email = @email"
        email_params = [
            {"name": "@event_id", "value": body["event_id"]},
            {"name": "@email", "value": email}
        ]
        existing_email = list(TicketsContainerProxy.query_items(
            query=email_query,
            parameters=email_params,
            enable_cross_partition_query=True
        ))
        
        if existing_email:
            return {
                "status_code": 400,
                "body": {"error": f"Email '{email}' is already registered for this event"}
            }

        # ---- 3) Check if user_id exists ----
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

        # ---- 4) Check if event_id exists and get max_tick ----
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

        event = event_items[0]
        max_tickets = event.get('max_tick', 0)

        # ---- 5) Count existing tickets for this event ----
        tickets_count_query = "SELECT VALUE COUNT(1) FROM c WHERE c.event_id = @event_id"
        tickets_count = list(TicketsContainerProxy.query_items(
            query=tickets_count_query,
            parameters=event_params,
            enable_cross_partition_query=True
        ))[0]

        if tickets_count >= max_tickets:
            return {
                "status_code": 400,
                "body": {"error": f"Event has reached maximum ticket capacity ({max_tickets} tickets)"}
            }

        # ---- 6) Generate unique id/ticket_id ----
        generated_id = str(uuid.uuid4())

        # ---- 7) Build the ticket document ----
        ticket_doc = {
            "id": generated_id,           # Required by Cosmos DB
            "ticket_id": generated_id,    # Our application's identifier
            "user_id": body["user_id"],
            "event_id": body["event_id"],
            "email": email
        }

        # ---- 8) Validate with JSON Schema ----
        jsonschema.validate(instance=ticket_doc, schema=TICKET_SCHEMA)

        # ---- 9) Insert into Cosmos DB ----
        TicketsContainerProxy.create_item(ticket_doc)

        return {
            "status_code": 201,
            "body": {"result": "success", "ticket_id": generated_id}
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
    READ tickets by either:
    - event_id (to get all tickets for an event)
    - user_id (to get all events a user is subscribed to)
    - both (to check if a user is subscribed to a specific event)
    """
    try:
        if req.method == 'POST':
            data = req.get_json()
            event_id = data.get("event_id")
            user_id = data.get("user_id")
        else:  # 'GET'
            event_id = req.params.get("event_id")
            user_id = req.params.get("user_id")

        # Case 1: Get all tickets for an event
        if event_id and not user_id:
            query = "SELECT * FROM c WHERE c.event_id = @event_id"
            params = [{"name": "@event_id", "value": event_id}]
            items = list(TicketsContainerProxy.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))
            
            if not items:
                return {
                    "status_code": 404,
                    "body": {"error": "No tickets found for this event"}
                }
            
            return {
                "status_code": 200,
                "body": {
                    "event_id": event_id,
                    "ticket_count": len(items),
                    "tickets": items
                }
            }

        # Case 2: Get all events a user is subscribed to
        elif user_id and not event_id:
            query = "SELECT * FROM c WHERE c.user_id = @user_id"
            params = [{"name": "@user_id", "value": user_id}]
            items = list(TicketsContainerProxy.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))
            
            if not items:
                return {
                    "status_code": 404,
                    "body": {"error": "User is not subscribed to any events"}
                }
            
            return {
                "status_code": 200,
                "body": {
                    "user_id": user_id,
                    "subscription_count": len(items),
                    "subscriptions": items
                }
            }

        # Case 3: Check if user is subscribed to specific event
        elif event_id and user_id:
            query = "SELECT * FROM c WHERE c.event_id = @event_id AND c.user_id = @user_id"
            params = [
                {"name": "@event_id", "value": event_id},
                {"name": "@user_id", "value": user_id}
            ]
            
            items = list(TicketsContainerProxy.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))

            if not items:
                return {
                    "status_code": 404,
                    "body": {"error": "User is not subscribed to this event"}
                }

            return {
                "status_code": 200,
                "body": {
                    "subscribed": True,
                    "ticket": items[0]
                }
            }
        
        else:
            return {
                "status_code": 400,
                "body": {"error": "Must provide either event_id, user_id, or both"}
            }

    except Exception as e:
        logging.error(f"Error retrieving ticket(s): {str(e)}")
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

