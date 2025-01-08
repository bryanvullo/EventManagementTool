# test_event_ud.py
import unittest
import uuid
import os
import random
import requests
import json
import jsonschema
from azure.cosmos import CosmosClient, exceptions
from datetime import datetime, timedelta
from dateutil import tz
from jsonschema.exceptions import ValidationError, SchemaError


# -------------------------------------------------------------------------
# Load environment variables from local.settings.json or system environment
# -------------------------------------------------------------------------
settings_file = os.path.join(os.path.dirname(__file__), '..', 'local.settings.json')
with open(settings_file) as f:
    settings = json.load(f).get('Values', {})

for key, value in settings.items():
    os.environ[key] = value


# -------------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------------
def isoformat_now_plus(days_offset=0):
    """
    Return a string in the format: yyyy-MM-ddTHH:%M:%S.%fZ
    always in UTC, offset by N days.
    """
    dt_utc = datetime.now(tz=tz.UTC) + timedelta(days=days_offset)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def isoformat_fixed(year, month, day, hour, minute):
    """
    Helper to produce a fixed ISO8601 string in UTC for a specific date/time.
    """
    dt_utc = datetime(year, month, day, hour, minute, tzinfo=tz.UTC)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TestIntegrationEventUpdateDelete(unittest.TestCase):
    """
    This test suite covers:
      1) Deleting an event with correct inputs (expect success, 2xx).
      2) Deleting an event with incorrect inputs (expect 4xx).
      3) Updating an event with correct inputs (expect success, 2xx).
      4) Updating an event with incorrect inputs (expect various 4xx).
    """

    @classmethod
    def setUpClass(cls):
        """
        setUpClass runs once before all tests.
        We establish a real CosmosClient and container references, plus
        prepare a valid user and location doc in the DB if needed.
        """
        # 1) Load environment vars
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

        # 3) We assume the valid location doc is present in DB or upserted. 
        cls.location_id = "ChIJVx6yK_RzdEgRWqDn24O08ek"
        cls.room_id_1015 = "1015"

        # 4) Create (or ensure) a test user with auth=True to allow event creation.
        #    Adjust as needed for your environment if user already exists or is created externally.
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
            # If already in DB, ignore
            pass

        # 5) Base URL for your deployed or local Azure Function App
        cls.base_url = "http://localhost:7071/api"
        cls.function_key = os.environ.get("FUNCTION_APP_KEY", "")  # if using function-level auth

        # Pre-build the function endpoints
        cls.create_event_url = cls._build_url(cls, "create_event")
        cls.update_event_url = cls._build_url(cls, "update_event")
        cls.delete_event_url = cls._build_url(cls, "delete_event")

    @classmethod
    def tearDownClass(cls):
        """
        tearDownClass runs once after all tests complete.
        Remove the test user from the DB if needed.
        """
        try:
            cls.users_container.delete_item(item=cls.user_id, partition_key=cls.user_id)
        except exceptions.CosmosResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error cleaning up user doc: {e}")

    @staticmethod
    def _build_url(self, endpoint_name):
        if self.function_key:
            return f"{self.base_url}/{endpoint_name}?code={self.function_key}"
        return f"{self.base_url}/{endpoint_name}"

    def _create_test_event(self, body=None):
        """
        Utility method to create a valid event in the DB
        by calling the create_event function endpoint.
        Returns (response_obj, response_json_dict).
        """
        if body is None:
            body = {
                "user_id": self.user_id,
                "name": "Test Event Delete",
                "groups": ["COMP3200"],
                "desc": "Testing tags + valid URL",
                "location_id": self.location_id,
                "room_id": self.room_id_1015,
                "start_date": isoformat_now_plus(random.randint(1, 10)),
                "end_date": isoformat_now_plus(random.randint(11, 20)),
                "max_tick": 20,
                "img_url": "https://example.com/event.png",
                "tags": ["lecture", "music"]
            }

        resp = requests.post(self.create_event_url, json=body)
        try:
            data = resp.json()
        except Exception:
            data = {}
        return resp, data

    def _delete_event_in_db(self, event_id: str):
        """
        Directly removes the event from Cosmos DB if it exists (cleanup helper).
        """
        try:
            self.events_container.delete_item(event_id, partition_key=event_id)
        except exceptions.CosmosResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error cleaning up event '{event_id}': {e}")

    # ---------------------------------------------------------------------
    # 1) Add valid event and delete it with correct inputs (expect 2xx code)
    # ---------------------------------------------------------------------
    def test_delete_event_correct_inputs(self):
        event_id = None
        try:
            # Step 1: Create event
            resp, data = self._create_test_event()
            print(resp.json())
            self.assertIn(resp.status_code, [200, 201],
                          f"Create event should succeed, got {resp.status_code}")

            event_id = data.get("event_id")
            self.assertIsNotNone(event_id, "Expected event_id in response body.")

            # Step 2: Call delete_event with correct user_id
            delete_payload = {
                "event_id": event_id,
                "user_id": self.user_id
            }
            del_resp = requests.post(self.delete_event_url, json=delete_payload)
            print(del_resp.json())
            self.assertIn(del_resp.status_code, [200, 202],
                          f"Expected 2xx, got {del_resp.status_code}")

            # Step 3: If your delete_event function truly removes the doc from DB, verify it's gone
            query = "SELECT * FROM c WHERE c.event_id = @event_id"
            params = [{"name": "@event_id", "value": event_id}]
            items = list(self.events_container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))
            # We expect no items if the item was truly deleted
            self.assertEqual(len(items), 0, "Event document should be removed from DB.")
        finally:
            # Always attempt cleanup
            if event_id:
                self._delete_event_in_db(event_id)

    # -----------------------------------------------------------------------
    # 2) Add a valid event and delete it with incorrect inputs (expect 4xx)
    # -----------------------------------------------------------------------
    def test_delete_event_incorrect_inputs(self):
        event_id = None
        try:
            # Step 1: Create event
            resp, data = self._create_test_event()
            self.assertIn(resp.status_code, [200, 201],
                          f"Create event should succeed, got {resp.status_code}")

            event_id = data.get("event_id")
            self.assertIsNotNone(event_id, "Expected event_id in response body.")

            # Case A: Missing user_id
            del_resp_a = requests.post(self.delete_event_url, json={"event_id": event_id})
            self.assertEqual(del_resp_a.status_code, 400,
                             f"Expected 400, got {del_resp_a.status_code}")

            # Case B: Wrong user_id (not in event_doc['creator_ids'])
            del_payload_b = {"event_id": event_id, "user_id": str(uuid.uuid4())}  # not the event's creator
            del_resp_b = requests.post(self.delete_event_url, json=del_payload_b)
            self.assertEqual(del_resp_b.status_code, 403,
                             f"Expected 403, got {del_resp_b.status_code}")

            # Case C: Invalid event_id
            del_payload_c = {"event_id": "some_wrong_id", "user_id": self.user_id}
            del_resp_c = requests.post(self.delete_event_url, json=del_payload_c)
            self.assertEqual(del_resp_c.status_code, 404,
                             f"Expected 404, got {del_resp_c.status_code}")
        finally:
            # Clean up the valid event
            if event_id:
                self._delete_event_in_db(event_id)

    # -------------------------------------------------------------------------
    # 3) Add a valid event and edit (update) it using correct inputs (expect 2xx)
    # -------------------------------------------------------------------------
    def test_update_event_correct_inputs(self):
        event_id = None
        try:
            # 1) Create event
            resp, data = self._create_test_event()
            self.assertIn(resp.status_code, [200, 201],
                          f"Create event should succeed, got {resp.status_code}")
            event_id = data.get("event_id")
            self.assertIsNotNone(event_id, "Expected event_id in response body.")

            # 2) Update with correct fields
            update_body = {
                "event_id": event_id,
                "user_id": self.user_id,  # must match creator_id
                "name": "Updated Event Name",
                "desc": "Updated description",
                "tags": ["lecture"],  # valid tags
            }
            up_resp = requests.post(self.update_event_url, json=update_body)
            self.assertIn(up_resp.status_code, [200, 202],
                          f"Expected 2xx, got {up_resp.status_code}")

            # 3) Validate changes in DB
            query = "SELECT * FROM c WHERE c.event_id = @event_id"
            params = [{"name": "@event_id", "value": event_id}]
            items = list(self.events_container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))
            self.assertTrue(len(items) > 0, "Updated event not found in DB.")
            updated_doc = items[0]
            self.assertEqual(updated_doc["name"], "Updated Event Name")
            self.assertEqual(updated_doc["desc"], "Updated description")
            self.assertEqual(updated_doc["tags"], ["lecture"])
        finally:
            # Cleanup
            if event_id:
                self._delete_event_in_db(event_id)

    # ------------------------------------------------------------------------------------
    # 4) Add a valid event and edit (update) it using all combos of incorrect inputs (4xx)
    # ------------------------------------------------------------------------------------
    def test_update_event_incorrect_inputs(self):
        event_id = None
        try:
            # 1) Create event
            resp, data = self._create_test_event()
            self.assertIn(resp.status_code, [200, 201],
                          f"Create event should succeed, got {resp.status_code}")
            event_id = data.get("event_id")
            self.assertIsNotNone(event_id, "Expected event_id in response body.")

            # We'll store test payloads in a list of (payload, expected_status, err_snippet)
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
                ({
                    "event_id": event_id,
                    "user_id": self.user_id,
                    "name": 123
                }, 400, "Event name must be a string."),
                # E) Non-string desc
                ({
                    "event_id": event_id,
                    "user_id": self.user_id,
                    "desc": 123
                }, 400, "Event description must be a string."),
                # F) Negative max_tick
                ({
                    "event_id": event_id,
                    "user_id": self.user_id,
                    "max_tick": -1
                }, 400, "must be greater than 0"),
                # G) Zero max_tick
                ({
                    "event_id": event_id,
                    "user_id": self.user_id,
                    "max_tick": 0
                }, 400, "must be greater than 0"),
                # H) Invalid tags (not in valid_tags)
                ({
                    "event_id": event_id,
                    "user_id": self.user_id,
                    "tags": ["lecture", "invalid_tag"]
                }, 400, "Invalid tag 'invalid_tag'"),
                # I) Passing new groups that are not in valid_groups
                ({
                    "event_id": event_id,
                    "user_id": self.user_id,
                    "groups": ["FakeGroup"]
                }, 400, "Invalid event group")
            ]

            for i, (body, exp_status, exp_error_frag) in enumerate(test_payloads, start=1):
                with self.subTest(f"Update scenario {i} => {body}"):
                    up_resp = requests.post(self.update_event_url, json=body)
                    print(up_resp.json())
                    self.assertEqual(up_resp.status_code, exp_status,
                                     f"[Scenario {i}] Expected {exp_status}, got {up_resp.status_code}")
                    # Check error message snippet
                    resp_json = up_resp.json()
                    self.assertIn(exp_error_frag, resp_json.get("error", ""),
                                  f"[Scenario {i}] Expected error snippet '{exp_error_frag}' in {resp_json}")
        finally:
            # Cleanup
            if event_id:
                self._delete_event_in_db(event_id)

    # ---------------------------------------------------------------------
    # Basic connectivity test (optional)
    # ---------------------------------------------------------------------
    def test_db_connection_check(self):
        """
        Verifies that we can query items from the containers without error.
        """
        try:
            list(self.events_container.read_all_items())
            list(self.locations_container.read_all_items())
            list(self.users_container.read_all_items())
            self.assertTrue(True)
        except Exception as e:
            self.fail(f"Database connection check failed: {e}")


if __name__ == '__main__':
    unittest.main()
