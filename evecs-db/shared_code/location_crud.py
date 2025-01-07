# shared_code/location_crud.py

import logging
import json
import jsonschema
import uuid
import os
import traceback
from datetime import timedelta, datetime
from dateutil import parser, tz
from urllib.parse import urlparse

# Load location schema for validation, if needed for multiple functions
def load_location_schema():
    events_schema = os.path.join(os.path.dirname(__file__), '..', 'schemas/location.json')
    with open(events_schema) as f:
        return json.load(f)

location_schema = load_location_schema()

# TODO: Should also return the room details (capacity and stuff)
def get_location_groups(req, LocationsContainerProxy):
    """
    Returns all locations and their associated groups from the locations container.
    Returns:
        - List of all locations with their full details
        - List of all unique groups across all locations
    """
    try:
        # Handle both GET and POST methods
        if req.method == 'POST':
            body = req.get_json()
            location_id = body.get("location_id")
        else:
            location_id = req.params.get("location_id")

        # Base query - now selecting entire document
        if location_id:
            query = "SELECT * FROM c WHERE c.location_id = @location_id"
            params = [{"name": "@location_id", "value": location_id}]
            locations = list(LocationsContainerProxy.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))
            
            if not locations:
                return {
                    "status_code": 404,
                    "body": {"error": "Location not found"}
                }
        else:
            query = "SELECT * FROM c"
            locations = list(LocationsContainerProxy.query_items(
                query=query,
                enable_cross_partition_query=True
            ))

        # Transform the data to include full location objects
        location_list = []
        all_groups = set()

        for loc in locations:
            # Remove internal Cosmos DB id if present
            if 'id' in loc:
                del loc['id']
            
            # Add location to list
            location_obj = {
                "location_id": loc["location_id"],
                "location_name": loc["location_name"],
                "events_ids": loc.get("events_ids", []),
                "capacity": loc.get("capacity", 0),
                "rooms": []
            }

            # Add rooms if present
            if "rooms" in loc and isinstance(loc["rooms"], list):
                for room in loc["rooms"]:
                    room_obj = {
                        "room_id": room.get("room_id", ""),
                        "room_name": room.get("room_name", ""),
                        "capacity": room.get("capacity", 0),
                        "description": room.get("description", ""),
                        "events_ids": room.get("events_ids", [])
                    }
                    location_obj["rooms"].append(room_obj)

            location_list.append(location_obj)
            
            # Collect groups from events if present
            if "events_ids" in loc:
                for event in loc.get("events_ids", []):
                    if "group" in event:
                        all_groups.add(event["group"])

            # Also check rooms for events with groups
            for room in loc.get("rooms", []):
                for event in room.get("events_ids", []):
                    if "group" in event:
                        all_groups.add(event["group"])

        return {
            "status_code": 200,
            "body": {
                "message": "Successfully retrieved location groups",
                "locations": location_list,
                "groups": sorted(list(all_groups))
            }
        }

    except Exception as e:
        logging.error(f"Error retrieving location groups: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

# TODO: This needs some really bad fixing
def create_location(req, LocationsContainerProxy, location_schema=location_schema):
    """
    Creates a new location document in the Locations container.
    Required fields (per location_schema or as listed): 
      [
         "location_id",
         "location_name",
         "events_ids",
         "rooms"
      ]
    Additional checks:
      - location_id and location_name must be unique across all documents
      - Validate rooms array structure: room_id must be unique in that location
      - Type checks if no location_schema is provided, or schema-based validation if location_schema is available
      If successful, returns 202.
    """
    try:
        body = req.get_json()

        # 1 Quick mandatory field check
        required_fields = ["location_name", "rooms"]
        missing = [f for f in required_fields if f not in body]
        if missing:
            return {
                "status_code": 400,
                "body": {"error": f"Missing mandatory field(s): {missing}"}
            }
        
        for room in body["rooms"]:
            if "room_name" not in room or "capacity" not in room:
                return {
                    "status_code": 400,
                    "body": {"error": "Missing mandatory field 'room_name' in rooms."}
                }
    
        # 2 Check uniqueness of location_name across all docs
        check_query = (
            "SELECT * FROM c "
            "WHERE c.location_name = @loc_name"
        )
        check_params = [
            {"name": "@loc_name", "value": body["location_name"]}
        ]
        existing_docs = list(
            LocationsContainerProxy.query_items(
                query=check_query,
                parameters=check_params,
                enable_cross_partition_query=True
            )
        )
        if existing_docs:
            return {
                "status_code": 400,
                "body": {
                    "error": (
                        f"Location with name '{body['location_name']}' already exists."
                    )
                }
            }

        # 3 Validate the document against the schema
        try:
            jsonschema.validate(instance=body, schema=location_schema)
        except jsonschema.exceptions.ValidationError as ve:
            return {
                "status_code": 400,
                "body": {"error": f"JSON schema validation error: {str(ve)}"}
            }

        # 4 All checks pass -> create new location
        location_id = str(uuid.uuid4())

        # 4.1 Generate unique IDs for each room
        rooms = body["rooms"]
        for room in rooms:
            room["room_id"] = str(uuid.uuid4())

        #4.2 Create the new document
        new_doc = {
            "id": str(uuid.uuid4()),
            "location_id": location_id,
            "location_name": body["location_name"],
            "events_ids": body["events_ids"],
            "rooms": rooms
        }
        # 4.3 Include additional fields
        for key, val in body.items():
            if key not in new_doc and key in location_schema["properties"]:
                new_doc[key] = val

        LocationsContainerProxy.create_item(new_doc)

        return {
            "status_code": 202,
            "body": {
                "message": "Location created successfully.",
                "location_id": location_id
            }
        }

    except Exception as e:
        logging.error(f"Error creating location: {e}")
        logging.error(traceback.format_exc())
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }


def delete_location(req, LocationsContainerProxy):
    """
    Deletes a location from the container by location_id.
    - If location exists, deletes it and returns 200/201
    - Otherwise returns 404
    """
    try:
        # Handle both GET and POST methods for location_id
        if req.method == 'POST':
            body = req.get_json()
            location_id = body.get("location_id")
        else:
            location_id = req.params.get("location_id")

        if not location_id:
            return {
                "status_code": 400,
                "body": {"error": "Missing 'location_id' parameter"}
            }

        # Fetch the doc
        query = "SELECT * FROM c WHERE c.location_id = @loc_id"
        params = [{"name": "@loc_id", "value": location_id}]
        docs = list(LocationsContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if not docs:
            return {
                "status_code": 404,
                "body": {"error": f"Location '{location_id}' not found"}
            }

        doc_to_delete = docs[0]
        # Cosmos DB requires the partition key (often location_id) and the internal 'id'
        item_id = doc_to_delete["id"]
        partition_key = doc_to_delete["location_id"]  # or whatever your partition key is

        LocationsContainerProxy.delete_item(item=item_id, partition_key=partition_key)

        return {
            "status_code": 200,
            "body": {"message": f"Location '{location_id}' deleted successfully."}
        }

    except Exception as e:
        logging.error(f"Error deleting location: {e}")
        logging.error(traceback.format_exc())
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }


def read_location(req, LocationsContainerProxy):
    """
    Reads a location document by location_id. 
    - If the location exists, return the full document with 200/201
    - Otherwise return 404
    """
    try:
        if req.method == 'POST':
            body = req.get_json()
            location_id = body.get("location_id")
        else:
            location_id = req.params.get("location_id")

        if not location_id:
            return {
                "status_code": 400,
                "body": {"error": "Missing 'location_id' parameter"}
            }

        query = "SELECT * FROM c WHERE c.location_id = @loc_id"
        params = [{"name": "@loc_id", "value": location_id}]
        docs = list(LocationsContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if not docs:
            return {
                "status_code": 404,
                "body": {"error": f"Location '{location_id}' not found."}
            }

        # Return the location document
        location_doc = docs[0]
        return {
            "status_code": 200,
            "body": {
                "message": f"Location '{location_id}' found.",
                "location": location_doc
            }
        }

    except Exception as e:
        logging.error(f"Error reading location: {e}")
        logging.error(traceback.format_exc())
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }


def edit_location(req, LocationsContainerProxy, location_schema=location_schema):
    """
    Edits an existing location document. Required fields in the location schema are:
       ["location_id", "location_name", "events_ids", "rooms"]
    - If no fields are specified in the body, return 400
    - Validate each updated field is compatible with the location JSON schema
    - If document not found, return 404
    - Otherwise upsert/replace the location and return 200/201
    """
    try:
        body = req.get_json()
        if not body or not isinstance(body, dict):
            return {
                "status_code": 400,
                "body": {"error": "Request body must be a valid JSON object."}
            }

        # We must have at least location_id to find the document
        location_id = body.get("location_id")
        if not location_id:
            return {
                "status_code": 400,
                "body": {"error": "Missing 'location_id' field to identify the document to edit."}
            }

        # Retrieve the existing doc
        query = "SELECT * FROM c WHERE c.location_id = @loc_id"
        params = [{"name": "@loc_id", "value": location_id}]
        docs = list(LocationsContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        if not docs:
            return {
                "status_code": 404,
                "body": {"error": f"Location '{location_id}' not found, cannot edit."}
            }

        location_doc = docs[0]

        # Merge updates from body into the existing doc
        # We skip "id" because that's the internal Cosmos ID
        # But we allow all other fields from the location schema
        updatable_keys = ["location_id", "location_name", "events_ids", "rooms"]
        updated_any_field = False

        for key in updatable_keys:
            if key in body:
                location_doc[key] = body[key]
                updated_any_field = True

        if not updated_any_field:
            return {
                "status_code": 400,
                "body": {"error": "No updatable fields specified in request body."}
            }

        # If we have a location schema, validate with jsonschema
        try:
            jsonschema.validate(instance=location_doc, schema=location_schema)
        except jsonschema.exceptions.ValidationError as ve:
            return {
                "status_code": 400,
                "body": {"error": f"JSON schema validation error: {str(ve)}"}
            }

        # Replace (upsert) the doc in the database
        LocationsContainerProxy.replace_item(item=location_doc, body=location_doc)

        return {
            "status_code": 200,
            "body": {
                "message": f"Location '{location_id}' updated successfully.",
                "location": location_doc
            }
        }

    except Exception as e:
        logging.error(f"Error editing location: {e}")
        logging.error(traceback.format_exc())
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }