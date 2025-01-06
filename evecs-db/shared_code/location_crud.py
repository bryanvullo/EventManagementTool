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

# TODO: Should also return the room details (capacity and stuff)
def get_location_groups(req, LocationsContainerProxy):
    """
    Returns all location_ids and their associated groups from the locations container.
    """
    try:
        # Handle both GET and POST methods
        if req.method == 'POST':
            # For POST, get any filters from request body
            body = req.get_json()
            location_id = body.get("location_id")
        else:
            # For GET, check query parameters
            location_id = req.params.get("location_id")

        # Base query
        if location_id:
            # If location_id provided, filter for specific location
            query = "SELECT c.location_id, c.groups FROM c WHERE c.location_id = @location_id"
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
            # Get all locations if no specific id provided
            query = "SELECT c.location_id, c.groups FROM c"
            locations = list(LocationsContainerProxy.query_items(
                query=query,
                enable_cross_partition_query=True
            ))

        # Transform the data into the required format
        location_list = [{"location_id": loc["location_id"]} for loc in locations]
        
        # Collect all unique groups across all locations
        all_groups = set()
        for loc in locations:
            if "groups" in loc and isinstance(loc["groups"], list):
                all_groups.update(loc["groups"])

        return {
            "status_code": 200,
            "body": {
                "message": "Successfully retrieved location groups",
                "locations": location_list,
                "groups": sorted(list(all_groups))  # Convert set to sorted list
            }
        }

    except Exception as e:
        logging.error(f"Error retrieving location groups: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

# Returns room details given a specified location_id
def get_rooms_from_location_id(req, LocationsContainerProxy):
    """
    Given a location_id, return the list of rooms (and their properties) from that building.
    
    Request can be GET or POST:
      - If GET, location_id is read from query string (?location_id=xxxx)
      - If POST, location_id is read from JSON body.
    """
    try:
        # Handle both GET and POST methods
        if req.method == 'POST':
            body = req.get_json()
            location_id = body.get("location_id")
        else:  # GET
            location_id = req.params.get("location_id")

        if not location_id:
            return {
                "status_code": 400,
                "body": {"error": "Missing 'location_id' parameter"}
            }

        # Query the database for the given location_id
        query = "SELECT * FROM c WHERE c.location_id = @location_id"
        params = [{"name": "@location_id", "value": location_id}]
        locations = list(
            LocationsContainerProxy.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            )
        )

        if not locations:
            return {
                "status_code": 404,
                "body": {"error": f"Location '{location_id}' not found"}
            }
        
        # We expect exactly one document per location_id (assuming your design)
        location_doc = locations[0]
        rooms = location_doc.get("rooms", [])

        return {
            "status_code": 200,
            "body": {
                "message": f"Successfully retrieved rooms for location '{location_id}'",
                "rooms": rooms  # Return the list of room objects as-is
            }
        }

    except Exception as e:
        logging.error(f"Error retrieving rooms from building: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }