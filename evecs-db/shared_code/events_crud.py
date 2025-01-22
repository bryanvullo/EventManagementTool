# shared_code/events_crud.py

import logging
import json
import jsonschema
import uuid
import os
import traceback
from datetime import timedelta, datetime
from dateutil import parser, tz
from urllib.parse import urlparse
import string
import random

from shared_code.ticket_crud import get_ticket

# Suppose we have the following global sets for validating tags/groups:
valid_tags = {"Lecture", "Society", "Leisure", "Sports", "Music", "Compulsory", "Optional", "Academic"} 
valid_groups = {"COMP3200", "COMP3227", "COMP3228", "COMP3269", "COMP3420", "COMP3666", 
                "COMP3229", "Badminton Society", "Chess Society", "Basketball Society", 
                "Standup Comedy Society", "ECS Society"}   

# Load event schema for validation, if needed for multiple functions
def load_event_schema():
    events_schema = os.path.join(os.path.dirname(__file__), '..', 'schemas/event.json')
    with open(events_schema) as f:
        print(f" TEST-ESCHEMA: Loading event schema from {events_schema}")
        return json.load(f)

EVENT_SCHEMA = load_event_schema()

def isoformat_now_plus(days_offset=0):
    """
    Return a string in the format: yyyy-MM-ddTHH:mm:ss.ffffffZ
    (up to 6 fractional digits), always in UTC.
    """
    dt_utc = datetime.now(tz=tz.UTC) + timedelta(days=days_offset)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def format_UTC_0(dt_str):
    """
    Accepts an ISO 8601 string from any timezone and converts it to UTC+0.
    Returns a string in yyyy-MM-ddTHH:mm:ssZ format (or with .ffffff if microseconds present).
    """
    try: 
        dt = parser.isoparse(dt_str)
        dt_utc = dt.astimezone(tz.UTC)
        # If microseconds are 0, use simpler format without them
        if dt_utc.microsecond == 0:
            return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, Exception) as e: 
        return { 
            "status_code": 500, 
            "body": {"error": f"Error converting date to GMT+0: {str(e)}" + "\n" + "Please use ISO 8601 format."}
        }


# TODO: Insert Alphanumeric code when event is created
def create_event(req, EventsContainerProxy, LocationsContainerProxy, UsersContainerProxy):
    """
    Create a new event, performing various business logic validations.
    """
    try:
        body = req.get_json()

        # ---- 0) Check mandatory fields  ----
        mandatory_fields = [
            "user_id",  # for creator_id
            "name",
            "groups",    # changed from 'group'
            "desc",
            "location_id",
            "room_id",
            "start_date",
            "end_date",
            "max_tick"
        ]
        missing_fields = [field for field in mandatory_fields if field not in body]

        if missing_fields:
            return {
                "status_code": 400,
                "body": {"error": f"Missing mandatory field(s): {missing_fields}"}
            }

        # ---- 1) start_date < end_date ----
        try:
            # First convert the dates to UTC format
            start_utc = format_UTC_0(body["start_date"])
            end_utc = format_UTC_0(body["end_date"])
            
            # Check if format_UTC_0 returned an error
            if isinstance(start_utc, dict) or isinstance(end_utc, dict):
                return {
                    "status_code": 400,
                    "body": {"error": "Invalid date format. Please use ISO 8601 (e.g. yyyy-MM-ddTHH:mm:ss.fffffffZ)"}
                }
            
            # Parse the UTC dates for comparison
            start_dt = parser.isoparse(start_utc)
            end_dt = parser.isoparse(end_utc)
            
            # Update the body with UTC formatted dates
            body["start_date"] = start_utc
            body["end_date"] = end_utc
            
        except ValueError as e:
            return {
                "status_code": 400,
                "body": {"error": f"Invalid date format: {str(e)}"}
            }

        if start_dt >= end_dt:
            return {
                "status_code": 400,
                "body": {"error": "Start date must be strictly before end date."}
            }

        # ---- 2) max_tick must be > 0  ----
        if not isinstance(body["max_tick"], (int, float)) or body["max_tick"] <= 0:
            return {
                "status_code": 400,
                "body": {"error": "max_tick must be a number greater than 0."}
            }

        # ---- 3) Check location_id is not null and exists in DB  ----
        location_id = body["location_id"]
        if not location_id:
            return {
                "status_code": 400,
                "body": {"error": "location_id cannot be null or empty."}
            }

        loc_query = "SELECT * FROM c WHERE c.location_id = @loc_id"
        loc_params = [{"name": "@loc_id", "value": location_id}]
        loc_items = list(LocationsContainerProxy.query_items(
            query=loc_query,
            parameters=loc_params,
            enable_cross_partition_query=True
        ))
        if not loc_items:
            return {
                "status_code": 400,
                "body": {"error": f"Location '{location_id}' not found in the database."}
            }

        # We'll store the location doc for future updates
        location_doc = loc_items[0]

        # ---- 5) Check that the creator_id (user_id) is valid AND authorized  ----
        user_id = body["user_id"]
        user_query = "SELECT * FROM c WHERE c.user_id = @u_id"
        user_params = [{"name": "@u_id", "value": user_id}]
        user_items = list(UsersContainerProxy.query_items(
            query=user_query,
            parameters=user_params,
            enable_cross_partition_query=True
        ))
        if not user_items:
            return {
                "status_code": 400,
                "body": {"error": f"User '{user_id}' not found in the users database."}
            }

        # ---5.5) Check user.auth == True
        user_doc = user_items[0]
        if not user_doc.get("auth", False):
            return {
                "status_code": 403,
                "body": {"error": f"User '{user_id}' is not authorized to create events."}
            }

        # ---- 6) Check that name and desc are strings  ----
        if not isinstance(body["name"], str):
            return {
                "status_code": 400,
                "body": {"error": "Event name must be a string."}
            }
        if not isinstance(body["desc"], str):
            return {
                "status_code": 400,
                "body": {"error": "Event description must be a string."}
            }

        # ---- 7) check for 'groups' ----
        for g in body["groups"]:
            if g not in valid_groups:
                return {
                    "status_code": 400,
                    "body": {"error": f"Invalid event group(s) '{body['groups']}'. Must be one of {list(valid_groups)}."}
                }

        # ---- 8) check for 'tags' (optional field) ----
        if "tags" in body and body["tags"]:
            if not isinstance(body["tags"], list):
                return {
                    "status_code": 400,
                    "body": {"error": "tags must be a list of strings."}
                }
            for t in body["tags"]:
                if not isinstance(t, str):
                    return {
                        "status_code": 400,
                        "body": {"error": "Each tag must be a string."}
                    }
                if t not in valid_tags:
                    return {
                        "status_code": 400,
                        "body": {"error": f"Invalid tag '{t}'. Must be one of {list(valid_tags)}."}
                    }

        # ---- 9) Check room_id exists in the location_doc and max_tick <= room.capacity----
        room_id = body["room_id"]

        # Retrieve rooms from the location_doc
        rooms = location_doc.get("rooms", [])
        selected_room = next((room for room in rooms if room["room_id"] == room_id), None)

        if not selected_room:
            return {
                "status_code": 404,
                "body": {"error": f"Room '{room_id}' not found in location '{location_id}'."}
            }
        
        room_capacity = selected_room.get("capacity", 0)
        if body["max_tick"] > room_capacity:
            return {
                "status_code": 400,
                "body": {
                    "error": f"max_tick ({body['max_tick']}) cannot exceed room capacity ({room_capacity})."
                }
            }
        
        # ---- 10) Check if the room is available for the event's time range with SQL query ----
        overlapping_events = list(
            EventsContainerProxy.query_items(
                query="""
                SELECT c.event_id, c.start_date, c.end_date
                FROM c
                WHERE c.location_id = @loc_id
                  AND c.room_id = @room_id
                  AND c.end_date > @start
                  AND c.start_date < @end
                """,
                parameters=[
                    {"name": "@loc_id", "value": body["location_id"]},
                    {"name": "@room_id", "value": body["room_id"]},
                    {"name": "@start",  "value": body["start_date"]},
                    {"name": "@end",    "value": body["end_date"]},
                ],
                enable_cross_partition_query=True
            )
        )

        if overlapping_events:  # not empty
            return {
                "status_code": 400,
                "body": {
                    "error": f"Room '{room_id}' is already booked during the requested time range."
                }
            }

    
        # ---- Build the event_doc after passing validations ----
        event_id = str(uuid.uuid4())
        # Generate 6 digit alphanumeric code for event validation
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        event_doc = {
            "id": event_id,
            "event_id": event_id,
            "code": code,
            "creator_id": [body["user_id"]],  # storing as a list
            "name": body["name"],
            "groups": body["groups"],
            "desc": body["desc"],
            "location_id": body["location_id"],
            "room_id": body["room_id"],
            "start_date": body["start_date"],
            "end_date": body["end_date"],
            "max_tick": body["max_tick"],
            "tags": body.get("tags", [])
        }

        # Add img_url only if it's provided
        if "img_url" in body:
            event_doc["img_url"] = body["img_url"]

        # ---- JSON Schema validation ----
        jsonschema.validate(instance=event_doc, schema=EVENT_SCHEMA)

        # ---- Insert the event into Cosmos DB (Events container) ----
        EventsContainerProxy.create_item(event_doc)

        # ---- Add the event to the location doc's "events_ids" array ----
        if "events_ids" not in location_doc:
            location_doc["events_ids"] = []
        location_doc["events_ids"].append({"event_id": event_doc["event_id"]})

        # ---- Also append to the room's "events_ids" ----
        for room in rooms:
            if room["room_id"] == room_id:
                if "events_ids" not in room:
                    room["events_ids"] = []
                room["events_ids"].append({"event_id": event_doc["event_id"]})
                break  # Room found and updated

        # ---- Update the location document in Cosmos (important!) ----
        LocationsContainerProxy.replace_item(item=location_doc, body=location_doc)

        return {
            "status_code": 201,
            "body": {"result": "success", "event_id": event_doc["event_id"]}
        }

    except jsonschema.exceptions.ValidationError as e:
        return {
            "status_code": 400,
            "body": {"error": f"JSON schema validation error: {str(e)}"}
        }

    except Exception as e:
        logging.error(f"Error creating event: {e}")
        logging.error(traceback.format_exc())
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

def delete_event(req, EventsContainerProxy, UsersContainerProxy, TicketsContainerProxy):
    """
    Deletes an event from the database and all associated tickets.
    Input (JSON):
      - event_id (required)
      - user_id (required)
    Output: { status_code: int, body: dict }
    """
    try:
        body = req.get_json()
        event_id = body.get("event_id")
        user_id = body.get("user_id")

        # Check required fields
        if not event_id or not user_id:
            return {
                "status_code": 400,
                "body": {"error": "Missing event_id or user_id"}
            }

        # Get the event
        event_query = "SELECT * FROM c WHERE c.event_id = @event_id"
        event_params = [{"name": "@event_id", "value": event_id}]
        event_items = list(EventsContainerProxy.query_items(
            query=event_query,
            parameters=event_params,
            enable_cross_partition_query=True
        ))

        if not event_items:
            return {
                "status_code": 404,
                "body": {"error": f"Event '{event_id}' not found."}
            }

        event_doc = event_items[0]

        # Check if user exists and get their auth status
        user_query = "SELECT * FROM c WHERE c.user_id = @user_id"
        user_params = [{"name": "@user_id", "value": user_id}]
        user_items = list(UsersContainerProxy.query_items(
            query=user_query,
            parameters=user_params,
            enable_cross_partition_query=True
        ))

        if not user_items:
            return {
                "status_code": 404,
                "body": {"error": f"User '{user_id}' not found."}
            }

        user_doc = user_items[0]

        # Allow deletion if user is either an admin (auth=true) or the creator of the event
        if not user_doc.get("auth", False) and user_id not in event_doc.get("creator_id", []):
            return {
                "status_code": 403,
                "body": {"error": "Unauthorized: You are not allowed to delete this event."}
            }

        # Get all tickets associated with this event
        ticket_query = "SELECT * FROM c WHERE c.event_id = @event_id"
        ticket_params = [{"name": "@event_id", "value": event_id}]
        tickets = list(TicketsContainerProxy.query_items(
            query=ticket_query,
            parameters=ticket_params,
            enable_cross_partition_query=True
        ))

        # Delete all associated tickets first
        for ticket in tickets:
            TicketsContainerProxy.delete_item(item=ticket["ticket_id"], partition_key=ticket["ticket_id"])

        # Delete the event
        EventsContainerProxy.delete_item(item=event_id, partition_key=event_id)

        return {
            "status_code": 200,
            "body": {
                "message": f"Event '{event_id}' and {len(tickets)} associated tickets deleted successfully."
            }
        }

    except Exception as e:
        logging.error(f"Error deleting event: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

# TODO: Update to reflect the changes in the create_event function
def update_event(req, EventsContainerProxy, LocationsContainerProxy, UsersContainerProxy):
    """
    Updates an existing event.
    Any admin user can update any event.
    Input (JSON):
      - event_id (required)
      - user_id (required)
      - any updatable fields
    Output: { status_code: int, body: dict }
    """
    try:
        body = req.get_json()
        event_id = body.get("event_id")
        user_id = body.get("user_id")

        if not event_id or not user_id:
            return {
                "status_code": 400,
                "body": {"error": "Missing event_id or user_id"}
            }

        # 1) Retrieve existing event
        query = "SELECT * FROM c WHERE c.event_id = @event_id"
        params = [{"name": "@event_id", "value": event_id}]
        items = list(EventsContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if not items:
            return {
                "status_code": 404,
                "body": {"error": "Event not found"}
            }

        event_doc = items[0]

        # 2) Check if user exists and is authorized
        user_query = "SELECT * FROM c WHERE c.user_id = @u_id"
        user_params = [{"name": "@u_id", "value": user_id}]
        user_items = list(UsersContainerProxy.query_items(
            query=user_query,
            parameters=user_params,
            enable_cross_partition_query=True
        ))

        if not user_items:
            return {
                "status_code": 400,
                "body": {"error": f"User '{user_id}' not found in users database."}
            }

        user_doc = user_items[0]
        if not user_doc.get("auth", False):
            return {
                "status_code": 403,
                "body": {"error": "Unauthorized: You are not allowed to update this event."}
            }

        # Handle tags update specifically
        if "tags" in body:
            # If tags is None or empty list, set to empty list
            if body["tags"] is None:
                event_doc["tags"] = []
            else:
                # Validate tags
                if not isinstance(body["tags"], list):
                    return {
                        "status_code": 400,
                        "body": {"error": "tags must be a list of strings."}
                    }
                for t in body["tags"]:
                    if not isinstance(t, str):
                        return {
                            "status_code": 400,
                            "body": {"error": "Each tag must be a string."}
                        }
                    if t not in valid_tags:
                        return {
                            "status_code": 400,
                            "body": {"error": f"Invalid tag '{t}'. Must be one of {list(valid_tags)}."}
                        }
                event_doc["tags"] = body["tags"]

        # Update other fields as before
        updatable_fields = [
            "name", "groups", "desc", "location_id", "room_id", "start_date",
            "end_date", "max_tick", "max_tick_pp", "img_url"
        ]
        for field in updatable_fields:
            if field in body:
                event_doc[field] = body[field]

        # 5) Now perform the validations on the updated doc

        # (i) start_date < end_date
        if "start_date" in event_doc and "end_date" in event_doc:
            try:
                start_utc = format_UTC_0(event_doc["start_date"])
                end_utc = format_UTC_0(event_doc["end_date"])

                # Check if format_UTC_0 returned an error
                if isinstance(start_utc, dict) or isinstance(end_utc, dict):
                    return {
                        "status_code": 400,
                        "body": {"error": "Invalid date format. Please use ISO 8601 (e.g. yyyy-MM-ddTHH:mm:ss.fffffffZ)"}
                    }
                
                # Parse the UTC dates for comparison
                start_dt = parser.isoparse(start_utc)
                end_dt = parser.isoparse(end_utc)

            except ValueError:
                return {
                    "status_code": 400,
                    "body": {"error": "Invalid date format. Use ISO 8601 (yyyy-MM-ddTHH:mm:ss.fffffffZ)."}
                }
            if start_dt >= end_dt:
                return {
                    "status_code": 400,
                    "body": {"error": "Start date must be strictly before end date."}
                }

        # (ii) max_tick and max_tick_pp > 0
        if "max_tick" in event_doc:
            if event_doc["max_tick"] <= 0:
                return {
                    "status_code": 400,
                    "body": {"error": "max_tick must be greater than 0."}
                }
        if "max_tick_pp" in event_doc:
            if event_doc["max_tick_pp"] <= 0:
                return {
                    "status_code": 400,
                    "body": {"error": "max_tick_pp must be greater than 0."}
                }

        # (iii) location_id not null & in DB
        if "location_id" in event_doc:
            if not event_doc["location_id"]:
                return {
                    "status_code": 400,
                    "body": {"error": "location_id cannot be null or empty."}
                }
            loc_query = "SELECT * FROM c WHERE c.location_id = @loc_id"
            loc_params = [{"name": "@loc_id", "value": event_doc["location_id"]}]
            loc_items = list(LocationsContainerProxy.query_items(
                query=loc_query,
                parameters=loc_params,
                enable_cross_partition_query=True
            ))
            if not loc_items:
                return {
                    "status_code": 400,
                    "body": {"error": f"Location '{event_doc['location_id']}' not found in DB."}
                }

        # (iv) img_url is a valid URL or empty
        if "img_url" in event_doc:
            img_url = event_doc["img_url"]
            if img_url and img_url.strip():  # only validate if not empty or whitespace
                try:
                    parsed = urlparse(img_url)
                    if not all([parsed.scheme, parsed.netloc]):
                        return {
                            "status_code": 400,
                            "body": {"error": "img_url must be a valid URL or empty."}
                        }
                except:
                    return {
                        "status_code": 400,
                        "body": {"error": "img_url must be a valid URL or empty."}
                    }

        # (v) name & desc must be strings
        if "name" in event_doc:
            if not isinstance(event_doc["name"], str):
                return {
                    "status_code": 400,
                    "body": {"error": "Event name must be a string."}
                }
        if "desc" in event_doc:
            if not isinstance(event_doc["desc"], str):
                return {
                    "status_code": 400,
                    "body": {"error": "Event description must be a string."}
                }

        # (vi) group check 
        if "groups" in event_doc:
            for g in event_doc["groups"]:
                if g not in valid_groups:
                    return {
                        "status_code": 400,
                        "body": {"error": f"Invalid event group '{event_doc['groups']}'. Must be one of {list(valid_groups)}."}
                    }
        
        #(vii) # tags check
        if "tags" in event_doc and event_doc["tags"]:
            if not isinstance(event_doc["tags"], list):
                return {
                    "status_code": 400,
                    "body": {"error": "tags must be a list of strings."}
                }
            for t in event_doc["tags"]:
                if not isinstance(t, str):
                    return {
                        "status_code": 400,
                        "body": {"error": "Each tag must be a string."}
                    }
                if t not in valid_tags:
                    return {
                        "status_code": 400,
                        "body": {"error": f"Invalid tag '{t}'. Must be one of {list(valid_tags)}."}
                    }


        # (viii) code check
        if "code" in event_doc:
            if not isinstance(event_doc["code"], str):
                return {
                    "status_code": 400,
                    "body": {"error": "code must be a string."}
                }

        # Add room validation after the existing validations
        # This should go after location_id validation (around line 483)
        if "room_id" in body or "location_id" in body or "max_tick" in body:
            # Get current location_id (either updated or original)
            location_id = event_doc["location_id"]
            
            # Fetch location document
            loc_query = "SELECT * FROM c WHERE c.location_id = @loc_id"
            loc_params = [{"name": "@loc_id", "value": location_id}]
            loc_items = list(LocationsContainerProxy.query_items(
                query=loc_query,
                parameters=loc_params,
                enable_cross_partition_query=True
            ))
            
            if not loc_items:
                return {
                    "status_code": 400,
                    "body": {"error": f"Location '{location_id}' not found in database."}
                }
            
            location_doc = loc_items[0]
            room_id = event_doc["room_id"]
            
            # Check if room exists in location
            rooms = location_doc.get("rooms", [])
            selected_room = next((room for room in rooms if room["room_id"] == room_id), None)
            
            if not selected_room:
                return {
                    "status_code": 404,
                    "body": {"error": f"Room '{room_id}' not found in location '{location_id}'."}
                }
            
            # Check room capacity
            room_capacity = selected_room.get("capacity", 0)
            if event_doc["max_tick"] > room_capacity:
                return {
                    "status_code": 400,
                    "body": {
                        "error": f"max_tick ({event_doc['max_tick']}) cannot exceed room capacity ({room_capacity})."
                    }
                }
            
            # Check for time conflicts if room or time changed
            if "room_id" in body or "start_date" in body or "end_date" in body:
                overlapping_events = list(
                    EventsContainerProxy.query_items(
                        query="""
                        SELECT c.event_id, c.start_date, c.end_date
                        FROM c
                        WHERE c.location_id = @loc_id
                          AND c.room_id = @room_id
                          AND c.end_date > @start
                          AND c.start_date < @end
                          AND c.event_id != @event_id
                        """,
                        parameters=[
                            {"name": "@loc_id", "value": location_id},
                            {"name": "@room_id", "value": room_id},
                            {"name": "@start", "value": event_doc["start_date"]},
                            {"name": "@end", "value": event_doc["end_date"]},
                            {"name": "@event_id", "value": event_id}
                        ],
                        enable_cross_partition_query=True
                    )
                )
                
                if overlapping_events:
                    return {
                        "status_code": 400,
                        "body": {
                            "error": f"Room '{room_id}' is already booked during the requested time range."
                        }
                    }


        # 6) Validate updated doc with JSON schema
        jsonschema.validate(instance=event_doc, schema=EVENT_SCHEMA)

        # 7) Replace (upsert) the updated document in DB
        EventsContainerProxy.replace_item(item=event_doc, body=event_doc)

        return {
            "status_code": 200,
            "body": {"result": "success"}
        }

    except jsonschema.exceptions.ValidationError as e:
        return {
            "status_code": 400,
            "body": {"error": f"Validation error: {str(e)}"}
        }
    except Exception as e:
        logging.error(f"Error updating event: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }


def get_event(req, EventsContainerProxy, TicketsContainerProxy, UsersContainerProxy, LocationsContainerProxy):
    """
    Retrieve events according to different input scenarios:

    1. No user_id and no event_id: Return all events.
    2. Only user_id: Check user exists, then return all events the user is subscribed to.
    3. Only event_id: Check event exists, then return that event.
    4. Both user_id and event_id: Check user is subscribed to the event, then return it.
    5. Only code: Check event exists, then return that event.
    """
    try:
        # Extract parameters from request
        if req.method == 'POST':
            data = req.get_json()
            event_id = data.get("event_id")
            user_id = data.get("user_id")
            code = data.get("code")
            ticket_id = data.get("ticket_id")
        else:  # 'GET'
            event_id = req.params.get("event_id")
            user_id = req.params.get("user_id")
            code = req.params.get("code")
            ticket_id = req.params.get("ticket_id")
        # Check if user exists in DB
        if user_id:
            user_query = "SELECT * FROM c WHERE c.user_id = @user_id"
            user_params = [{"name": "@user_id", "value": user_id}]
            user_items = list(UsersContainerProxy.query_items(
                query=user_query, 
                parameters=user_params, 
                enable_cross_partition_query=True
            ))
            if not user_items:
                return {"status_code": 404, "body": {"error": f"User '{user_id}' not found."}}
        
        # Check if event exists in DB - FIXED to use both id and event_id
        if event_id:
            event_query = """
                SELECT * FROM c 
                WHERE c.event_id = @event_id 
                OR c.id = @event_id
            """
            event_params = [{"name": "@event_id", "value": event_id}]
            event_items = list(EventsContainerProxy.query_items(
                query=event_query, 
                parameters=event_params, 
                enable_cross_partition_query=True
            )) 
            if not event_items:
                return {"status_code": 404, "body": {"error": f"Event '{event_id}' not found."}}

        # Scenario 1: No inputs => return all events
        if not user_id and not event_id:
            query = "SELECT * FROM c"
            items = list(EventsContainerProxy.query_items(query=query, enable_cross_partition_query=True))
            if not items:
                return {"status_code": 404, "body": {"error": "No events found."}}
            return {"status_code": 200, "body": {"event_count": len(items), "events": items}}

        # Scenario 2: Only user_id => return all events user is subscribed to - FIXED
        elif user_id and not event_id:
            # Fetch tickets for user
            ticket_query = "SELECT * FROM c WHERE c.user_id = @user_id"
            ticket_params = [{"name": "@user_id", "value": user_id}]
            tickets = list(TicketsContainerProxy.query_items(
                query=ticket_query,
                parameters=ticket_params,
                enable_cross_partition_query=True
            ))

            # Get events for each ticket
            subscribed_events = []
            for ticket in tickets:
                e_id = ticket.get("event_id")
                if e_id:
                    event_query = """
                        SELECT * FROM c 
                        WHERE c.event_id = @event_id 
                        OR c.id = @event_id
                    """
                    event_params = [{"name": "@event_id", "value": e_id}]
                    ev_items = list(EventsContainerProxy.query_items(
                        query=event_query,
                        parameters=event_params,
                        enable_cross_partition_query=True
                    ))
                    if ev_items:
                        subscribed_events.append(ev_items[0])

            return {
                "status_code": 200, 
                "body": {
                    "user_id": user_id,
                    "event_count": len(subscribed_events),
                    "events": subscribed_events
                }
            }

        # Scenario 3: Only event_id => return that event - FIXED
        elif event_id and not user_id:
            event_query = """
                SELECT * FROM c 
                WHERE c.event_id = @event_id 
                OR c.id = @event_id
            """
            event_params = [{"name": "@event_id", "value": event_id}]
            items = list(EventsContainerProxy.query_items(
                query=event_query,
                parameters=event_params,
                enable_cross_partition_query=True
            ))
            if not items:
                return {"status_code": 404, "body": {"error": f"Event '{event_id}' not found."}}
            
            # Add location name and room name
            event = items[0]
            location_id = event.get("location_id")
            room_id = event.get("room_id")
            if location_id:
                location_items = list(LocationsContainerProxy.query_items(
                    query="SELECT * FROM c WHERE c.location_id = @loc_id",
                    parameters=[{"name": "@loc_id", "value": location_id}],
                    enable_cross_partition_query=True
                ))
                if location_items:
                    location = location_items[0]
                    event["location_name"] = location.get("location_name")
                    if room_id and "rooms" in location:
                        for room in location["rooms"]:
                            if room["room_id"] == room_id:
                                event["room_name"] = room.get("room_name")
                                break

            return {"status_code": 200, "body": event}

        # Scenario 4: Both user_id and event_id => return the event if user is subscribed
        elif user_id and event_id:
            # Check subscription with get_ticket
            ticket_req = type('', (), {})()
            ticket_req.method = 'GET'
            ticket_req.params = {"user_id": user_id, "event_id": event_id}
            ticket_res = get_ticket(ticket_req, TicketsContainerProxy)
            if ticket_res["status_code"] != 200:
                return {"status_code": ticket_res["status_code"], "body": ticket_res["body"]}
            # Retrieve the event
            event_query = "SELECT * FROM c WHERE c.event_id = @event_id"
            event_params = [{"name": "@event_id", "value": event_id}]
            event_items = list(EventsContainerProxy.query_items(
                query=event_query, 
                parameters=event_params,
                enable_cross_partition_query=True
            ))
            if not event_items:
                return {"status_code": 404, "body": {"error": f"Event '{event_id}' not found."}}

            # Add location name and room name
            event = event_items[0]
            location_id = event.get("location_id")
            room_id = event.get("room_id")
            if location_id:
                location_items = list(LocationsContainerProxy.query_items(
                    query="SELECT * FROM c WHERE c.location_id = @loc_id",
                    parameters=[{"name": "@loc_id", "value": location_id}],
                    enable_cross_partition_query=True
                ))
                if location_items:
                    location = location_items[0]
                    event["location_name"] = location.get("location_name")
                    if room_id and "rooms" in location:
                        for room in location["rooms"]:
                            if room["room_id"] == room_id:
                                event["room_name"] = room.get("room_name")
                                break

            return {"status_code": 200, "body": event}
        
        # Scenario 5: Only code => return that event
        elif code:
            query = "SELECT * FROM c WHERE c.code = @code"
            params = [{"name": "@code", "value": code}]
            items = list(EventsContainerProxy.query_items(
                query=query, parameters=params, enable_cross_partition_query=True))
            if not items:
                return {"status_code": 404, "body": {"error": f"Event with code '{code}' not found."}}
            return {"status_code": 200, "body": items[0]}

        # Scenario 6: Only ticket_id => return that event
        elif ticket_id:
            query = "SELECT * FROM c WHERE c.ticket_id = @ticket_id"
            params = [{"name": "@ticket_id", "value": ticket_id}]
            items = list(EventsContainerProxy.query_items(
                query=query, parameters=params, enable_cross_partition_query=True))
            if not items:
                return {"status_code": 404, "body": {"error": f"Event with ticket_id '{ticket_id}' not found."}}
            return {"status_code": 200, "body": items[0]}

        else:
            return {"status_code": 400, "body": {"error": "Invalid combination of inputs."}}

    except Exception as e:
        logging.error(f"Error in get_event: {str(e)}")
        return {"status_code": 500, "body": {"error": "Internal Server Error"}}


def grant_event_adminship(req, EventsContainerProxy):
    """
    Original logic for granting adminship.
    """
    try:
        data = req.get_json()
        creator_id = data.get("creator_id")
        new_admin_id = data.get("new_admin_id")
        event_id = data.get("event_id")

        # Basic input check
        if not creator_id or not new_admin_id or not event_id:
            return {
                "status_code": 400,
                "body": {"error": "Missing one of: creator_id, new_admin_id, event_id"}
            }

        # Fetch the event
        query = "SELECT * FROM c WHERE c.event_id = @event_id"
        params = [{"name": "@event_id", "value": event_id}]
        items = list(EventsContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if not items:
            return {
                "status_code": 404,
                "body": {"error": "Event not found"}
            }

        event_doc = items[0]

        # Check if the caller is in the creator_id list
        if creator_id not in event_doc["creator_id"]:
            return {
                "status_code": 403,
                "body": {"error": "Unauthorized: You are not an admin of this event."}
            }

        # Add the new admin if they are not already in the list
        if new_admin_id not in event_doc["creator_id"]:
            event_doc["creator_id"].append(new_admin_id)

            # Validate the updated doc (optional but recommended)
            jsonschema.validate(instance=event_doc, schema=EVENT_SCHEMA)

            # Update in DB
            EventsContainerProxy.replace_item(item=event_doc, body=event_doc)

        return {
            "status_code": 200,
            "body": {"result": f"User {new_admin_id} now has admin rights on event {event_id}"}
        }
    except jsonschema.exceptions.ValidationError as e:
        return {
            "status_code": 400,
            "body": {"error": f"Validation error: {str(e)}"}
        }
    except Exception as e:
        logging.error(f"Error granting adminship: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

# TODO: Update to reflect the changes in the create_event function
# TODO: Groups is now a list :(
def make_calendar(req, EventsContainerProxy, LocationsContainerProxy):
    """
    Original make_calendar logic.
    """
    try:
        body = req.get_json()

        start_date_str = body.get("start_date")
        end_date_str = body.get("end_date")
        filters = body.get("filters", {})

        if not start_date_str or not end_date_str:
            return {
                "status_code": 400,
                "body": {"error": "start_date and end_date are required."}
            }

        # Validate date format
        try:
            start_dt = parser.isoparse(start_date_str)
            end_dt   = parser.isoparse(end_date_str)
        except ValueError:
            return {
                "status_code": 400,
                "body": {"error": "Invalid date format. Must be ISO8601."}
            }

        # Check start < end
        if start_dt >= end_dt:
            return {
                "status_code": 400,
                "body": {"error": "start_date must be strictly before end_date."}
            }

        # (Filters) logic the same
        # ...
        # We'll do partial snippet here; same as your original.

        # Validate filters like tags, group, desc, location_id, max_tick, max_tick_pp
        # (A) tags
        if "tags" in filters and filters["tags"]:
            if not isinstance(filters["tags"], list):
                return {
                    "status_code": 400,
                    "body": {"error": "tags must be a list of strings."}
                }
            for t in filters["tags"]:
                if not isinstance(t, str):
                    return {
                        "status_code": 400,
                        "body": {"error": "Each tag must be a string."}
                    }
                if t not in valid_tags:
                    return {
                        "status_code": 400,
                        "body": {"error": f"Invalid tag '{t}'. Must be in {list(valid_tags)}."}
                    }
        # (B) group in valid_groups
        if "group" in filters:
            if not isinstance(filters["group"], str):
                return {
                    "status_code": 400,
                    "body": {"error": "group must be a string."}
                }
            if filters["group"] not in valid_groups:
                return {
                    "status_code": 400,
                    "body": {"error": f"Invalid event group '{filters['group']}'. Allowed: {list(valid_groups)}."}
                }

        # (C) desc => must be a string
        if "desc" in filters:
            if not isinstance(filters["desc"], str):
                return {
                    "status_code": 400,
                    "body": {"error": "desc must be a string."}
                }

        # (D) location_id => must exist in DB
        if "location_id" in filters and filters["location_id"]:
            if not isinstance(filters["location_id"], str) or not filters["location_id"].strip():
                return {
                    "status_code": 400,
                    "body": {"error": "location_id must be a non-empty string."}
                }
            loc_query = "SELECT * FROM c WHERE c.location_id = @loc_id"
            loc_params = [{"name": "@loc_id", "value": filters["location_id"]}]
            loc_items = list(LocationsContainerProxy.query_items(
                query=loc_query,
                parameters=loc_params,
                enable_cross_partition_query=True
            ))
            if not loc_items:
                return {
                    "status_code": 400,
                    "body": {"error": f"location_id '{filters['location_id']}' not found in DB."}
                }

        # (E) max_tick => must be > 0
        if "max_tick" in filters:
            if not isinstance(filters["max_tick"], (int, float)) or filters["max_tick"] <= 0:
                return {
                    "status_code": 400,
                    "body": {"error": "max_tick must be a positive number."}
                }

        # Query events in date range
        query = """
        SELECT * FROM c
         WHERE c.start_date >= @start_lex
           AND c.end_date   <= @end_lex
        """
        parameters = [
            {"name": "@start_lex", "value": start_date_str},
            {"name": "@end_lex",   "value": end_date_str}
        ]
        events_in_range = list(EventsContainerProxy.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))

        # Filtering logic as in your snippet
        def event_matches_filter(ev_doc, filters):
            # tags => exact array match
            if "tags" in filters and filters["tags"]:
                if ev_doc.get("tags", []) != filters["tags"]:
                    return False
            # group => exact match
            if "group" in filters:
                if ev_doc.get("group") != filters["group"]:
                    return False
            # desc => substring
            if "desc" in filters:
                if filters["desc"] not in ev_doc.get("desc", ""):
                    return False
            # location_id
            if "location_id" in filters:
                if ev_doc.get("location_id") != filters["location_id"]:
                    return False
            # max_tick
            if "max_tick" in filters:
                if ev_doc.get("max_tick") != filters["max_tick"]:
                    return False
            return True

        filtered_events = []
        for ev in events_in_range:
            if event_matches_filter(ev, filters):
                filtered_events.append(ev)

        return {
            "status_code": 200,
            "body": {"results": filtered_events}
        }

    except Exception as e:
        logging.error(f"Error in make_calendar: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }
    
def get_valid_groups_crud():
    """
    Returns all valid groups defined here.
    """
    try:
        return {
            "status_code": 200,
            "body": {"groups": list(valid_groups)}
        }
    except Exception as e:
        logging.error(f"Error in get_groups: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }
    

def get_valid_tags_crud():
    """
    Returns all valid tags defined here.
    """
    try:
        return {
            "status_code": 200,
            "body": {"tags": list(valid_tags)}
        }
    except Exception as e:
        logging.error(f"Error in get_tags: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }