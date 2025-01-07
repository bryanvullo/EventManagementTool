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

class TestLocationEdit(unittest.TestCase):

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
    
    def _get_edit_location_url(self) -> str:
        return f"{self.base_url}/edit_location"


    # ----------------------------------------------------------------
    # 4A. Create a valid location, then edit with valid fields
    # ----------------------------------------------------------------
    def test_edit_location_valid(self):
        """
        1) Create a valid location
        2) Edit it with valid JSON data (e.g. change location_name)
        3) Expect success
        """
        location_id = str(uuid.uuid4())
        create_body = {
            "location_id": location_id,
            "location_name": "Edit Test DELETE ME",
            "events_ids": [
                {"event_id": EXISTING_EVENT_ID_2}
            ],
            "rooms": []
        }
        # Create
        resp_create = requests.post(self._get_create_location_url(), json=create_body)
        self.assertIn(resp_create.status_code, [200, 201, 202], f"Create failed with {resp_create.status_code}.")

        # find the location doc in cosmos db
        check_query = (
            "SELECT * FROM c "
            "WHERE c.location_name = @loc_name"
        )
        check_params = [
            {"name": "@loc_name", "value": create_body["location_name"]}
        ]
        existing_docs = list(
            self.locations_container.query_items(
                query=check_query,
                parameters=check_params,
                enable_cross_partition_query=True
            )
        )
        self.assertEqual(len(existing_docs), 1, "Expected to find the location doc in Cosmos DB.")
        doc = existing_docs[0]
        location_id = doc["location_id"]

        # Edit: We'll rename the location
        edit_body = {
            "location_id": location_id,
            "location_name": "Edited Building Name DELETE ME"
        }
        resp_edit = requests.post(self._get_edit_location_url(), json=edit_body)
        self.assertIn(resp_edit.status_code, [200, 201], f"Edit returned unexpected code: {resp_edit.status_code}")
        edit_data = resp_edit.json()
        self.assertIn("location", edit_data, "Expected the updated location doc in response.")
        self.assertEqual(edit_data["location"]["location_name"], edit_body["location_name"])

        for doc in self.locations_container.read_all_items():
            if doc['location_id'] == location_id:
                self.locations_container.delete_item(item=doc, partition_key=doc['location_id'])
                break

    # ----------------------------------------------------------------
    # 4B. Create a valid location, then edit with invalid JSON data
    # ----------------------------------------------------------------
    # def test_edit_location_invalid(self):
    #     """
    #     1) Create a valid location
    #     2) Try to edit with invalid data (e.g. 'rooms' as a string).
    #     3) Expect an error (400).
    #     """
    #     location_id = f"loc_edit_inv_{uuid.uuid4()}"
    #     create_body = {
    #         "location_id": location_id,
    #         "location_name": "Edit Test Invalid",
    #         "events_ids": [
    #             {"event_id": EXISTING_EVENT_ID_1}
    #         ],
    #         "rooms": []
    #     }
    #     # Create
    #     resp_create = requests.post(self._get_create_location_url(), json=create_body)
    #     self.assertIn(resp_create.status_code, [200, 201, 202])

    #     # Edit with invalid field
    #     edit_body = {
    #         "location_id": location_id,
    #         "rooms": "NotAnArray"   # invalid type
    #     }
    #     resp_edit = requests.post(self._get_edit_location_url(), json=edit_body)
    #     self.assertEqual(resp_edit.status_code, 400, f"Expected 400, got {resp_edit.status_code}.")
    #     error_data = resp_edit.json()
    #     self.assertIn("error", error_data)

if __name__ == '__main__':
    unittest.main()
