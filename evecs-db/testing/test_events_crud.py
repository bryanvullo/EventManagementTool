# test_events_crud.py
import unittest
import uuid
import os
import json
import requests
from datetime import datetime, timedelta
from dateutil import tz
import jsonschema
from jsonschema.exceptions import ValidationError, SchemaError
from azure.cosmos import CosmosClient, exceptions

# -------------------------------------------------------------------------
# 1) Load environment variables (local.settings.json or system environment)
# -------------------------------------------------------------------------
settings_file = os.path.join(os.path.dirname(__file__), '..', 'local.settings.json')
if os.path.exists(settings_file):
    with open(settings_file) as f:
        local_settings = json.load(f).get('Values', {})
    for key, value in local_settings.items():
        os.environ[key] = value

# -------------------------------------------------------------------------
# 2) Helper Functions for date/time
# -------------------------------------------------------------------------
def isoformat_now_plus(days_offset=0):
    """
    Return a string in the format: yyyy-MM-ddTHH:mm:ss.ffffffZ
    (up to 6 fractional digits), always in UTC, offset by N days.
    """
    dt_utc = datetime.now(tz=tz.UTC) + timedelta(days=days_offset)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def isoformat_fixed(year, month, day, hour, minute):
    """
    Produce a fixed ISO8601 string in UTC for a specific date/time.
    """
    dt_utc = datetime(year, month, day, hour, minute, tzinfo=tz.UTC)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# =============================================================================
#                             TEST CLASS 1: CREATE
# =============================================================================
class TestCreateEvent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """
        setUpClass runs once before all tests in this class.
        We establish a CosmosClient connection and prepare test data.
        """
        # 1) Retrieve environment variables
        cls.connection_string = os.environ.get("DB_CONNECTION_STRING")
        cls.db_name = os.environ.get("DB_NAME", "evecs")
        cls.events_container_name = os.environ.get("EVENTS_CONTAINER", "events")
        cls.locations_container_name = os.environ.get("LOCATIONS_CONTAINER", "locations")
        cls.users_container_name = os.environ.get("USERS_CONTAINER", "users")

        # 2) Initialize the CosmosClient and containers
        cls.client = CosmosClient.from_connection_string(cls.connection_string)
        cls.db = cls.client.get_database_client(cls.db_name)
        cls.events_container = cls.db.get_container_client(cls.events_container_name)
        cls.locations_container = cls.db.get_container_client(cls.locations_container_name)
        cls.users_container = cls.db.get_container_client(cls.users_container_name)

        # 3) Base URL for the Function App endpoint
        cls.base_url = "http://localhost:7071/api"
        cls.function_key = os.environ.get("FUNCTION_APP_KEY", "")
        if cls.function_key:
            cls.create_event_url = f"{cls.base_url}/create_event?code={cls.function_key}"
        else:
            cls.create_event_url = f"{cls.base_url}/create_event"

        # 4) Insert a user doc with auth=True for testing
        cls.user_id = str(uuid.uuid4())
        cls.user_doc = {
            "id": cls.user_id,
            "user_id": cls.user_id,
            "IP": "127.0.0.1",
            "email": "authuser@example.com",
            "auth": True,
            "password": "hashed_password"
        }
        cls.users_container.create_item(cls.user_doc)

        # 5) Path to local event schema (optional, if used in tests)
        cls.schema_path = os.path.join(os.path.dirname(__file__), '..', 'schemas', 'event.json')

        # 6) Known existing location in DB for use in tests
        cls.location_id = "ChIJVx6yK_RzdEgRWqDn24O08ek"
        cls.room_id_1015 = "1015"

    @classmethod
    def tearDownClass(cls):
        """
        tearDownClass runs once after all tests in this class.
        Clean up user doc we created.
        """
        try:
            cls.users_container.delete_item(cls.user_id, partition_key=cls.user_id)
        except exceptions.CosmosResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error cleaning up user doc: {e}")

    def _delete_event_in_db(self, event_id: str):
        """
        Helper to remove a created event from the events container.
        """
        try:
            self.events_container.delete_item(event_id, partition_key=event_id)
        except exceptions.CosmosResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error cleaning up event '{event_id}': {e}")

    # ----------------------------------------------------------------
    # Tests
    # ----------------------------------------------------------------
    def test_db_connection_check(self):
        """
        Test that we can read items from each container (basic connectivity).
        """
        try:
            list(self.events_container.read_all_items())
            list(self.locations_container.read_all_items())
            list(self.users_container.read_all_items())
            self.assertTrue(True)
        except Exception as e:
            self.fail(f"Database connection check failed: {e}")

    def test_events_schema(self):
        """
        Verify that the 'event.json' file is a valid JSON Schema (Draft-07).
        """
        with open(self.schema_path, 'r') as f:
            schema = json.load(f)
        try:
            jsonschema.Draft7Validator.check_schema(schema)
        except SchemaError as e:
            self.fail(f"Schema is not valid under Draft 7! Error: {e}")
        except Exception as e:
            self.fail(f"Unexpected error checking schema: {e}")

    def test_created_event_validation(self):
        """
        Test that a properly formed event document passes the schema.
        """
        with open(self.schema_path, 'r') as f:
            schema = json.load(f)

        valid_body = {
            "event_id": str(uuid.uuid4()),
            "creator_id": [str(uuid.uuid4())],
            "name": "Integration Test Event",
            "groups": ["COMP3200", "COMP3666"],
            "desc": "This is a valid event document.",
            "location_id": "loc_456",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 100,
            "img_url": "https://example.com/image.png",
            "tags": ["lecture", "society"]
        }

        try:
            jsonschema.validate(instance=valid_body, schema=schema)
        except ValidationError as e:
            self.fail(f"Document should be valid but failed validation: {e}")
        except Exception as e:
            self.fail(f"Unexpected error during validation: {e}")

    def test_start_date_less_than_end_date(self):
        body = {
            "user_id": self.user_id,
            "name": "Event with optional fields",
            "groups": ["COMP3200"],
            "desc": "Testing date check",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(2),
            "end_date": isoformat_now_plus(1),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }
        resp = requests.post(self.create_event_url, json=body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Start date must be strictly before end date", resp.json()["error"])

    def test_max_tick_positive(self):
        body = {
            "user_id": self.user_id,
            "name": "Event with zero max_tick",
            "groups": ["COMP3200"],
            "desc": "Testing max_tick constraint",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1015",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 0,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }
        resp = requests.post(self.create_event_url, json=body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("max_tick must be a number greater than 0.", resp.json()["error"])

    def test_img_url_must_be_valid_or_empty(self):
        body_invalid_url = {
            "user_id": self.user_id,
            "name": "Bad Img URL Event",
            "groups": ["COMP3200"],
            "desc": "Invalid URL for image",
            "location_id": self.location_id,
            "room_id": "1015",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 10,
            "img_url": "not a real url"
        }
        resp = requests.post(self.create_event_url, json=body_invalid_url)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("JSON schema validation error", resp.json()["error"])

    def test_user_auth_must_be_true(self):
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

        body = {
            "user_id": bad_user_id,
            "name": "Event with optional fields",
            "groups": ["COMP3200"],
            "desc": "Testing auth",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }
        resp = requests.post(self.create_event_url, json=body)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("is not authorized to create events", resp.json()["error"])

        # Clean up
        try:
            self.users_container.delete_item(bad_user_id, partition_key=bad_user_id)
        except:
            pass

    def test_name_and_desc_must_be_strings(self):
        body = {
            "user_id": self.user_id,
            "name": 123,
            "groups": ["COMP3200"],
            "desc": "Testing name/desc",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }
        resp = requests.post(self.create_event_url, json=body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Event name must be a string", resp.json()["error"])

        body["name"] = "Event with optional fields"
        body["desc"] = 123
        resp = requests.post(self.create_event_url, json=body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Event description must be a string", resp.json()["error"])

    def test_group_must_be_in_valid_groups(self):
        # Here we intentionally pass 'group' (singular) to test missing 'groups'
        bad_body = {
            "user_id": self.user_id,
            "name": "Event with optional fields",
            "group": ["random_group"],
            "desc": "Testing groups field",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }
        resp = requests.post(self.create_event_url, json=bad_body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Missing mandatory field(s): ['groups']", resp.json()["error"])

    def test_tags_must_be_valid(self):
        body = {
            "user_id": self.user_id,
            "name": "Event with optional fields",
            "groups": ["COMP3200"],
            "desc": "Testing tags",
            "location_id": "ChIJhbfAkaBzdEgRii3AIRj1Qp4",
            "room_id": "1001",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", 123]
        }
        resp = requests.post(self.create_event_url, json=body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Each tag must be a string", resp.json()["error"])

        body["tags"] = ["lecture", "invalid_tag"]
        resp = requests.post(self.create_event_url, json=body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid tag 'invalid_tag'", resp.json()["error"])

    def test_correctly_formatted_event_with_optional_fields(self):
        body = {
            "user_id": self.user_id,
            "name": "Event with optional fields",
            "groups": ["COMP3200"],
            "desc": "Testing tags + valid URL",
            "location_id": self.location_id,
            "room_id": self.room_id_1015,
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 20,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture", "music"]
        }
        resp = requests.post(self.create_event_url, json=body)
        self.assertIn(resp.status_code, [200, 201])
        data = resp.json()
        self.assertEqual(data["result"], "success")
        server_event_id = data["event_id"]
        self.assertTrue(server_event_id)

        # Confirm it is in the DB
        query = "SELECT * FROM c WHERE c.event_id = @event_id"
        params = [{"name": "@event_id", "value": server_event_id}]
        items = list(self.events_container.query_items(
            query=query, parameters=params, enable_cross_partition_query=True
        ))
        if not items:
            self.fail("Event not found in DB after creation.")
        event_doc = items[0]
        self.assertEqual(event_doc["tags"], body["tags"])

        # Cleanup
        self._delete_event_in_db(server_event_id)

    def test_check9_exceeding_room_capacity(self):
        """
        Attempt to add event with max_tick > room.capacity (which is 40 in test doc).
        """
        body = {
            "user_id": self.user_id,
            "name": "Event Exceeding Capacity",
            "groups": ["COMP3200"],
            "desc": "Testing room capacity check",
            "location_id": self.location_id,
            "room_id": "3077",  # capacity=40
            "start_date": isoformat_fixed(2025, 5, 16, 12, 0),
            "end_date": isoformat_fixed(2025, 5, 16, 13, 0),
            "max_tick": 50,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture"]
        }
        resp = requests.post(self.create_event_url, json=body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot exceed room capacity", resp.text)

    def test_check9_within_room_capacity(self):
        """
        Attempt to add event with max_tick <= room.capacity => should succeed.
        """
        body = {
            "user_id": self.user_id,
            "name": "Event Within Capacity",
            "groups": ["COMP3200"],
            "desc": "Testing capacity check OK",
            "location_id": self.location_id,
            "room_id": "3077",  # capacity=40
            "start_date": isoformat_fixed(2025, 5, 16, 12, 0),
            "end_date": isoformat_fixed(2025, 5, 16, 13, 0),
            "max_tick": 30,
            "img_url": "https://example.com/event.png",
            "tags": ["lecture"]
        }
        resp = requests.post(self.create_event_url, json=body)
        self.assertIn(resp.status_code, [200, 201, 202])
        if resp.status_code in [200, 201, 202]:
            event_id = resp.json().get("event_id")
            if event_id:
                self._delete_event_in_db(event_id)

    def test_check10_event_time_conflict(self):
        """
        Attempt to add event overlapping an existing event in the test DB.
        """
        body = {
            "user_id": self.user_id,
            "name": "Overlapping Event Test",
            "groups": ["COMP3200"],
            "desc": "This event overlaps the existing one",
            "location_id": self.location_id,
            "room_id": self.room_id_1015,
            "start_date": "2024-05-16T11:30:00Z",
            "end_date": "2024-05-16T13:00:00Z",
            "max_tick": 20,
            "img_url": "https://example.com/overlap.png",
            "tags": ["lecture"]
        }
        resp = requests.post(self.create_event_url, json=body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already booked", resp.text)

    def test_check10_no_event_time_conflict(self):
        """
        Attempt to add event that does NOT overlap the existing event.
        """
        body = {
            "user_id": self.user_id,
            "name": "Non-conflicting Event Test",
            "groups": ["COMP3200"],
            "desc": "Starts exactly at 12:00 so no overlap",
            "location_id": self.location_id,
            "room_id": self.room_id_1015,
            "start_date": "2024-05-16T12:00:00Z",
            "end_date": "2024-05-16T13:00:00Z",
            "max_tick": 20,
            "img_url": "https://example.com/no_conflict.png",
            "tags": ["lecture"]
        }
        resp = requests.post(self.create_event_url, json=body)
        self.assertIn(resp.status_code, [200, 201, 202])
        if resp.status_code in [200, 201, 202]:
            event_id = resp.json().get("event_id")
            if event_id:
                self._delete_event_in_db(event_id)


# =============================================================================
#                     TEST CLASS 2: UPDATE & DELETE
# =============================================================================
class TestIntegrationEventUpdateDelete(unittest.TestCase):
    """
    Covers:
      - Deleting an event with correct/incorrect inputs
      - Updating an event with correct/incorrect inputs
    """

    @classmethod
    def setUpClass(cls):
        """
        Runs once before all tests in this class.
        """
        # 1) Env Vars
        cls.connection_string = os.environ.get("DB_CONNECTION_STRING")
        cls.db_name = os.environ.get("DB_NAME", "evecs")
        cls.events_container_name = os.environ.get("EVENTS_CONTAINER", "events")
        cls.locations_container_name = os.environ.get("LOCATIONS_CONTAINER", "locations")
        cls.users_container_name = os.environ.get("USERS_CONTAINER", "users")

        # 2) Cosmos
        cls.client = CosmosClient.from_connection_string(cls.connection_string)
        cls.db = cls.client.get_database_client(cls.db_name)
        cls.events_container = cls.db.get_container_client(cls.events_container_name)
        cls.locations_container = cls.db.get_container_client(cls.locations_container_name)
        cls.users_container = cls.db.get_container_client(cls.users_container_name)

        # 3) Known location info
        cls.location_id = "ChIJVx6yK_RzdEgRWqDn24O08ek"
        cls.room_id_1015 = "1015"

        # 4) Create user with auth=True
        cls.user_id = "f451d5ef-47b0-47b0-8999-6687e3e4b13f"
        cls.user_doc = {
            "id": cls.user_id,
            "user_id": cls.user_id,
            "IP": "127.0.0.1",
            "email": "test@example.com",
            "auth": True,
            "password": "hashed_password",
            "groups": ["COMP3200"]
        }
        try:
            cls.users_container.create_item(cls.user_doc)
        except exceptions.CosmosResourceExistsError:
            pass

        # 5) Build function endpoints
        cls.base_url = "http://localhost:7071/api"
        cls.function_key = os.environ.get("FUNCTION_APP_KEY", "")
        if cls.function_key:
            cls.create_event_url = f"{cls.base_url}/create_event?code={cls.function_key}"
            cls.update_event_url = f"{cls.base_url}/update_event?code={cls.function_key}"
            cls.delete_event_url = f"{cls.base_url}/delete_event?code={cls.function_key}"
        else:
            cls.create_event_url = f"{cls.base_url}/create_event"
            cls.update_event_url = f"{cls.base_url}/update_event"
            cls.delete_event_url = f"{cls.base_url}/delete_event"

    @classmethod
    def tearDownClass(cls):
        """
        Remove test user from DB.
        """
        try:
            cls.users_container.delete_item(item=cls.user_id, partition_key=cls.user_id)
        except exceptions.CosmosResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error cleaning up user doc: {e}")

    def _create_test_event(self, body=None):
        """
        Helper to create a valid event in the DB by calling create_event.
        Returns (resp, data).
        """
        if body is None:
            body = {
                "user_id": self.user_id,
                "name": "Test Event Delete",
                "groups": ["COMP3200"],
                "desc": "Testing delete logic",
                "location_id": self.location_id,
                "room_id": self.room_id_1015,
                "start_date": isoformat_now_plus(1),
                "end_date": isoformat_now_plus(2),
                "max_tick": 20,
                "img_url": "https://example.com/event.png",
                "tags": ["lecture", "music"]
            }
        resp = requests.post(self.create_event_url, json=body)
        try:
            data = resp.json()
        except:
            data = {}
        return resp, data

    def _delete_event_in_db(self, event_id: str):
        """
        Directly remove event from DB (cleanup).
        """
        try:
            self.events_container.delete_item(event_id, partition_key=event_id)
        except exceptions.CosmosResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error cleaning up event '{event_id}': {e}")

    # ---------------------------------------------------------------------
    # 1) Valid delete
    # ---------------------------------------------------------------------
    def test_delete_event_correct_inputs(self):
        event_id = None
        try:
            resp, data = self._create_test_event()
            self.assertIn(resp.status_code, [200, 201])
            event_id = data.get("event_id")
            self.assertIsNotNone(event_id)

            # Now delete
            delete_payload = {
                "event_id": event_id,
                "user_id": self.user_id
            }
            del_resp = requests.post(self.delete_event_url, json=delete_payload)
            self.assertIn(del_resp.status_code, [200, 202])

            # Verify gone
            query = "SELECT * FROM c WHERE c.event_id = @event_id"
            params = [{"name": "@event_id", "value": event_id}]
            items = list(self.events_container.query_items(
                query=query, parameters=params, enable_cross_partition_query=True
            ))
            self.assertEqual(len(items), 0, "Event document should be removed from DB.")
        finally:
            if event_id:
                self._delete_event_in_db(event_id)

    # ---------------------------------------------------------------------
    # 2) Invalid delete
    # ---------------------------------------------------------------------
    def test_delete_event_incorrect_inputs(self):
        event_id = None
        try:
            # Create event
            resp, data = self._create_test_event()
            self.assertIn(resp.status_code, [200, 201])
            event_id = data.get("event_id")
            self.assertIsNotNone(event_id)

            # A) Missing user_id
            del_resp_a = requests.post(self.delete_event_url, json={"event_id": event_id})
            self.assertEqual(del_resp_a.status_code, 400)

            # B) Wrong user_id
            del_payload_b = {"event_id": event_id, "user_id": str(uuid.uuid4())}
            del_resp_b = requests.post(self.delete_event_url, json=del_payload_b)
            self.assertEqual(del_resp_b.status_code, 403)

            # C) Invalid event_id
            del_payload_c = {"event_id": "some_wrong_id", "user_id": self.user_id}
            del_resp_c = requests.post(self.delete_event_url, json=del_payload_c)
            self.assertEqual(del_resp_c.status_code, 404)
        finally:
            if event_id:
                self._delete_event_in_db(event_id)

    # ---------------------------------------------------------------------
    # 3) Update with correct inputs
    # ---------------------------------------------------------------------
    def test_update_event_correct_inputs(self):
        event_id = None
        try:
            # 1) Create
            resp, data = self._create_test_event()
            self.assertIn(resp.status_code, [200, 201])
            event_id = data.get("event_id")
            self.assertIsNotNone(event_id)

            # 2) Update
            update_body = {
                "event_id": event_id,
                "user_id": self.user_id,
                "name": "Updated Event Name",
                "desc": "Updated description",
                "tags": ["lecture"]
            }
            up_resp = requests.post(self.update_event_url, json=update_body)
            self.assertIn(up_resp.status_code, [200, 202])

            # 3) Validate
            query = "SELECT * FROM c WHERE c.event_id = @event_id"
            params = [{"name": "@event_id", "value": event_id}]
            items = list(self.events_container.query_items(
                query=query, parameters=params, enable_cross_partition_query=True
            ))
            self.assertTrue(len(items) > 0)
            updated_doc = items[0]
            self.assertEqual(updated_doc["name"], "Updated Event Name")
            self.assertEqual(updated_doc["desc"], "Updated description")
            self.assertEqual(updated_doc["tags"], ["lecture"])
        finally:
            if event_id:
                self._delete_event_in_db(event_id)

    # ---------------------------------------------------------------------
    # 4) Update with incorrect inputs
    # ---------------------------------------------------------------------
    def test_update_event_incorrect_inputs(self):
        event_id = None
        try:
            # 1) Create
            resp, data = self._create_test_event()
            self.assertIn(resp.status_code, [200, 201])
            event_id = data.get("event_id")
            self.assertIsNotNone(event_id)

            # Scenarios
            test_payloads = [
                # A) Missing user_id
                ({"event_id": event_id}, 400, "Missing event_id or user_id"),
                # B) user_id not in creator_id
                ({"event_id": event_id, "user_id": str(uuid.uuid4())}, 403, "Unauthorized"),
                # C) start_date >= end_date
                ({
                    "event_id": event_id,
                    "user_id": self.user_id,
                    "start_date": isoformat_now_plus(2),
                    "end_date": isoformat_now_plus(2)
                 }, 400, "Start date must be strictly before end date"),
                # D) Non-string name
                ({"event_id": event_id, "user_id": self.user_id, "name": 123}, 400, "Event name must be a string."),
                # E) Non-string desc
                ({"event_id": event_id, "user_id": self.user_id, "desc": 123}, 400, "Event description must be a string."),
                # F) Negative max_tick
                ({"event_id": event_id, "user_id": self.user_id, "max_tick": -1}, 400, "must be greater than 0"),
                # G) Zero max_tick
                ({"event_id": event_id, "user_id": self.user_id, "max_tick": 0}, 400, "must be greater than 0"),
                # H) Invalid tags
                ({"event_id": event_id, "user_id": self.user_id, "tags": ["lecture", "invalid_tag"]}, 400, "Invalid tag 'invalid_tag'"),
                # I) Invalid group
                ({"event_id": event_id, "user_id": self.user_id, "groups": ["FakeGroup"]}, 400, "Invalid event group")
            ]

            for i, (body_, exp_status, exp_error_frag) in enumerate(test_payloads, start=1):
                with self.subTest(f"Update scenario {i} => {body_}"):
                    up_resp = requests.post(self.update_event_url, json=body_)
                    self.assertEqual(up_resp.status_code, exp_status)
                    resp_json = up_resp.json()
                    self.assertIn(exp_error_frag, resp_json.get("error", ""))
        finally:
            if event_id:
                self._delete_event_in_db(event_id)

    def test_db_connection_check(self):
        """
        Quick check for container connectivity.
        """
        try:
            list(self.events_container.read_all_items())
            list(self.locations_container.read_all_items())
            list(self.users_container.read_all_items())
            self.assertTrue(True)
        except Exception as e:
            self.fail(f"Database connection check failed: {e}")


# =============================================================================
#                        TEST CLASS 3: GET EVENT
# =============================================================================
class TestGetEvent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 1) Env Vars
        cls.connection_string = os.environ.get("DB_CONNECTION_STRING")
        cls.db_name = os.environ.get("DB_NAME", "evecs")
        cls.events_container_name = os.environ.get("EVENTS_CONTAINER", "events")
        cls.users_container_name = os.environ.get("USERS_CONTAINER", "users")
        cls.tickets_container_name = os.environ.get("TICKETS_CONTAINER", "tickets")

        # 2) Cosmos
        cls.client = CosmosClient.from_connection_string(cls.connection_string)
        cls.db = cls.client.get_database_client(cls.db_name)
        cls.events_container = cls.db.get_container_client(cls.events_container_name)
        cls.users_container = cls.db.get_container_client(cls.users_container_name)
        cls.tickets_container = cls.db.get_container_client(cls.tickets_container_name)

        # 3) Known existing event/user from sample data
        cls.existing_event_id = "54c7ff11-ae76-4644-a34b-e2966f4dbedb"
        cls.existing_user_id = "e99c0fc0-e800-48db-b358-c9106f216a12"

        # 4) Build get_event URL
        cls.base_url = "http://localhost:7071/api/get_event"
        cls.function_key = os.environ.get("FUNCTION_APP_KEY", "")
        if cls.function_key:
            cls.base_url += f"?code={cls.function_key}"

        # 5) Attempt a quick DB check
        try:
            _ = list(cls.events_container.read_all_items())
            _ = list(cls.users_container.read_all_items())
            _ = list(cls.tickets_container.read_all_items())
        except Exception as e:
            print(f"Warning: Issue accessing the test DB containers: {e}")

        # 6) Clean up old test tickets for known user/event
        cls._delete_test_tickets_for_user_event(cls.existing_user_id, cls.existing_event_id)

    @classmethod
    def tearDownClass(cls):
        cls._delete_test_tickets_for_user_event(cls.existing_user_id, cls.existing_event_id)

    @classmethod
    def _delete_test_tickets_for_user_event(cls, user_id, event_id):
        try:
            query = "SELECT * FROM c WHERE c.user_id = @uid AND c.event_id = @eid"
            params = [
                {"name": "@uid", "value": user_id},
                {"name": "@eid", "value": event_id},
            ]
            tickets = list(
                cls.tickets_container.query_items(
                    query=query, parameters=params, enable_cross_partition_query=True
                )
            )
            for t in tickets:
                cls.tickets_container.delete_item(t["id"], partition_key=t["id"])
        except Exception as e:
            print(f"Error deleting test tickets for user '{user_id}', event '{event_id}': {e}")

    def _create_ticket_for_user_event(self, user_id, event_id, email="testticket@example.com"):
        """
        Helper to create a ticket for (user_id, event_id).
        If TICKET_FUNC_URL is not set, we directly insert into the DB.
        """
        ticket_url = os.environ.get("TICKET_FUNC_URL")
        if not ticket_url:
            # Direct insertion
            new_ticket_id = str(uuid.uuid4())
            ticket_doc = {
                "id": new_ticket_id,
                "ticket_id": new_ticket_id,
                "user_id": user_id,
                "event_id": event_id,
                "email": email
            }
            self.tickets_container.create_item(ticket_doc)
            return new_ticket_id

        # Otherwise call create_ticket endpoint
        payload = {
            "user_id": user_id,
            "event_id": event_id,
            "email": email
        }
        resp = requests.post(ticket_url, json=payload)
        if resp.status_code not in [200, 201]:
            self.fail(f"Failed to create ticket: {resp.status_code} => {resp.text}")
        return resp.json().get("ticket_id")

    # -------------------------------------------------------------------------
    # SCENARIO 1: No user_id and no event_id => Return ALL events
    # -------------------------------------------------------------------------
    def test_scenario1_no_input_returns_all_events(self):
        resp = requests.get(self.base_url)
        self.assertIn(resp.status_code, [200, 404])

        if resp.status_code == 404:
            # Means no events in DB
            db_items = list(self.events_container.query_items(
                query="SELECT * FROM c", enable_cross_partition_query=True
            ))
            if len(db_items) == 0:
                self.assertIn("No events found", resp.text)
            else:
                self.fail("get_event returned 404 but the DB actually has events!")
            return

        data = resp.json()
        self.assertIn("events", data)
        returned_events = data["events"]
        db_items = list(self.events_container.query_items(
            query="SELECT * FROM c", enable_cross_partition_query=True
        ))
        self.assertEqual(len(returned_events), len(db_items))

    # -------------------------------------------------------------------------
    # SCENARIO 2: Only user_id => Return all events the user is subscribed to
    # -------------------------------------------------------------------------
    def test_scenario2_only_user_id(self):
        # Create a ticket
        ticket_id = self._create_ticket_for_user_event(self.existing_user_id, self.existing_event_id)

        # GET with user_id
        url = self.base_url
        if self.function_key:
            url = f"{self.base_url}&user_id={self.existing_user_id}"
        else:
            url = f"{self.base_url}?user_id={self.existing_user_id}"

        resp = requests.get(url)
        self.assertIn(resp.status_code, [200, 404])

        if resp.status_code == 404:
            self.fail("get_event returned 404 though a ticket was created for the user.")

        data = resp.json()
        self.assertIn("events", data)
        returned_events = data["events"]
        # Expect at least 1 event with matching ID
        matching = [ev for ev in returned_events if ev["event_id"] == self.existing_event_id]
        self.assertTrue(matching, "User-subscribed events do not include the event we just created a ticket for.")

        # Cleanup
        try:
            self.tickets_container.delete_item(ticket_id, partition_key=ticket_id)
        except exceptions.CosmosResourceNotFoundError:
            pass

    # -------------------------------------------------------------------------
    # SCENARIO 3: Only event_id => Return that event
    # -------------------------------------------------------------------------
    def test_scenario3_only_event_id(self):
        # Good event_id
        url = self.base_url
        if self.function_key:
            url += f"&event_id={self.existing_event_id}"
        else:
            url += f"?event_id={self.existing_event_id}"

        resp = requests.get(url)
        self.assertIn(resp.status_code, [200, 404])
        if resp.status_code == 404:
            self.fail(f"get_event returned 404 for existing event_id={self.existing_event_id}.")

        data = resp.json()
        self.assertEqual(data["event_id"], self.existing_event_id)

        # Random event_id => 404
        random_id = str(uuid.uuid4())
        url2 = self.base_url
        if self.function_key:
            url2 += f"&event_id={random_id}"
        else:
            url2 += f"?event_id={random_id}"

        resp_nf = requests.get(url2)
        self.assertEqual(resp_nf.status_code, 404)

    # -------------------------------------------------------------------------
    # SCENARIO 4: Both user_id and event_id => Return the event if user is subscribed
    # -------------------------------------------------------------------------
    def test_scenario4_user_id_and_event_id(self):
        ticket_id = self._create_ticket_for_user_event(self.existing_user_id, self.existing_event_id)

        # user_id & event_id
        url = self.base_url
        if self.function_key:
            url += f"&user_id={self.existing_user_id}&event_id={self.existing_event_id}"
        else:
            url += f"?user_id={self.existing_user_id}&event_id={self.existing_event_id}"

        resp = requests.get(url)
        self.assertIn(resp.status_code, [200, 404])
        if resp.status_code == 404:
            self.fail("get_event returned 404 but user has a ticket for that event.")

        data = resp.json()
        self.assertEqual(data["event_id"], self.existing_event_id)

        # Cleanup
        try:
            self.tickets_container.delete_item(ticket_id, partition_key=ticket_id)
        except exceptions.CosmosResourceNotFoundError:
            pass

        # 4B) Now user has no ticket => expect 404
        random_eid = str(uuid.uuid4())
        url2 = self.base_url
        if self.function_key:
            url2 += f"&user_id={self.existing_user_id}&event_id={random_eid}"
        else:
            url2 += f"?user_id={self.existing_user_id}&event_id={random_eid}"

        resp_nf = requests.get(url2)
        self.assertEqual(resp_nf.status_code, 404)


# =============================================================================
#                 NEW TEST CLASS 4: GET VALID GROUPS & TAGS
# =============================================================================
class TestGetGroupsTags(unittest.TestCase):
    """
    Tests the endpoints /get_valid_groups and /get_valid_tags
    to ensure they return the correct lists of groups and tags
    as defined in events_crud.py.
    """

    @classmethod
    def setUpClass(cls):
        """
        setUpClass runs once for this class.
        We only need to define the endpoint URLs for groups and tags.
        """
        cls.base_url = "http://localhost:7071/api"
        cls.function_key = os.environ.get("FUNCTION_APP_KEY", "")

        if cls.function_key:
            cls.get_valid_groups_url = f"{cls.base_url}/get_valid_groups?code={cls.function_key}"
            cls.get_valid_tags_url = f"{cls.base_url}/get_valid_tags?code={cls.function_key}"
        else:
            cls.get_valid_groups_url = f"{cls.base_url}/get_valid_groups"
            cls.get_valid_tags_url = f"{cls.base_url}/get_valid_tags"

        # Optionally, define the known "valid_groups" and "valid_tags" we expect:
        cls.expected_groups = [
            "COMP3200", "COMP3227", "COMP3228", "COMP3269",
            "COMP3420", "COMP3666", "COMP3229", "Sports"
        ]
        cls.expected_tags = [
            "lecture", "society", "leisure", "sports", "music", "sex"
        ]

    def test_get_valid_groups(self):
        """
        Verify the /get_valid_groups endpoint returns a 200 status
        and a JSON body containing a "groups" list matching the
        groups in events_crud.py.
        """
        resp = requests.get(self.get_valid_groups_url)
        self.assertEqual(resp.status_code, 200, f"Expected 200, got {resp.status_code}")

        data = resp.json()
        self.assertIn("groups", data, "Response JSON must contain 'groups' key")

        returned_groups = data["groups"]
        self.assertIsInstance(returned_groups, list, "groups should be a list")

        # Check that the returned list matches what we expect
        # For a strict match:
        self.assertEqual(sorted(returned_groups), sorted(self.expected_groups),
                         "The returned groups do not match the expected list.")

    def test_get_valid_tags(self):
        """
        Verify the /get_valid_tags endpoint returns a 200 status
        and a JSON body containing a "tags" list matching the
        tags in events_crud.py.
        """
        resp = requests.get(self.get_valid_tags_url)
        self.assertEqual(resp.status_code, 200, f"Expected 200, got {resp.status_code}")

        data = resp.json()
        self.assertIn("tags", data, "Response JSON must contain 'tags' key")

        returned_tags = data["tags"]
        self.assertIsInstance(returned_tags, list, "tags should be a list")

        # For a strict match:
        self.assertEqual(sorted(returned_tags), sorted(self.expected_tags),
                         "The returned tags do not match the expected list.")


# =============================================================================
#  MAIN
# =============================================================================
if __name__ == '__main__':
    unittest.main()
