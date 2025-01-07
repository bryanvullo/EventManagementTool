# NOTE: The following still need testing

# | event_crud.create_event
# |   1. check location_id is not null and exists in DB  ----
# |   2. check that the creator_id (user_id) is valid AND authorized  ----
# |   3. create tickets and test that they are added to tickets partition
# |   4. test that a created event is added to the locations partition

#--------------------------------------SETUP----------------------------------------
# test_event_crud.py
import unittest
import uuid
import os
import requests
import json
import jsonschema
from azure.cosmos import CosmosClient, exceptions
from datetime import datetime, timedelta
from dateutil import tz
from jsonschema.exceptions import ValidationError, SchemaError


settings_file = os.path.join(os.path.dirname(__file__), '..', 'local.settings.json')
with open(settings_file) as f:
    settings = json.load(f).get('Values', {})

for key, value in settings.items():
    os.environ[key] = value

# ----------------------------------TEST DOCS----------------------------------------
mock_user_doc_auth_true = {
    "user_id": "user_1234",
    "IP": "127.0.0.1",
    "email": "test@example.com",
    "auth": True,
    "password": "hashed_password"
}

mock_user_doc_auth_false = {
    "user_id": "user_5678",
    "IP": "192.168.1.100",
    "email": "noauth@example.com",
    "auth": False,
    "password": "hashed_password"
}

mock_location_doc = {
    "id": "loc_1234",
    "location_id": "loc_1234",
    "location_name": "Main Hall",
    "capacity": 100,
    "events_ids": []
}

# ---------------------------------------TESTS---------------------------------------
def isoformat_now_plus(days_offset=0):
    """
    Return a string in the format: yyyy-MM-ddTHH:mm:ss.ffffffZ
    (up to 6 fractional digits), always in UTC.
    """
    dt_utc = datetime.now(tz=tz.UTC) + timedelta(days=days_offset)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

class TestIntegrationCreateEvent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """
        setUpClass runs once before all tests.
        We establish a real CosmosClient and create references to our containers.
        We'll also prepare a test location and user in the DB (with auth=True).
        """

        # 1) Load environment vars (from local.settings.json or system env)
        cls.connection_string = os.environ.get("DB_CONNECTION_STRING")
        cls.db_name = os.environ.get("DB_NAME", "evecs")
        cls.events_container_name = os.environ.get("EVENTS_CONTAINER", "events")
        cls.locations_container_name = os.environ.get("LOCATIONS_CONTAINER", "locations")
        cls.users_container_name = os.environ.get("USERS_CONTAINER", "users")

        # 2) Initialize the CosmosClient
        cls.client = CosmosClient.from_connection_string(cls.connection_string)
        cls.db = cls.client.get_database_client(cls.db_name)
        cls.events_container = cls.db.get_container_client(cls.events_container_name)
        cls.locations_container = cls.db.get_container_client(cls.locations_container_name)
        cls.users_container = cls.db.get_container_client(cls.users_container_name)

        # 3) Insert a test location doc (for use in all tests)
        cls.location_id = f"loc_{uuid.uuid4()}"
        cls.location_doc = {
            "id": cls.location_id,
            "location_id": cls.location_id,
            "location_name": "Integration Test Location",
            "capacity": 500,
            "events_ids": []
        }
        cls.locations_container.create_item(cls.location_doc)

        # 4) Insert a test user doc with auth=True
        cls.user_id = f"user_{uuid.uuid4()}"
        cls.user_doc = {
            "id": cls.user_id,
            "user_id": cls.user_id,
            "IP": "127.0.0.1",
            "email": "authuser@example.com",
            "auth": True,
            "password": "hashed_password"
        }
        cls.users_container.create_item(cls.user_doc)

        # 5) Base URL for your deployed Azure Function App (no trailing slash)
        cls.base_url = "http://localhost:7071/api"
        cls.deployment_url = "https://evecs.azurewebsites.net/api"

        # 6) Load the function app key if needed (for Function-level auth)
        cls.function_key = os.environ.get("FUNCTION_APP_KEY", "")

        # 7) Event schema path
        cls.schema_path = os.path.join(os.path.dirname(__file__), '..', 'schemas', 'event.json')

    @classmethod
    def tearDownClass(cls):
        """
        tearDownClass runs once after all tests finish.
        Clean up the location and user docs we created in the DB.
        """
        try:
            cls.locations_container.delete_item(
                item=cls.location_id,
                partition_key=cls.location_id
            )
        except exceptions.CosmosResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error cleaning up location doc: {e}")

        try:
            cls.users_container.delete_item(
                item=cls.user_id,
                partition_key=cls.user_id
            )
        except exceptions.CosmosResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error cleaning up user doc: {e}")

    # ----------------------------------------------------------------
    # Helper: Build URL with function key
    # ----------------------------------------------------------------
    def _get_create_event_url(self) -> str:
        """
        Returns the 'create_event' endpoint with the function key appended.
        Example: https://evecs.azurewebsites.net/api/create_event?code=XYZ
        """
        return f"{self.base_url}/create_event?code={self.function_key}"

    # ----------------------------------------------------------------
    # 1. Test that DB/partition connections work.
    #    query on each container to ensure no exceptions.
    # ----------------------------------------------------------------
    def test_db_connection_check(self):
        try:
            list(self.events_container.read_all_items())
            list(self.locations_container.read_all_items())
            list(self.users_container.read_all_items())
            # If we got here, we're presumably able to connect. We'll just assert True.
            self.assertTrue(True)
        except Exception as e:
            self.fail(f"Database connection check failed: {e}")

    def test_events_schema(self):
        """
        Test that the 'event.json' file itself is valid JSON Schema (draft-07).
        """
        with open(self.schema_path, 'r') as f:
            schema = json.load(f)

        try:
            jsonschema.Draft7Validator.check_schema(schema)
        except SchemaError as e:
            print("Schema Error:", e)
            self.fail("Schema is not valid JSON Schema (draft-07)!")
        except Exception as e:
            self.fail(f"Unexpected error checking schema: {e}")

        # If we get here, schema is valid
        print("Schema passes draft-07 check!")

    def test_created_event_validation(self):
        """
        Test that a properly formed event document
        actually validates against 'event.json'.
        """
        with open(self.schema_path, 'r') as f:
            schema = json.load(f)

        valid_body = {
            "event_id": str(uuid.uuid4()),         # required
            "creator_id": ["creator_123"],         # array of strings
            "name": "Integration Test Event",
            "group": "lecture",
            "desc": "This is a valid event document.",
            "location_id": "loc_456",
            "start_date": isoformat_now_plus(1),   # Tomorrow
            "end_date": isoformat_now_plus(2),     # Day after tomorrow
            "max_tick": 100,
            "img_url": "https://example.com/image.png",
            "tags": ["lecture", "society"]
        }

        try:
            # Validate against the event schema
            jsonschema.validate(instance=valid_body, schema=schema)
            print("Valid event document passes schema validation!")
        except ValidationError as e:
            print("Validation Error:", e)
            self.fail("Document should be valid but failed validation!")
        except Exception as e:
            print("Unexpected Error:", e)
            self.fail("Unexpected error when validating document!")

    # ----------------------------------------------------------------
    # 3. Systematically test incorrect formatting & constraints
    # ----------------------------------------------------------------

    # 3.1. Check start_date < end_date
    def test_start_date_less_than_end_date(self):
        endpoint_url = self._get_create_event_url()
        bad_body = {
            "user_id": self.user_id,
            "name": "Event with optional fields",
            "group": "COMP3200",
            "desc": "Testing tags + valid URL",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(2),
            "end_date": isoformat_now_plus(1),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }
        resp = requests.post(endpoint_url, json=bad_body)
        self.assertEqual(resp.status_code, 400, f"Expected 400, got {resp.status_code}")
        data = resp.json()
        self.assertIn("Start date must be strictly before end date", data["error"])

    # 3.2. max_tick > 0 
    def test_max_tick_positive(self):
        endpoint_url = self._get_create_event_url()

        # max_tick = 0
        body_with_zero_tick = {
            "user_id": self.user_id,
            "name": "Event with optional fields",
            "group": "COMP3200",
            "desc": "Testing tags + valid URL",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 0,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }
        resp = requests.post(endpoint_url, json=body_with_zero_tick)
        self.assertEqual(resp.status_code, 400, f"Expected 400, got {resp.status_code}")
        self.assertIn("max_tick must be a number greater than 0.", resp.json()["error"])

    # 3.3. img_url must be a valid URL (or empty)
    def test_img_url_must_be_valid_or_empty(self):
        endpoint_url = self._get_create_event_url()
        body_invalid_url = {
            "user_id": self.user_id,
            "name": "Bad Img URL Event",
            "group": "lecture",
            "desc": "Invalid URL for image",
            "location_id": self.location_id,
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 10,
            "img_url": "not a real url"
        }
        resp = requests.post(endpoint_url, json=body_invalid_url)
        self.assertEqual(resp.status_code, 400, f"Expected 400, got {resp.status_code}")
        self.assertIn("img_url must be a valid URL or empty", resp.json()["error"])

    # 3.4. Check user.auth == True
    def test_user_auth_must_be_true(self):
        """
        Create a new user doc with auth=False, then attempt creation.
        """
        # Insert a user with auth=False
        bad_user_id = f"user_{uuid.uuid4()}"
        bad_user_doc = {
            "id": bad_user_id,
            "user_id": bad_user_id,
            "IP": "127.0.0.2",
            "email": "noauth@example.com",
            "auth": False,
            "password": "hashed_password"
        }
        self.users_container.create_item(bad_user_doc)

        endpoint_url = self._get_create_event_url()
        body = {
            "user_id": bad_user_id,
            "name": "Event with optional fields",
            "group": "COMP3200",
            "desc": "Testing tags + valid URL",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }
        resp = requests.post(endpoint_url, json=body)
        self.assertEqual(resp.status_code, 403, f"Expected 403, got {resp.status_code}")
        self.assertIn("is not authorized to create events", resp.json()["error"])

        # Clean up that user
        self._delete_user_in_db(bad_user_id)

    # 3.5. name and desc must be strings
    def test_name_and_desc_must_be_strings(self):
        endpoint_url = self._get_create_event_url()
        body = {
            "user_id": self.user_id,
            "name": 123,
            "group": "COMP3200",
            "desc": "Testing tags + valid URL",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }
        resp = requests.post(endpoint_url, json=body)
        self.assertEqual(resp.status_code, 400, f"Expected 400, got {resp.status_code}")
        error_msg = resp.json()["error"]
        self.assertIn("Event name must be a string", error_msg)

        # Now test desc
        body = {
            "user_id": self.user_id,
            "name": "Event with optional fields",
            "group": "COMP3200",
            "desc": 123,
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }

        resp = requests.post(endpoint_url, json=body)
        self.assertEqual(resp.status_code, 400, f"Expected 400, got {resp.status_code}")
        self.assertIn("Event description must be a string", resp.json()["error"])


    # 3.6. group must be in valid_groups
    def test_group_must_be_in_valid_groups(self):
        endpoint_url = self._get_create_event_url()
        body = {
            "user_id": self.user_id,
            "name": "Event with optional fields",
            "group": "random_group",
            "desc": "Testing tags + valid URL",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }
        resp = requests.post(endpoint_url, json=body)
        self.assertEqual(resp.status_code, 400, f"Expected 400, got {resp.status_code}")
        self.assertIn("Invalid event group 'random_group'", resp.json()["error"])

    # 3.7. tags must be a list of valid tags
    def test_tags_must_be_valid(self):
        endpoint_url = self._get_create_event_url()
        body = {
            "user_id": self.user_id,
            "name": "Event with optional fields",
            "group": "COMP3200",
            "desc": "Testing tags + valid URL",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", 123]
        }
        resp = requests.post(endpoint_url, json=body)
        self.assertEqual(resp.status_code, 400, f"Expected 400, got {resp.status_code}")
        self.assertIn("Each tag must be a string", resp.json()["error"])

        # Now test a string tag that isn't in valid_tags
        body["tags"] = ["lecture", "invalid_tag"]
        resp = requests.post(endpoint_url, json=body)
        self.assertEqual(resp.status_code, 400, f"Expected 400, got {resp.status_code}")
        self.assertIn("Invalid tag 'invalid_tag'", resp.json()["error"])

    # 3.8. Properly formatted event object with optional fields
    def test_correctly_formatted_event_with_optional_fields(self):
        endpoint_url = self._get_create_event_url()
        body = {
            "user_id": self.user_id,
            "name": "Event with optional fields",
            "group": "COMP3200",
            "desc": "Testing tags + valid URL",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }

        # POST to the endpoint
        resp = requests.post(endpoint_url, json=body)
        self.assertIn(resp.status_code, [200, 201], f"Expected 200 or 201, got {resp.status_code}")

        data = resp.json()
        self.assertEqual(data["result"], "success")

        # Here is the server-generated event_id
        server_event_id = data["event_id"]  
        self.assertTrue(server_event_id, "Returned event_id is empty.")

        # Confirm it is in the DB using the server's event_id
        try:
            # 1) Build a SQL query to find the event by server_event_id
            query = "SELECT * FROM c WHERE c.event_id = @event_id"
            params = [{"name": "@event_id", "value": server_event_id}]
            
            # 2) Execute the query
            items = list(self.events_container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))
            
            # 3) Check if any items were returned
            if not items:
                self.fail("Event not found in DB after creation.")
            
            # 4) Grab the first matching document
            event_doc = items[0]
            print(f"Event doc: {event_doc}")
            self.assertIsNotNone(event_doc)
            self.assertEqual(event_doc["tags"], body["tags"])
        
        except exceptions.CosmosHttpResponseError as e:
            self.fail(f"An error occurred while querying the DB: {str(e)}")

        #Cleanup
        self._delete_event_in_db(server_event_id)

    # ----------------------------------------------------------------
    # Helper Methods
    # ----------------------------------------------------------------
    def _delete_event_in_db(self, event_id: str):
        try:
            self.events_container.delete_item(event_id, partition_key=event_id)
        except exceptions.CosmosResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error cleaning up event '{event_id}': {e}")

    def _delete_user_in_db(self, user_id: str):
        try:
            self.users_container.delete_item(user_id, partition_key=user_id)
        except exceptions.CosmosResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error cleaning up user '{user_id}': {e}")


if __name__ == '__main__':
    unittest.main()