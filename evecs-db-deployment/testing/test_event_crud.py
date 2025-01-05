# NOTE: The following still need testing

# | event_crud.create_event
# |   1. check location_id is not null and exists in DB  ----
# |   2. check that the creator_id (user_id) is valid AND authorized  ----
# |   3. create tickets and test that they are added to tickets partition
# |   4. test that a created event is added to the locations partition

#--------------------------------------SETUP----------------------------------------
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


#----------------------------------TEST DOCS----------------------------------------

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

#---------------------------------------TESTS----------------------------------------- 
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
            "id": cls.location_id,            # 'id' is the partition key
            "location_id": cls.location_id,   # to match your schema
            "location_name": "Integration Test Location",
            "capacity": 500,
            "events_ids": []
        }
        cls.locations_container.create_item(cls.location_doc)

        # 4) Insert a test user doc with auth=True
        cls.user_id = f"user_{uuid.uuid4()}"
        cls.user_doc = {
            "id": cls.user_id,     # partition key
            "user_id": cls.user_id,
            "IP": "127.0.0.1",
            "email": "authuser@example.com",
            "auth": True,
            "password": "hashed_password"
        }
        cls.users_container.create_item(cls.user_doc)

        # 5) Base URL for your local Function App
        cls.base_url = "http://localhost:7071/api"

        # 6) Event schema path
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
            "type": "lecture",
            "desc": "This is a valid event document.",
            "location_id": "loc_456",
            "start_date": isoformat_now_plus(1),   # Tomorrow
            "end_date": isoformat_now_plus(2),     # Day after tomorrow
            "max_tick": 100,
            "max_tick_pp": 5,
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
    #    We show representative tests; you can replicate for each
    #    required field & scenario.
    # ----------------------------------------------------------------

    # 3.1. Check start_date < end_date
    def test_start_date_less_than_end_date(self):
        endpoint_url = f"{self.base_url}/create_event"
        bad_body = {
            "user_id": self.user_id,
            "name": "Bad date event",
            "type": "lecture",
            "desc": "Start >= End",
            "location_id": self.location_id,
            "start_date": isoformat_now_plus(2),  # +2 days
            "end_date": isoformat_now_plus(1),    # +1 day
            "max_tick": 100,
            "max_tick_pp": 2
        }
        resp = requests.post(endpoint_url, json=bad_body)
        self.assertEqual(resp.status_code, 400)  # Or whatever your code returns for invalid
        data = resp.json()
        self.assertIn("Start date must be strictly before end date", data["error"])

    # 3.2. max_tick and max_tick_pp must be > 0
    def test_max_tick_and_max_tick_pp_positive(self):
        endpoint_url = f"{self.base_url}/create_event"

        # max_tick = 0
        body_with_zero_tick = {
            "user_id": self.user_id,
            "name": "Zero Tick Event",
            "type": "lecture",
            "desc": "max_tick is zero",
            "location_id": self.location_id,
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 0,
            "max_tick_pp": 1
        }
        resp = requests.post(endpoint_url, json=body_with_zero_tick)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("max_tick must be greater than 0", resp.json()["error"])

        # max_tick_pp = 0
        body_with_zero_tick_pp = {
            "user_id": self.user_id,
            "name": "Zero Tick PP Event",
            "type": "lecture",
            "desc": "max_tick_pp is zero",
            "location_id": self.location_id,
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 10,
            "max_tick_pp": 0
        }
        resp = requests.post(endpoint_url, json=body_with_zero_tick_pp)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("max_tick_pp must be greater than 0", resp.json()["error"])

    # 3.3. img_url must be a valid URL (or empty)
    def test_img_url_must_be_valid_or_empty(self):
        endpoint_url = f"{self.base_url}/create_event"
        body_invalid_url = {
            "user_id": self.user_id,
            "name": "Bad Img URL Event",
            "type": "lecture",
            "desc": "Invalid URL for image",
            "location_id": self.location_id,
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 10,
            "max_tick_pp": 2,
            "img_url": "not a real url"
        }
        resp = requests.post(endpoint_url, json=body_invalid_url)
        self.assertEqual(resp.status_code, 400)
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

        endpoint_url = f"{self.base_url}/create_event"
        body = {
            "user_id": bad_user_id,
            "name": "Unauthorized Event",
            "type": "lecture",
            "desc": "Should fail",
            "location_id": self.location_id,
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 10,
            "max_tick_pp": 1
        }
        resp = requests.post(endpoint_url, json=body)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("is not authorized to create events", resp.json()["error"])

        # Clean up that user
        self._delete_user_in_db(bad_user_id)

    # 3.5. name and desc must be strings
    def test_name_and_desc_must_be_strings(self):
        endpoint_url = f"{self.base_url}/create_event"
        body = {
            "user_id": self.user_id,
            "name": 12345,  # not a string
            "type": "lecture",
            "desc": ["not", "a", "string"],
            "location_id": self.location_id,
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 10,
            "max_tick_pp": 5
        }
        resp = requests.post(endpoint_url, json=body)
        self.assertEqual(resp.status_code, 400)
        error_msg = resp.json()["error"]
        # The function checks name first, so we'll see that error
        self.assertIn("Event name must be a string", error_msg)

    # 3.6. type must be in valid_types
    def test_type_must_be_in_valid_types(self):
        endpoint_url = f"{self.base_url}/create_event"
        body = {
            "user_id": self.user_id,
            "name": "Bad Type",
            "type": "random_type",
            "desc": "This event has invalid type",
            "location_id": self.location_id,
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 10,
            "max_tick_pp": 5
        }
        resp = requests.post(endpoint_url, json=body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid event type 'random_type'", resp.json()["error"])

    # 3.7. tags must be a list of valid tags
    def test_tags_must_be_valid(self):
        endpoint_url = f"{self.base_url}/create_event"
        body = {
            "user_id": self.user_id,
            "name": "Tags Test",
            "type": "lecture",
            "desc": "Invalid tags",
            "location_id": self.location_id,
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 10,
            "max_tick_pp": 5,
            "tags": ["lecture", 123]  # 123 is not a string
        }
        resp = requests.post(endpoint_url, json=body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Each tag must be a string", resp.json()["error"])

        # Now test a string tag that isn't in valid_tags
        body["tags"] = ["lecture", "invalid_tag"]
        resp = requests.post(endpoint_url, json=body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid tag 'invalid_tag'", resp.json()["error"])

    # 3.8. Properly formatted event object with optional fields
    #      verifies it was actually created (similar to test_proper_event_object_creation).
    def test_correctly_formatted_event_with_optional_fields(self):
        endpoint_url = f"{self.base_url}/create_event"
        body = {
            "event_id": str(uuid.uuid4()),       
            "user_id": self.user_id,
            "name": "Event with optional fields",
            "type": "lecture",
            "desc": "Testing tags + valid URL",
            "location_id": self.location_id,
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "max_tick_pp": 2,
            "tags": ["lecture", "music"],
            "img_url": "https://example.com/event.png"
        }
        print("TEST-BODY: ", body, "\n")
        resp = requests.post(endpoint_url, json=body)
        print("TEST-RESP: ", resp, "\n")
        self.assertIn(resp.status_code, [200, 201])
        data = resp.json()
        print ("TEST DATA: ", data, "\n")
        self.assertEqual(data["result"], "success")
        self.assertIn("event_id", data)
        event_id = data["event_id"]

        # Confirm it is in the DB
        try:
            event_doc = self.events_container.read_item(event_id, partition_key=event_id)
            self.assertIsNotNone(event_doc)
            self.assertEqual(event_doc["tags"], body["tags"])
        except exceptions.CosmosResourceNotFoundError:
            self.fail("Event not found in DB after creation.")

        # Cleanup
        self._delete_event_in_db(event_id)

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
