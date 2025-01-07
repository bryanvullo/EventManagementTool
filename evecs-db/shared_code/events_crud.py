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

from shared_code.ticket_crud import get_ticket

# Suppose we have the following global sets for validating tags/groups:
valid_tags = {"lecture", "society", "leisure", "sports", "music"}  # TBD
valid_groups = {"COMP3200", "COMP3227", "COMP3228", "COMP3269", "COMP3420", "COMP3666", "COMP3229", "Sports"}          # TBD

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


# TODO: Return 400 if the selected room is already booked for the event's time range
# TODO: Return 400 if the event's max_tick exceeds the room's capacity
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
            "max_tick",
            "img_url"   # added as per schema
        ]
        missing_fields = [field for field in mandatory_fields if field not in body]

        if missing_fields:
            return {
                "status_code": 400,
                "body": {"error": f"Missing mandatory field(s): {missing_fields}"}
            }

        # ---- 1) start_date < end_date ----
        try:
            start_dt = parser.isoparse(body["start_date"])
            end_dt = parser.isoparse(body["end_date"])
        except ValueError:
            return {
                "status_code": 400,
                "body": {"error": "Invalid date format. Please use ISO 8601 (e.g. yyyy-MM-ddTHH:mm:ss.fffffffZ)"}
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

        # ---- 4) Check img_url is a URL (or can be null/empty)  ----
        img_url = body.get("img_url", "")
        if img_url:  # only validate if non-empty
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
        if not isinstance(body["groups"], list):
            return {
                "status_code": 400,
                "body": {"error": "groups must be an array of strings."}
            }
        for group in body["groups"]:
            if group not in valid_groups:
                return {
                    "status_code": 400,
                    "body": {"error": f"Invalid event group '{group}'. Must be one of {list(valid_groups)}."}
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

        # ---- 9) Check room_id exists in the location_doc ----
        room_id = body["room_id"]

        # Retrieve rooms from the location_doc
        rooms = location_doc.get("rooms", [])
        selected_room = next((room for room in rooms if room["room_id"] == room_id), None)

        if not selected_room:
            return {
                "status_code": 400,
                "body": {"error": f"Room '{room_id}' not found in location '{location_id}'."}
            }

        # Ensure max_tick <= room.capacity
        room_capacity = selected_room.get("capacity", 0)
        if body["max_tick"] > room_capacity:
            return {
                "status_code": 400,
                "body": {
                    "error": f"max_tick ({body['max_tick']}) cannot exceed room capacity ({room_capacity})."
                }
            }

        # ---- Build the event_doc after passing validations ----
        event_id = str(uuid.uuid4())
        event_doc = {
            "id": event_id,
            "event_id": event_id,
            "creator_id": [body["user_id"]],  # storing as a list
            "name": body["name"],
            "groups": body["groups"],
            "desc": body["desc"],
            "location_id": body["location_id"],
            "room_id": body["room_id"],
            "start_date": body["start_date"],
            "end_date": body["end_date"],
            "max_tick": body["max_tick"],
            "tags": body.get("tags", []),
            "img_url": body["img_url"]
        }

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

def delete_event(req, EventsContainerProxy):
    """
    Original delete_event logic.
    """
    try:
        if req.method == 'POST':
            data = req.get_json()
            event_id = data.get("event_id")
            user_id = data.get("user_id")
        else:
            event_id = req.params.get("event_id")
            user_id = req.params.get("user_id")

        if not event_id or not user_id:
            return {
                "status_code": 400,
                "body": {"error": "Missing event_id or user_id"}
            }

        # Query the event by event_id
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

        # Check if user is in the admin array
        if user_id not in event_doc["creator_ids"]:
            return {
                "status_code": 403,
                "body": {"error": "Unauthorized: You are not an admin of this event."}
            }

        # Validate group
        event_group = event_doc.get("group")
        if event_group not in valid_groups:
            return {
                "status_code": 400,
                "body": {"error": f"Event group '{event_group}' is not in {list(valid_groups)}."}
            }

        return {
            "status_code": 200,
            "body": {"result": "success"}
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
    Same logic as your original update_event function.
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

        # 2) Check user ownership 
        if user_id not in event_doc["creator_id"]:
            return {
                "status_code": 403,
                "body": {"error": "Unauthorized: You are not the creator of this event."}
            }

        # 3) Ensure the user is valid & authorized (user.auth == True)
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
                "body": {"error": f"User '{user_id}' is not authorized to update events."}
            }

        # 4) Update only the fields provided
        updatable_fields = [
            "name", "group", "desc", "location_id", "start_date",
            "end_date", "max_tick", "max_tick_pp", "tags", "img_url"
        ]
        for field in updatable_fields:
            if field in body:
                event_doc[field] = body[field]

        # 5) Now perform the validations on the updated doc

        # (i) start_date < end_date
        if "start_date" in event_doc and "end_date" in event_doc:
            try:
                start_dt = parser.isoparse(event_doc["start_date"])
                end_dt = parser.isoparse(event_doc["end_date"])
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
            if img_url:  # if not empty
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
            if not isinstance(event_doc["groups"], list):
                return {
                    "status_code": 400,
                    "body": {"error": "groups must be an array of strings."}
                }
            for group in event_doc["groups"]:
                if group not in valid_groups:
                    return {
                        "status_code": 400,
                        "body": {"error": f"Invalid event group '{group}'. Must be one of {list(valid_groups)}."}
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


def get_event(req, EventsContainerProxy, TicketsContainerProxy, UsersContainerProxy):
    """
    Retrieve events according to different input scenarios:

    1. No user_id and no event_id: Return all events.
    2. Only user_id: Check user exists, then return all events the user is subscribed to.
    3. Only event_id: Check event exists, then return that event.
    4. Both user_id and event_id: Check user is subscribed to the event, then return it.
    """
    try:
        # Extract parameters from request
        if req.method == 'POST':
            data = req.get_json()
            event_id = data.get("event_id")
            user_id = data.get("user_id")
        else:  # 'GET'
            event_id = req.params.get("event_id")
            user_id = req.params.get("user_id")

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
        
        # check if event exists in DB
        if event_id:
            event_query = "SELECT * FROM c WHERE c.event_id = @event_id"
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

        # Scenario 2: Only user_id => return all events user is subscribed to using get_ticket
        elif user_id and not event_id:
            # Fetch tickets for user
            ticket_req = type('', (), {})()
            ticket_req.method = 'GET'
            ticket_req.params = {"user_id": user_id}
            ticket_res = get_ticket(ticket_req, TicketsContainerProxy)
            if ticket_res["status_code"] != 200:
                return {"status_code": ticket_res["status_code"], "body": ticket_res["body"]}

            # Use event_ids from tickets to retrieve events
            subscribed_events = []
            for t in ticket_res["body"]["subscriptions"]:
                e_id = t.get("event_id")
                if not e_id:
                    return {"status_code": 400, "body": {"error": "Invalid ticket data: missing event_id."}}
                event_query = "SELECT * FROM c WHERE c.event_id = @event_id"
                event_params = [{"name": "@event_id", "value": e_id}]
                ev_items = list(EventsContainerProxy.query_items(
                    query=event_query, 
                    parameters=event_params,
                    enable_cross_partition_query=True
                ))
                if ev_items:
                    subscribed_events.append(ev_items[0])

            if not subscribed_events: subscribed_events = [] # empty list if no events better than 404
            return {"status_code": 200, "body": {"user_id": user_id, "event_count": len(subscribed_events), "events": subscribed_events}}

        # Scenario 3: Only event_id => return that event
        elif event_id and not user_id:
            query = "SELECT * FROM c WHERE c.event_id = @event_id"
            params = [{"name": "@event_id", "value": event_id}]
            items = list(EventsContainerProxy.query_items(
                query=query, 
                parameters=params, 
                enable_cross_partition_query=True
            ))
            if not items:
                return {"status_code": 404, "body": {"error": f"Event '{event_id}' not found."}}
            return {"status_code": 200, "body": items[0]}

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
            return {"status_code": 200, "body": event_items[0]}

        else:
            return {"status_code": 400, "body": {"error": "Invalid combination of user_id and event_id."}}

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