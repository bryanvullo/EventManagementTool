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
        # cls.base_url = "https://your-function-app.azurewebsites.net/api"

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
        # No global cleanup since each test cleans up its own doc.
        pass

    # ----------------------------------------------------------------
    # Helper: Build endpoints for location CRUD
    # ----------------------------------------------------------------
    def _get_create_location_url(self) -> str:
        """Returns the 'create_location' endpoint with function key appended if needed."""
        return f"{self.base_url}/create_location"

    def _get_delete_location_url(self) -> str:
        return f"{self.base_url}/delete_location"

    def _get_read_location_url(self) -> str:
        return f"{self.base_url}/read_location"

    def _get_edit_location_url(self) -> str:
        return f"{self.base_url}/edit_location"

    def _get_rooms_url(self) -> str:
        """Endpoint for 'get_rooms_from_location_id'"""
        return f"{self.base_url}/get_rooms_from_location_id"

    def _delete_in_db(self, location_id: str):
        """
        Direct cleanup helper to remove a location from Cosmos DB if it exists.
        """
        if not location_id:
            return
        try:
            self.locations_container.delete_item(item=location_id, partition_key=location_id)
        except exceptions.CosmosResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error cleaning up location '{location_id}': {e}")

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
    # 1. Creating a valid location should succeed (and then we delete it)
    # ----------------------------------------------------------------
    def test_1_create_location_valid(self):
        """
        POST a valid location object, expecting a 202 success code.
        Cleanup: delete the newly created doc in DB.
        """
        location_id = None
        try:
            valid_location_body = {
                "location_name": "Test Building 101",
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
            print(resp.json())
            self.assertIn(resp.status_code, [202, 201, 200], f"Unexpected status code: {resp.status_code}")
            data = resp.json()
            print("Create location response:", data)

            # Check the response body for success confirmation
            self.assertIn("message", data, "Response body missing 'message'.")
            self.assertIn("location_id", data, "Response body missing 'location_id'.")
            location_id = data["location_id"]

            # Confirm it was created in DB
            query = f"SELECT * FROM c WHERE c.location_id = '{location_id}'"
            results = list(self.locations_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            self.assertEqual(len(results), 1, "Expected exactly one matching location item.")
            location_doc = results[0]
            self.assertEqual(location_doc["location_name"], valid_location_body["location_name"])
            self.assertEqual(location_doc["rooms"], valid_location_body["rooms"])

        finally:
            # Cleanup
            if location_id:
                self._delete_in_db(location_id)

    # ----------------------------------------------------------------
    # 2. Creating an invalid location should fail
    #    We'll systematically break each required field
    # ----------------------------------------------------------------
    def test_2_create_location_invalid(self):
        """
        POST invalid location documents that fail the required property checks or type checks.
        We expect an unsuccessful (400) response for each.
        No location is created, so no cleanup needed.
        """
        # Cases that break each required field
        invalid_payloads = [
            # 1) Missing location_id
            {
                "location_name": "Missing ID",
                "events_ids": [EXISTING_EVENT_ID_1],
                "rooms": []
            },
            # 2) Missing location_name
            {
                "location_id": "loc_invalid_1",
                "events_ids": [EXISTING_EVENT_ID_1],
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
                "events_ids": [EXISTING_EVENT_ID_1]
            },
            # 5) Wrong data type for rooms (string instead of array)
            {
                "location_id": "loc_invalid_4",
                "location_name": "Bad Rooms Type",
                "events_ids": [EXISTING_EVENT_ID_1],
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
        The test itself does the deletion step, so it's self-cleaning.
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

        # 2) Delete (via the endpoint)
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
        4) Cleanup in finally
        """
        location_id = None
        try:
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
            self.assertIn(resp_edit.status_code, [200, 201],
                          f"Edit returned unexpected code: {resp_edit.status_code}")
            edit_data = resp_edit.json()
            self.assertIn("location", edit_data, "Expected the updated location doc in response.")
            self.assertEqual(edit_data["location"]["location_name"], edit_body["location_name"])
        finally:
            self._delete_in_db(location_id)

    # ----------------------------------------------------------------
    # 4B. Create a valid location, then edit with invalid JSON data
    # ----------------------------------------------------------------
    def test_4B_edit_location_invalid(self):
        """
        1) Create a valid location
        2) Try to edit with invalid data (e.g. 'rooms' as a string).
        3) Expect an error (400).
        4) Cleanup in finally
        """
        location_id = None
        try:
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
        finally:
            self._delete_in_db(location_id)


if __name__ == '__main__':
    unittest.main()
