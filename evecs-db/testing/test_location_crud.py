# test_locations_crud.py

import unittest
import uuid
import os
import requests
import json
import jsonschema
from azure.cosmos import CosmosClient, exceptions
from datetime import datetime
from jsonschema.exceptions import ValidationError, SchemaError

# ----------------------------------SETUP----------------------------------------
# Load local.settings.json or environment variables
settings_file = os.path.join(os.path.dirname(__file__), '..', 'local.settings.json')
if os.path.exists(settings_file):
    with open(settings_file) as f:
        settings = json.load(f).get('Values', {})
    for key, value in settings.items():
        os.environ[key] = value

# --------------------------------------------------------------------------------
# Use the two existing event documents in Cosmos DB (on the events partition).
# These event_id values must exist in your DB for these tests to pass.
# --------------------------------------------------------------------------------
EXISTING_EVENT_ID_1 = "683f7199-cfd4-46df-89ef-98aec0e3dfca"
EXISTING_EVENT_ID_2 = "324a9052-0378-45a5-9cd9-4a314d3aef72"

class TestLocationCrud(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """
        setUpClass runs once before all tests.
        We establish a CosmosClient and create references to our containers.
        """
        # 1) Load environment vars (from local.settings.json or system env)
        cls.connection_string = os.environ.get("DB_CONNECTION_STRING")
        cls.db_name = os.environ.get("DB_NAME", "evecs")
        cls.locations_container_name = os.environ.get("LOCATIONS_CONTAINER", "locations")

        # 2) Initialize the CosmosClient
        cls.client = CosmosClient.from_connection_string(cls.connection_string)
        cls.db = cls.client.get_database_client(cls.db_name)
        cls.locations_container = cls.db.get_container_client(cls.locations_container_name)

        # 3) Base URL for your deployed Azure Function App (no trailing slash).
        #    Adjust if you're running locally (http://localhost:7071/api) or in Azure.
        cls.base_url = "http://localhost:7071/api"
        #cls.base_url = "https://your-function-app.azurewebsites.net/api"

        # 4) Load the function app key if needed (for Function-level auth)
        cls.function_key = os.environ.get("FUNCTION_APP_KEY", "")

        # 5) Path to your location.json schema
        cls.location_schema_path = os.path.join(os.path.dirname(__file__), '..', 'schemas', 'location.json')

    @classmethod
    def tearDownClass(cls):
        """
        tearDownClass runs once after all tests finish.
        (Optionally) Clean up leftover test data in DB if desired.
        """
        # Clean up test data in Cosmos DB
        pass

    # ----------------------------------------------------------------
    # Helper: Build endpoints for location CRUD
    # ----------------------------------------------------------------
    def _get_create_location_url(self) -> str:
        """Returns the 'create_location' endpoint with function key appended if needed."""
        # Example: http://localhost:7071/api/create_location?code=XYZ
        return f"{self.base_url}/create_location"

    def _get_delete_location_url(self) -> str:
        return f"{self.base_url}/delete_location"

    def _get_read_location_url(self) -> str:
        return f"{self.base_url}/read_location"

    def _get_edit_location_url(self) -> str:
        return f"{self.base_url}/edit_location"

    def _get_rooms_url(self) -> str:
        """ Endpoint for 'get_rooms_from_building' """
        return f"{self.base_url}/get_rooms_from_location_id"

    # ----------------------------------------------------------------
    # 0. Test that a properly formatted location.json schema 
    #    itself is valid JSON schema
    # ----------------------------------------------------------------
    def test_0_location_schema_is_valid(self):
        """
        Ensure the location.json file is a valid JSON Schema (Draft 7 or whichever draft you use).
        """
        with open(self.location_schema_path, 'r') as f:
            schema = json.load(f)

        try:
            jsonschema.Draft7Validator.check_schema(schema)
        except SchemaError as e:
            self.fail(f"Location schema is not valid: {e}")
        except Exception as e:
            self.fail(f"Unexpected error when validating location schema: {e}")

        # If we get here, the schema is valid
        print("Location schema passes draft-07 check!")

    # ----------------------------------------------------------------
    # 1. Creating a valid location should succeed
    # ----------------------------------------------------------------
    def test_1_create_location_valid(self):
        """
        POST a valid location object, expecting a 202 success code.
        """
        valid_location_body = {
            "location_name": "Test Building 9",
            "events_ids": [
                {"event_id": EXISTING_EVENT_ID_1},
                {"event_id": EXISTING_EVENT_ID_2}
            ],
            "rooms": [
                {
                    "room_id": "room_A",
                    "room_name": "Auditorium A",
                    "capacity": 100,
                    "events_ids": [{"event_id": EXISTING_EVENT_ID_1}],
                    "description": "Large auditorium"
                },
                {
                    "room_id": "room_B",
                    "room_name": "Conference Room B",
                    "capacity": 50,
                    "events_ids": [{"event_id": EXISTING_EVENT_ID_2}],
                    "description": "Medium conference room"
                }
            ]
        }

        resp = requests.post(self._get_create_location_url(), json=valid_location_body)
        self.assertIn(resp.status_code, [202, 201, 200], f"Unexpected status code: {resp.status_code}")
        data = resp.json()
        print("Create location response:", data)

        # Check the response body for success confirmation
        self.assertIn("message", data, "Response body missing 'message'.")
        self.assertIn("location_id", data, "Response body missing 'location_id'.")

        #Make a query to the database to check if the location was created
        query = f"SELECT * FROM c WHERE c.location_id = '{data['location_id']}'"
        results = list(self.locations_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        self.assertEqual(len(results), 1, "Expected exactly one matching location item.")
        location_doc = results[0]
        self.assertEqual(location_doc["location_name"], valid_location_body["location_name"])
        self.assertEqual(location_doc["rooms"], valid_location_body["rooms"])

    # ----------------------------------------------------------------
    # 2. Creating an invalid location should fail
    #    We'll systematically break each required field 
    # ----------------------------------------------------------------
    def test_2_create_location_invalid(self):
        """
        POST invalid location documents that fail the required property checks or type checks.
        We expect an unsuccessful (400) response for each.
        """
        # Cases that break each required field
        invalid_payloads = [
            # 1) Missing location_id
            {
                "location_name": "Missing ID",
                "events_ids": [],
                "rooms": []
            },
            # 2) Missing location_name
            {
                "location_id": "loc_invalid_1",
                "events_ids": [],
                "rooms": []
            },
            # 3) Missing events_ids
            {
                "location_id": "loc_invalid_2",
                "location_name": "No events",
                "rooms": []
            },
            # 4) Missing rooms
            {
                "location_id": "loc_invalid_3",
                "location_name": "No Rooms",
                "events_ids": []
            },
            # 5) Wrong data type for rooms (string instead of array)
            {
                "location_id": "loc_invalid_4",
                "location_name": "Bad Rooms Type",
                "events_ids": [],
                "rooms": "NotAnArray"
            }
        ]

        for idx, payload in enumerate(invalid_payloads):
            resp = requests.post(self._get_create_location_url(), json=payload)
            self.assertEqual(
                resp.status_code, 
                400, 
                f"[Case {idx}] Expected 400 but got {resp.status_code}. Payload: {payload}"
            )
            error_body = resp.json()
            self.assertIn("error", error_body, f"[Case {idx}] 'error' message expected in response body.")

    # ----------------------------------------------------------------
    # 3. Create a valid location, then delete it
    # ----------------------------------------------------------------
    def test_3_delete_location(self):
        """
        1) Create a valid location
        2) Delete it
        3) Expect a successful status code from deletion
        """
        create_body = {
            "location_name": "Delete Me",
            "events_ids": [
                {"event_id": EXISTING_EVENT_ID_1}
            ],
            "rooms": []
        }

        # 1) Create
        resp_create = requests.post(self._get_create_location_url(), json=create_body)
        self.assertIn(resp_create.status_code, [202, 201, 200])
        location_id = resp_create.json()["location_id"]

        # 2) Delete
        delete_payload = {"location_id": location_id}
        resp_delete = requests.post(self._get_delete_location_url(), json=delete_payload)
        self.assertIn(resp_delete.status_code, [200, 201], f"Unexpected status code: {resp_delete.status_code}")
        data = resp_delete.json()
        self.assertIn("message", data, "Expected a 'message' confirming deletion.")

        # 3) Verify it no longer exists in DB
        with self.assertRaises(exceptions.CosmosResourceNotFoundError):
            self.locations_container.read_item(item=location_id, partition_key=location_id)

    # ----------------------------------------------------------------
    # 4A. Create a valid location, then edit with valid fields
    # ----------------------------------------------------------------
    def test_4A_edit_location_valid(self):
        """
        1) Create a valid location
        2) Edit it with valid JSON data (e.g. change location_name)
        3) Expect success
        """
        location_id = f"loc_edit_{uuid.uuid4()}"
        create_body = {
            "location_id": location_id,
            "location_name": "Edit Test Original",
            "events_ids": [
                {"event_id": EXISTING_EVENT_ID_2}
            ],
            "rooms": []
        }
        # Create
        resp_create = requests.post(self._get_create_location_url(), json=create_body)
        self.assertIn(resp_create.status_code, [200, 201, 202], f"Create failed with {resp_create.status_code}.")

        # Edit: We'll rename the location
        edit_body = {
            "location_id": location_id,
            "location_name": "Edited Building Name"
        }
        resp_edit = requests.post(self._get_edit_location_url(), json=edit_body)
        self.assertIn(resp_edit.status_code, [200, 201], f"Edit returned unexpected code: {resp_edit.status_code}")
        edit_data = resp_edit.json()
        self.assertIn("location", edit_data, "Expected the updated location doc in response.")
        self.assertEqual(edit_data["location"]["location_name"], edit_body["location_name"])

    # ----------------------------------------------------------------
    # 4B. Create a valid location, then edit with invalid JSON data
    # ----------------------------------------------------------------
    def test_4B_edit_location_invalid(self):
        """
        1) Create a valid location
        2) Try to edit with invalid data (e.g. 'rooms' as a string).
        3) Expect an error (400).
        """
        location_id = f"loc_edit_inv_{uuid.uuid4()}"
        create_body = {
            "location_id": location_id,
            "location_name": "Edit Test Invalid",
            "events_ids": [
                {"event_id": EXISTING_EVENT_ID_1}
            ],
            "rooms": []
        }
        # Create
        resp_create = requests.post(self._get_create_location_url(), json=create_body)
        self.assertIn(resp_create.status_code, [200, 201, 202])

        # Edit with invalid field
        edit_body = {
            "location_id": location_id,
            "rooms": "NotAnArray"   # invalid type
        }
        resp_edit = requests.post(self._get_edit_location_url(), json=edit_body)
        self.assertEqual(resp_edit.status_code, 400, f"Expected 400, got {resp_edit.status_code}.")
        error_data = resp_edit.json()
        self.assertIn("error", error_data)

    # ----------------------------------------------------------------
    # 5. Test get_rooms_from_location_id
    # ----------------------------------------------------------------
    def test_5_get_rooms_from_location_id(self):
        """
        1) Create a properly formatted location doc with multiple rooms
        2) Call get_rooms_from_location_id
        3) Check the returned rooms match what was inserted
        """
        location_id = f"loc_rooms_{uuid.uuid4()}"
        rooms_example = [
            {
                "room_id": "room_100",
                "room_name": "Room 100",
                "capacity": 200,
                "events_ids": [{"event_id": EXISTING_EVENT_ID_1}],
                "description": "Large Lecture Hall"
            },
            {
                "room_id": "room_101",
                "room_name": "Room 101",
                "capacity": 50,
                "events_ids": [{"event_id": EXISTING_EVENT_ID_2}],
                "description": "Small Meeting Room"
            }
        ]
        create_body = {
            "location_id": location_id,
            "location_name": "Get Rooms Building",
            "events_ids": [
                {"event_id": EXISTING_EVENT_ID_2}
            ],
            "rooms": rooms_example
        }

        # Create
        resp_create = requests.post(self._get_create_location_url(), json=create_body)
        self.assertIn(resp_create.status_code, [200, 201, 202], "Create location for get_rooms failed unexpectedly.")

        # Now call get_rooms_from_location_id
        get_rooms_payload = {"location_id": location_id}
        resp_rooms = requests.post(self._get_rooms_url(), json=get_rooms_payload)
        self.assertEqual(resp_rooms.status_code, 200, f"Unexpected code: {resp_rooms.status_code}")
        body_rooms = resp_rooms.json()

        self.assertIn("rooms", body_rooms, "Expected 'rooms' in response.")
        returned_rooms = body_rooms["rooms"]
        self.assertEqual(len(returned_rooms), len(rooms_example), "Mismatch in the number of rooms returned.")

        # Check that each room matches (basic check)
        for original, returned in zip(rooms_example, returned_rooms):
            self.assertEqual(original["room_id"], returned["room_id"])
            self.assertEqual(original["room_name"], returned["room_name"])
            self.assertEqual(original["capacity"], returned["capacity"])
            self.assertEqual(original["events_ids"], returned["events_ids"])
            self.assertEqual(original["description"], returned["description"])


if __name__ == '__main__':
    unittest.main()
