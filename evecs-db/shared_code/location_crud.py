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

        return {
            "status_code": 200,
            "body": {
                "message": "Successfully retrieved location groups",
                "locations": locations
            }
        }

    except Exception as e:
        logging.error(f"Error retrieving location groups: {str(e)}")
        return {
            "status_code": 500,
            "body": {"error": "Internal Server Error"}
        }

