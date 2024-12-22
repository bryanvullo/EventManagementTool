import logging
import json
import jsonschema
import os
import uuid

# date parsing
from dateutil import parser  # pip install python-dateutil
from urllib.parse import urlparse

# azure imports
import azure.functions as func
from azure.cosmos import CosmosClient
from openai import AzureOpenAI

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# Cosmos DB Containers
GroupCosmos = CosmosClient.from_connection_string(os.environ['DB_CONNECTION_STRING'])
EvecsDBProxy = GroupCosmos.get_database_client(os.environ['DB_NAME'])
EventsContainerProxy = EvecsDBProxy.get_container_client(os.environ['EVENTS_CONTAINER'])
TicketsContainerProxy = EvecsDBProxy.get_container_client(os.environ['TICKETS_CONTAINER'])
LocationsContainerProxy = EvecsDBProxy.get_container_client(os.environ['LOCATIONS_CONTAINER'])
UsersContainerProxy = EvecsDBProxy.get_container_client(os.environ['USERS_CONTAINER'])

# Azure OpenAI 
OpenAIEndpoint = os.environ['OPENAI_ENDPOINT']
OpenAIKey = os.environ['OPENAI_API_KEY']
OpenAIApiVersion = "2024-08-01-preview"
OpenAIClient = AzureOpenAI(azure_endpoint=OpenAIEndpoint, api_key=OpenAIKey, api_version=OpenAIApiVersion)
    
@app.route(route="create_event_gpt", auth_level=func.AuthLevel.FUNCTION, methods=['POST'])
def createEventGPT(req: func.HttpRequest) -> func.HttpResponse:
    """
    Creates a new event using GPT-35-turbo
    input: text
    output: Event: json object
    """
    logging.info('Python HTTP trigger function processed a request.')

    input = req.get_json()
    text = input['text']

    with open('schemas/event.json', 'r') as f:
        schema = f.read()

    eventJSON = ""
    prompt = f'''
        Using the following text as input, create a new event as a JSON object 
        in the structure of the following JSON Schema:
        
        Input: 
        {text}

        JSON Schema:
        {schema}
        '''
    valid = False

    for i in range(4):
        eventJSON = OpenAIClient.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": 
                    "Assistant is a large language model trained to create JSON objects based on user input. "},
                {"role": "user", "content": prompt}
            ]
        )

        try: 
            jsonschema.validate(instance=eventJSON, schema=schema)
            valid = True
            break
        except jsonschema.exceptions.ValidationError as e:
            logging.error(f"Error: {e.message}")

    if not valid:
        return func.HttpResponse(
            body = json.dumps({"result" : "Cannot generate JSON" }),
            status_code=500
        )
    
    return func.HttpResponse(
        body = json.dumps(eventJSON),
        status_code=200
    )



# -------------------------
# EVENT CRUD ENDPOINTS
# -------------------------

# Suppose we have the following global sets for validating tags/types:
valid_tags = {"lecture", "society", "leisure", "sports", "music"}  # Example
valid_types = {"lecture", "society", "sports", "concert"}          # Example

# Load event schema for validation
with open('schemas/event.json', 'r') as f:
    EVENT_SCHEMA = json.load(f)

# Create event
@app.route(route="create_event", auth_level=func.AuthLevel.FUNCTION, methods=['POST'])
def create_event(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()

        # ---- 0) Check mandatory fields  ----
        mandatory_fields = [
            "user_id", "name", "type", "desc", "location_id",
            "start_date", "end_date", "max_tick", "max_tick_pp"
        ]
        for field in mandatory_fields:
            if field not in body:
                return func.HttpResponse(
                    body=json.dumps({"error": f"Missing mandatory field: {field}"}),
                    status_code=400
                )

        # ---- 1) start_date < end_date ----
        try:
            start_dt = parser.isoparse(body["start_date"])
            end_dt = parser.isoparse(body["end_date"])
        except ValueError:
            return func.HttpResponse(
                body=json.dumps({"error": "Invalid date format. Please use ISO 8601 (e.g. yyyy-MM-ddTHH:mm:ss.fffffffZ)"}),
                status_code=400
            )
        if start_dt >= end_dt:
            return func.HttpResponse(
                body=json.dumps({"error": "Start date must be strictly before end date."}),
                status_code=400
            )

        # ---- 2) max_tick and max_tick_pp must be > 0  ----
        if body["max_tick"] <= 0:
            return func.HttpResponse(
                body=json.dumps({"error": "max_tick must be greater than 0."}),
                status_code=400
            )
        if body["max_tick_pp"] <= 0:
            return func.HttpResponse(
                body=json.dumps({"error": "max_tick_pp must be greater than 0."}),
                status_code=400
            )

        # ---- 3) Check location_id is not null and exists in DB  ----
        if not body["location_id"]:
            return func.HttpResponse(
                body=json.dumps({"error": "location_id cannot be null or empty."}),
                status_code=400
            )

        loc_query = "SELECT * FROM c WHERE c.location_id = @loc_id"
        loc_params = [{"name": "@loc_id", "value": body["location_id"]}]
        loc_items = list(LocationsContainerProxy.query_items(
            query=loc_query,
            parameters=loc_params,
            enable_cross_partition_query=True
        ))
        if not loc_items:
            return func.HttpResponse(
                body=json.dumps({"error": f"Location '{body['location_id']}' not found in the database."}),
                status_code=400
            )

        # ---- 4) Check img_url is a URL (or can be null/empty)  ----
        img_url = body.get("img_url", "")
        if img_url:  # only validate if non-empty
            try:
                parsed = urlparse(img_url)
                if not all([parsed.scheme, parsed.netloc]):
                    return func.HttpResponse(
                        body=json.dumps({"error": "img_url must be a valid URL or empty."}),
                        status_code=400
                    )
            except:
                return func.HttpResponse(
                    body=json.dumps({"error": "img_url must be a valid URL or empty."}),
                    status_code=400
                )

        # ---- 5) Check that the creator_id (user_id) is valid AND authorized  ----
        user_query = "SELECT * FROM c WHERE c.user_id = @u_id"
        user_params = [{"name": "@u_id", "value": body["user_id"]}]
        user_items = list(UsersContainerProxy.query_items(
            query=user_query,
            parameters=user_params,
            enable_cross_partition_query=True
        ))
        if not user_items:
            return func.HttpResponse(
                body=json.dumps({"error": f"User '{body['user_id']}' not found in the users database."}),
                status_code=400
            )

        # ---5.5) Check user.auth == True
        user_doc = user_items[0]
        if not user_doc.get("auth", False):
            return func.HttpResponse(
                body=json.dumps({"error": f"User '{body['user_id']}' is not authorized to create events."}),
                status_code=403  # 403 Forbidden
            )

        # ---- 6) Check that name and desc are strings  ----
        if not isinstance(body["name"], str):
            return func.HttpResponse(
                body=json.dumps({"error": "Event name must be a string."}),
                status_code=400
            )
        if not isinstance(body["desc"], str):
            return func.HttpResponse(
                body=json.dumps({"error": "Event description must be a string."}),
                status_code=400
            )
        
        # ---- 7) check for 'type':
        if body["type"] not in valid_types:
            return func.HttpResponse( body=json.dumps({"error": f"Invalid event type '{body['type']}'. Must be one of {list(valid_types)}."}), status_code=400)

        # ---- 8) check for 'tags' (optional field):
        if "tags" in body and body["tags"]:
            if not isinstance(body["tags"], list):
                return func.HttpResponse( body=json.dumps({"error": "tags must be a list of strings."}), status_code=400)
            for t in body["tags"]:
                if not isinstance(t, str):
                    return func.HttpResponse(body=json.dumps({"error": "Each tag must be a string."}), status_code=400)
                if t not in valid_tags:
                    return func.HttpResponse(
                    body=json.dumps({"error": f"Invalid tag '{t}'. Must be one of {list(valid_tags)}."}), status_code=400)

        # ---- Build the event_doc after passing validations ----
        event_id = str(uuid.uuid4())
        event_doc = {
            "event_id": event_id,
            "creator_id": [body["user_id"]],
            "name": body["name"],
            "type": body["type"],
            "desc": body["desc"],
            "location_id": body["location_id"],
            "start_date": body["start_date"],
            "end_date": body["end_date"],
            "max_tick": body["max_tick"],
            "max_tick_pp": body["max_tick_pp"],
            "tags": body.get("tags", []),
            "img_url": img_url
        }

        # ---- JSON Schema validation ----
        jsonschema.validate(instance=event_doc, schema=EVENT_SCHEMA)

        # ---- Insert into Cosmos DB ----
        EventsContainerProxy.create_item(event_doc)

        return func.HttpResponse(
            body=json.dumps({"result": "success", "event_id": event_id}),
            status_code=201
        )

    except jsonschema.exceptions.ValidationError as e:
        return func.HttpResponse(
            body=json.dumps({"error": f"JSON schema validation error: {str(e)}"}),
            status_code=400
        )
    except Exception as e:
        logging.error(f"Error creating event: {str(e)}")
        return func.HttpResponse(
            body=json.dumps({"error": "Internal Server Error"}),
            status_code=500
        )

# Get event
@app.route(route="get_event", auth_level=func.AuthLevel.FUNCTION, methods=['GET', 'POST'])
def get_event(req: func.HttpRequest) -> func.HttpResponse:
    """
    READ an event by event_id and user_id.
    Input (query params or JSON):
      - event_id
      - user_id
    """
    try:
        if req.method == 'POST':
            data = req.get_json()
            event_id = data.get("event_id")
            user_id = data.get("user_id")
        else:  # 'GET' in query params
            event_id = req.params.get("event_id")
            user_id = req.params.get("user_id")

        if not event_id or not user_id:
            return func.HttpResponse(
                body=json.dumps({"error": "Missing event_id or user_id"}),
                status_code=400
            )

        tags = event_doc.get("tags", [])
        event_type = event_doc.get("type")

        # Validate tags
        if not isinstance(tags, list):
            return func.HttpResponse(
                body=json.dumps({"error": "Event has invalid 'tags' format; expected a list."}), status_code=400)
        for t in tags:
            if t not in valid_tags:
                return func.HttpResponse(
                    body=json.dumps({"error": f"Event tag '{t}' is not in {list(valid_tags)}."}), status_code=400)

        # Validate type
        if event_type not in valid_types:
            return func.HttpResponse(
                body=json.dumps({"error": f"Event type '{event_type}' is not in {list(valid_types)}."}), status_code=400)

        # Query the event by event_id
        query = "SELECT * FROM c WHERE c.event_id = @event_id"
        params = [{"name": "@event_id", "value": event_id}]
        items = list(EventsContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if not items:
            return func.HttpResponse(
                body=json.dumps({"error": "Event not found"}),
                status_code=404
            )

        event_doc = items[0]
        return func.HttpResponse(
            body=json.dumps(event_doc),
            status_code=200
        )
    except Exception as e:
        logging.error(f"Error retrieving event: {str(e)}")
        return func.HttpResponse(
            body=json.dumps({"error": "Internal Server Error"}),
            status_code=500
        )

# TODO: Validate against valid tags and types
@app.route(route="update_event", auth_level=func.AuthLevel.FUNCTION, methods=['PUT', 'POST'])
def update_event(req: func.HttpRequest) -> func.HttpResponse:
    """
    UPDATE an event by event_id, only if the user_id is in creator_id array.
    Input:
      - event_id (string)
      - user_id  (string)
      - fields to update:
          name, type, desc, location_id, start_date, end_date, max_tick, max_tick_pp, tags, img_url
    Validations:
      1) start_date < end_date (datetime parse)
      2) max_tick, max_tick_pp > 0
      3) location_id not null & in DB
      4) img_url is a valid URL or can be empty
      5) creator_id (user_id) must exist in users partition
      6) name & desc must be strings
      7) type check (always passes for now)
      8) user.auth == True
    """
    try:
        body = req.get_json()
        event_id = body.get("event_id")
        user_id = body.get("user_id")

        if not event_id or not user_id:
            return func.HttpResponse(
                body=json.dumps({"error": "Missing event_id or user_id"}),
                status_code=400
            )

        # 1) Retrieve existing event
        query = "SELECT * FROM c WHERE c.event_id = @event_id"
        params = [{"name": "@event_id", "value": event_id}]
        items = list(EventsContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if not items:
            return func.HttpResponse(
                body=json.dumps({"error": "Event not found"}),
                status_code=404
            )

        event_doc = items[0]

        # 2) Check user ownership 
        if user_id not in event_doc["creator_id"]:
            return func.HttpResponse(
                body=json.dumps({"error": "Unauthorized: You are not the creator of this event."}),
                status_code=403
            )

        # 3) Ensure the user is valid & authorized (user.auth == True)
        user_query = "SELECT * FROM c WHERE c.user_id = @u_id"
        user_params = [{"name": "@u_id", "value": user_id}]
        user_items = list(UsersContainerProxy.query_items(
            query=user_query,
            parameters=user_params,
            enable_cross_partition_query=True
        ))
        if not user_items:
            return func.HttpResponse(
                body=json.dumps({"error": f"User '{user_id}' not found in users database."}),
                status_code=400
            )

        user_doc = user_items[0]
        if not user_doc.get("auth", False):
            return func.HttpResponse(
                body=json.dumps({"error": f"User '{user_id}' is not authorized to update events."}),
                status_code=403
            )

        # 4) Update only the fields provided
        updatable_fields = [
            "name", "type", "desc", "location_id", "start_date",
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
                return func.HttpResponse(
                    body=json.dumps({"error": "Invalid date format. Use ISO 8601 (yyyy-MM-ddTHH:mm:ss.fffffffZ)."}),
                    status_code=400
                )
            if start_dt >= end_dt:
                return func.HttpResponse(
                    body=json.dumps({"error": "Start date must be strictly before end date."}),
                    status_code=400
                )

        # (ii) max_tick and max_tick_pp > 0
        if "max_tick" in event_doc:
            if event_doc["max_tick"] <= 0:
                return func.HttpResponse(
                    body=json.dumps({"error": "max_tick must be greater than 0."}),
                    status_code=400
                )
        if "max_tick_pp" in event_doc:
            if event_doc["max_tick_pp"] <= 0:
                return func.HttpResponse(
                    body=json.dumps({"error": "max_tick_pp must be greater than 0."}),
                    status_code=400
                )

        # (iii) location_id not null & in DB
        if "location_id" in event_doc:
            if not event_doc["location_id"]:
                return func.HttpResponse(
                    body=json.dumps({"error": "location_id cannot be null or empty."}),
                    status_code=400
                )
            loc_query = "SELECT * FROM c WHERE c.location_id = @loc_id"
            loc_params = [{"name": "@loc_id", "value": event_doc["location_id"]}]
            loc_items = list(LocationsContainerProxy.query_items(
                query=loc_query,
                parameters=loc_params,
                enable_cross_partition_query=True
            ))
            if not loc_items:
                return func.HttpResponse( body=json.dumps({"error": f"Location '{event_doc['location_id']}' not found in DB."}), status_code=400)

        # (iv) img_url is a valid URL or empty
        if "img_url" in event_doc:
            img_url = event_doc["img_url"]
            if img_url:  # if not empty
                try:
                    parsed = urlparse(img_url)
                    if not all([parsed.scheme, parsed.netloc]):
                        return func.HttpResponse( body=json.dumps({"error": "img_url must be a valid URL or empty."}), status_code=400)
                except:
                    return func.HttpResponse( body=json.dumps({"error": "img_url must be a valid URL or empty."}), status_code=400)

        # (v) name & desc must be strings
        if "name" in event_doc:
            if not isinstance(event_doc["name"], str):
                return func.HttpResponse(
                    body=json.dumps({"error": "Event name must be a string."}),
                    status_code=400
                )
        if "desc" in event_doc:
            if not isinstance(event_doc["desc"], str):
                return func.HttpResponse(
                    body=json.dumps({"error": "Event description must be a string."}),
                    status_code=400
                )

        # (vi) type check 
        if "type" in event_doc:
            if event_doc["type"] not in valid_types:
                return func.HttpResponse(
                    body=json.dumps({"error": f"Invalid event type '{event_doc['type']}'. Must be one of {list(valid_types)}."}), status_code=400)
        
        #(vii) # tags check
        if "tags" in event_doc and event_doc["tags"]:
            if not isinstance(event_doc["tags"], list):
                return func.HttpResponse(
                    body=json.dumps({"error": "tags must be a list of strings."}), status_code=400)
            for t in event_doc["tags"]:
                if not isinstance(t, str):
                    return func.HttpResponse(body=json.dumps({"error": "Each tag must be a string."}), status_code=400)
                if t not in valid_tags:
                    return func.HttpResponse(body=json.dumps({"error": f"Invalid tag '{t}'. Must be one of {list(valid_tags)}."}), status_code=400)

        # 6) Validate updated doc with JSON schema
        jsonschema.validate(instance=event_doc, schema=EVENT_SCHEMA)

        # 7) Replace (upsert) the updated document in DB
        EventsContainerProxy.replace_item(item=event_doc, body=event_doc)

        return func.HttpResponse(
            body=json.dumps({"result": "success"}),
            status_code=200
        )

    except jsonschema.exceptions.ValidationError as e:
        return func.HttpResponse(
            body=json.dumps({"error": f"Validation error: {str(e)}"}),
            status_code=400
        )
    except Exception as e:
        logging.error(f"Error updating event: {str(e)}")
        return func.HttpResponse(
            body=json.dumps({"error": "Internal Server Error"}),
            status_code=500
        )

@app.route(route="delete_event", auth_level=func.AuthLevel.FUNCTION, methods=['DELETE', 'POST'])
def delete_event(req: func.HttpRequest) -> func.HttpResponse:
    try:
        if req.method == 'POST':
            data = req.get_json()
            event_id = data.get("event_id")
            user_id = data.get("user_id")
        else:
            event_id = req.params.get("event_id")
            user_id = req.params.get("user_id")

        if not event_id or not user_id:
            return func.HttpResponse(
                body=json.dumps({"error": "Missing event_id or user_id"}),
                status_code=400
            )

        # Query the event by event_id
        query = "SELECT * FROM c WHERE c.event_id = @event_id"
        params = [{"name": "@event_id", "value": event_id}]
        items = list(EventsContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if not items:
            return func.HttpResponse(
                body=json.dumps({"error": "Event not found"}),
                status_code=404
            )

        event_doc = items[0]

        # Check if user is in the admin array
        if user_id not in event_doc["creator_ids"]:
            return func.HttpResponse(
                body=json.dumps({"error": "Unauthorized: You are not an admin of this event."}),
                status_code=403
            )

        # Delete the document
        EventsContainerProxy.delete_item(item=event_doc, partition_key=event_doc["event_id"]) 
        # NOTE: This may need to be adjusted

        return func.HttpResponse(
            body=json.dumps({"result": "success"}),
            status_code=200
        )
    except Exception as e:
        logging.error(f"Error deleting event: {str(e)}")
        return func.HttpResponse(
            body=json.dumps({"error": "Internal Server Error"}),
            status_code=500
        )

# Allows owners to grant adminship to other users
@app.route(route="grant_event_adminship", auth_level=func.AuthLevel.FUNCTION, methods=['POST'])
def grant_event_adminship(req: func.HttpRequest) -> func.HttpResponse:
    """
    Grants admin permission to another user on a specific event.
    Inputs (JSON):
      - creator_id    (the caller who must already be in creator_id)
      - new_admin_id  (the user to be added to creator_id)
      - event_id
    """
    try:
        data = req.get_json()
        creator_id = data.get("creator_id")
        new_admin_id = data.get("new_admin_id")
        event_id = data.get("event_id")

        # Basic input check
        if not creator_id or not new_admin_id or not event_id:
            return func.HttpResponse(
                json.dumps({"error": "Missing one of: creator_id, new_admin_id, event_id"}),
                status_code=400
            )

        # Fetch the event
        query = "SELECT * FROM c WHERE c.event_id = @event_id"
        params = [{"name": "@event_id", "value": event_id}]
        items = list(EventsContainerProxy.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if not items:
            return func.HttpResponse(
                json.dumps({"error": "Event not found"}),
                status_code=404
            )

        event_doc = items[0]

        # Check if the caller is in the creator_id list
        if creator_id not in event_doc["creator_id"]:
            return func.HttpResponse(
                json.dumps({"error": "Unauthorized: You are not an admin of this event."}),
                status_code=403
            )

        # Add the new admin if they are not already in the list
        if new_admin_id not in event_doc["creator_id"]:
            event_doc["creator_id"].append(new_admin_id)

            # Validate the updated doc (optional but recommended)
            jsonschema.validate(instance=event_doc, schema=EVENT_SCHEMA)

            # Update in DB
            EventsContainerProxy.replace_item(item=event_doc, body=event_doc)

        return func.HttpResponse(
            json.dumps({"result": f"User {new_admin_id} now has admin rights on event {event_id}"}),
            status_code=200
        )
    except jsonschema.exceptions.ValidationError as e:
        return func.HttpResponse(
            body=json.dumps({"error": f"Validation error: {str(e)}"}),
            status_code=400
        )
    except Exception as e:
        logging.error(f"Error granting adminship: {str(e)}")
        return func.HttpResponse(
            body=json.dumps({"error": "Internal Server Error"}),
            status_code=500
        )

# -------------------------
# EVENT HELPER FUNCTIONS
# -------------------------
@app.route(route="make_calendar", methods=['POST'])
def make_calendar(req: func.HttpRequest) -> func.HttpResponse:
    """
    1) Input (JSON):
       {
         "start_date": "2024-01-01T00:00:00.0000000Z",
         "end_date":   "2024-12-31T23:59:59.9999999Z",
         "filters": {
             "tags": ["leisure", "society"],
             "type": "lecture",
             "desc": "some phrase to match",
             "location_id": "2050g0902805",
             "max_tick": 150,
             "max_tick_pp": 3
         }
       }

    2) Validations:
       - start_date < end_date
       - if "tags" in filters => each tag in valid_tags
       - if "type" in filters => must be in valid_types
       - if "desc" in filters => must be a string
       - if "location_id" in filters => must exist in the DB \locations
       - if "max_tick" in filters => > 0
       - if "max_tick_pp" in filters => > 0

    3) Returns:
       List of events that lie within the date range and match the given filters.
    """
    try:
        # ---------------------
        # 1) Parse Input JSON
        # ---------------------
        body = req.get_json()

        start_date_str = body.get("start_date")
        end_date_str = body.get("end_date")
        filters = body.get("filters", {})  # a dict/object with possible fields

        if not start_date_str or not end_date_str:
            return func.HttpResponse(
                json.dumps({"error": "start_date and end_date are required."}),
                status_code=400
            )

        # Validate date format
        try:
            start_dt = parser.isoparse(start_date_str)
            end_dt   = parser.isoparse(end_date_str)
        except ValueError:
            return func.HttpResponse(
                json.dumps({"error": "Invalid date format. Must be ISO8601, e.g. yyyy-MM-ddTHH:mm:ss.fffffffZ"}),
                status_code=400
            )

        # Check start < end
        if start_dt >= end_dt:
            return func.HttpResponse(
                json.dumps({"error": "start_date must be strictly before end_date."}),
                status_code=400
            )

        # ---------------------
        # 2) Validate Filters
        # ---------------------
        # The "filters" object can have keys: ["tags", "type", "desc", "location_id", "max_tick", "max_tick_pp", ...]

        # (A) tags => list of strings in valid_tags
        if "tags" in filters and filters["tags"]:
            if not isinstance(filters["tags"], list):
                return func.HttpResponse(
                    json.dumps({"error": "tags must be a list of strings."}),
                    status_code=400
                )
            # Check each tag is in valid_tags
            for t in filters["tags"]:
                if not isinstance(t, str):
                    return func.HttpResponse(
                        json.dumps({"error": "Each tag must be a string."}),
                        status_code=400
                    )
                
                if (not t or t.strip() == ""):
                    pass  # allow empty strings

                if t not in valid_tags:
                    return func.HttpResponse(
                        json.dumps({"error": f"Invalid tag '{t}'. Must be in {list(valid_tags)}."}),
                        status_code=400
                    )

        # (B) type => string in valid_types
        if "type" in filters:
            if not isinstance(filters["type"], str):
                return func.HttpResponse(
                    json.dumps({"error": "type must be a string."}),
                    status_code=400
                )
            if filters["type"] not in valid_types:
                return func.HttpResponse(
                    json.dumps({"error": f"Invalid event type '{filters['type']}'. Allowed: {list(valid_types)}."}),
                    status_code=400
                )

        # (C) desc => must be a string
        if "desc" in filters:
            if not isinstance(filters["desc"], str):
                return func.HttpResponse(
                    json.dumps({"error": "desc must be a string."}),
                    status_code=400
                )
            # We'll do partial matching with 'desc' client side or using a LIKE condition.

        # (D) location_id => must exist in DB
        if "location_id" in filters and filters["location_id"]:
            if not isinstance(filters["location_id"], str) or not filters["location_id"].strip():
                return func.HttpResponse(
                    json.dumps({"error": "location_id must be a non-empty string."}),
                    status_code=400
                )

            loc_query = "SELECT * FROM c WHERE c.location_id = @loc_id"
            loc_params = [{"name": "@loc_id", "value": filters["location_id"]}]
            loc_items = list(LocationsContainerProxy.query_items(
                query=loc_query,
                parameters=loc_params,
                enable_cross_partition_query=True
            ))
            if not loc_items:
                return func.HttpResponse(
                    json.dumps({"error": f"location_id '{filters['location_id']}' not found in DB."}),
                    status_code=400
                )

        # (E) max_tick, max_tick_pp => must be > 0
        if "max_tick" in filters:
            if not isinstance(filters["max_tick"], (int, float)) or filters["max_tick"] <= 0:
                return func.HttpResponse(
                    json.dumps({"error": "max_tick must be a positive number."}),
                    status_code=400
                )
        if "max_tick_pp" in filters:
            if not isinstance(filters["max_tick_pp"], (int, float)) or filters["max_tick_pp"] <= 0:
                return func.HttpResponse(
                    json.dumps({"error": "max_tick_pp must be a positive number."}),
                    status_code=400
                )

        # ---------------------
        # 3) Retrieve Events in Date Range
        # ---------------------
        # Because start_date'/'end_date' are stored as ISO8601 strings, you can compare them lexically:
        #  c.start_date >= @start_lex AND c.end_date <= @end_lex
        # This only works if all events are stored as zero-padded UTC times. 

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

        # ---------------------
        # 4) Client-Side Filter 
        # ---------------------
        # For each filter key, we check if the event doc matches exactly or partially (e.g., desc with substring).
        # If a filter is not present, do no filtering on that field.

        def event_matches_filter(event_doc, filters):
            # (A) tags => exact array match
            if "tags" in filters and filters["tags"]:
                if event_doc.get("tags", []) != filters["tags"]:
                    return False

            # (B) type => exact match
            if "type" in filters:
                if event_doc.get("type") != filters["type"]:
                    return False

            # (C) desc => partial substring match? or exact?
            #   The requirement says: "search all events with a matching desc using regex or query logic"
            #   We'll do a simple "substring" check here:
            if "desc" in filters:
                if filters["desc"] not in event_doc.get("desc", ""):
                    return False

            # (D) location_id => exact match
            if "location_id" in filters and filters["location_id"]:
                if event_doc.get("location_id") != filters["location_id"]:
                    return False

            # (E) max_tick => exact match
            if "max_tick" in filters:
                if event_doc.get("max_tick") != filters["max_tick"]:
                    return False

            # (F) max_tick_pp => exact match
            if "max_tick_pp" in filters:
                if event_doc.get("max_tick_pp") != filters["max_tick_pp"]:
                    return False

            return True

        filtered_events = []
        for ev in events_in_range:
            if event_matches_filter(ev, filters):
                filtered_events.append(ev)

        # ---------------------
        # 5) Return Results
        # ---------------------
        return func.HttpResponse(
            json.dumps({"results": filtered_events}, default=str),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in make_calendar: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": "Internal Server Error"}),
            status_code=500
        )