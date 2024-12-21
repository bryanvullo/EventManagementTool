import logging
import json
import jsonschema
import os
import requests
import uuid 

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

# Load event schema for validation
with open('schemas/event.json', 'r') as f:
    EVENT_SCHEMA = json.load(f)

@app.route(route="create_event", auth_level=func.AuthLevel.FUNCTION, methods=['POST'])
def create_event(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()

        # Check mandatory fields (including user_id -> to be first admin)
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

        # Generate event_id
        event_id = str(uuid.uuid4())

        # Build the event document
        # Instead of "creator_id": body["user_id"], we store an array of IDs
        event_doc = {
            "event_id": event_id,
            "creator_id": [body["user_id"]],  # store the single user as an array
            "name": body["name"],
            "type": body["type"],
            "desc": body["desc"],
            "location_id": body["location_id"],
            "start_date": body["start_date"],  
            "end_date": body["end_date"],
            "max_tick": body["max_tick"],
            "max_tick_pp": body["max_tick_pp"],
            # optional
            "tags": body.get("tags", []),
            "img_url": body.get("img_url", "")
        }

        # Validate with JSON schema
        jsonschema.validate(instance=event_doc, schema=EVENT_SCHEMA)

        # Insert into Cosmos DB
        EventsContainerProxy.create_item(event_doc)

        return func.HttpResponse(
            body=json.dumps({"result": "success", "event_id": event_id}),
            status_code=201
        )
    except jsonschema.exceptions.ValidationError as e:
        return func.HttpResponse(
            body=json.dumps({"error": f"Validation error: {str(e)}"}),
            status_code=400
        )
    except Exception as e:
        logging.error(f"Error creating event: {str(e)}")
        return func.HttpResponse(
            body=json.dumps({"error": "Internal Server Error"}),
            status_code=500
        )

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


@app.route(route="update_event", auth_level=func.AuthLevel.FUNCTION, methods=['PUT', 'POST'])
def update_event(req: func.HttpRequest) -> func.HttpResponse:
    """
    UPDATE an event by event_id, only if the user_id matches the creator_id.
    Input:
      - event_id (string)
      - user_id  (string)
      - fields to update:
          name, type, desc, location_id, start_date, end_date, max_tick, max_tick_pp, tags, img_url
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

        # Get existing event
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

        # Check user ownership
        if user_id not in event_doc["creator_id"]:
            return func.HttpResponse(
                body=json.dumps({"error": "Unauthorized: You are not the creator of this event."}),
                status_code=403
            )

        # Update only the fields provided
        updatable_fields = [
            "name", "type", "desc", "location_id", "start_date", 
            "end_date", "max_tick", "max_tick_pp", "tags", "img_url"
        ]
        for field in updatable_fields:
            if field in body:
                event_doc[field] = body[field]

        # Validate updated doc
        jsonschema.validate(instance=event_doc, schema=EVENT_SCHEMA)

        # Upsert (replace) the updated document
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
        EventsContainerProxy.delete_item(item=event_doc, partition_key=event_doc["creator_ids"][0])
        # NOTE: If your partition key is still the original user ID, you might need to figure out 
        # which user is the "primary" partition key. 
        # Or if your partition key is something else entirely, adapt here.

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