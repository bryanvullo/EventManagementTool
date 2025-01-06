import logging
import json
import jsonschema
import os
import requests
import uuid

# date parsing
from dateutil import parser  # pip install python-dateutil
from urllib.parse import urlparse

# azure imports
import azure.functions as func
from azure.cosmos import CosmosClient, exceptions as cosmos_exceptions
from openai import AzureOpenAI

# Import event_crud, login_crud, ticket_crud, location_crud functions
from shared_code.events_crud import (create_event, get_event, update_event, delete_event, grant_event_adminship, make_calendar)
from shared_code.ticket_crud import (create_ticket, get_ticket, delete_ticket)
from shared_code.login_crud import (register_user, login_user, update_user, delete_user)
from shared_code.location_crud import (get_location_groups)
from shared_code.location_crud import (get_rooms_from_location_id, create_location, delete_location, read_location, edit_location)

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

    i= 0
    while i in range(4):
        eventJSON = OpenAIClient.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": 
                    "Assistant is a large language model trained to create JSON objects based on user input. "},
                {"role": "user", "content": prompt}
            ]
        )
        i += 1 # Added an i update here
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

@app.route(route="create_ticket", auth_level=func.AuthLevel.FUNCTION, methods=['POST'])
def create_ticket_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = create_ticket(req, TicketsContainerProxy, UsersContainerProxy, EventsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="get_ticket", auth_level=func.AuthLevel.FUNCTION, methods=['GET', 'POST'])
def get_ticket_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = get_ticket(req, TicketsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="delete_ticket", auth_level=func.AuthLevel.FUNCTION, methods=['POST'])
def delete_ticket_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = delete_ticket(req, TicketsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

# -------------------------
# EVENT CRUD ENDPOINTS
# NOTE: The actual code is contained in the shared_code/events_crud folder.
# NOTE: The lists valid_tags and valid_types determine validation ourcomes. Edit these lists to change validation.
# -------------------------
@app.route(route="create_event", auth_level=func.AuthLevel.FUNCTION, methods=['POST'])
def create_event_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = create_event(req, EventsContainerProxy, LocationsContainerProxy, UsersContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="get_event", auth_level=func.AuthLevel.FUNCTION, methods=['GET', 'POST'])
def get_event_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = get_event(req, EventsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="update_event", auth_level=func.AuthLevel.FUNCTION, methods=['PUT', 'POST'])
def update_event_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = update_event(req, EventsContainerProxy, LocationsContainerProxy, UsersContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="delete_event", auth_level=func.AuthLevel.FUNCTION, methods=['DELETE', 'POST'])
def delete_event_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = delete_event(req, EventsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="grant_event_adminship", auth_level=func.AuthLevel.FUNCTION, methods=['POST'])
def grant_event_adminship_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = grant_event_adminship(req, EventsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="make_calendar", methods=['POST'])
def make_calendar_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = make_calendar(req, EventsContainerProxy, LocationsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

# -------------------------
# LOGIN/USER CRUD ENDPOINTS
# -------------------------
@app.route(route="register_user", methods=['POST'])
def register_user_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = register_user(req, UsersContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="login_user", methods=['POST'])
def login_user_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = login_user(req, UsersContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="update_user", methods=['POST', 'PUT'])
def update_user_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = update_user(req, UsersContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="delete_user", methods=['POST', 'DELETE'])
def delete_user_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = delete_user(req, UsersContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

# -------------------------
# LOCATION CRUD ENDPOINTS
# NOTE: The actual code is contained in the shared_code/location_crud folder.
# -------------------------
@app.route(route="get_location_groups", auth_level=func.AuthLevel.FUNCTION, methods=['GET', 'POST'])
def get_location_groups_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    result = get_location_groups(req, LocationsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="get_rooms_from_location_id", auth_level=func.AuthLevel.FUNCTION, methods=['GET', 'POST'])
def get_rooms_from_location_id_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Endpoint for retrieving rooms given a location_id."""
    result = get_rooms_from_location_id(req, LocationsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="create_location", auth_level=func.AuthLevel.FUNCTION, methods=['POST'])
def create_location_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Endpoint for creating a new location."""
    result = create_location(req, LocationsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="delete_location", auth_level=func.AuthLevel.FUNCTION, methods=['POST', 'DELETE'])
def delete_location_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Endpoint for deleting a location by location_id."""
    result = delete_location(req, LocationsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="read_location", auth_level=func.AuthLevel.FUNCTION, methods=['GET', 'POST'])
def read_location_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Endpoint for reading a location's full document by location_id."""
    result = read_location(req, LocationsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )

@app.route(route="edit_location", auth_level=func.AuthLevel.FUNCTION, methods=['PUT', 'POST'])
def edit_location_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Endpoint for editing an existing location."""
    result = edit_location(req, LocationsContainerProxy)
    return func.HttpResponse(
        body=json.dumps(result["body"]),
        status_code=result["status_code"]
    )