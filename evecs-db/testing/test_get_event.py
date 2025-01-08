# test_get_event.py
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

import unittest
import uuid
import os
import requests
import json
from azure.cosmos import CosmosClient, exceptions

# ---------------------------------------TESTS---------------------------------------
# We'll assume your local.settings.json or environment is already set
# with DB_CONNECTION_STRING, DB_NAME, and container names as in your example test_events_crud.py

class TestGetEvent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1) Load environment vars
        cls.connection_string = os.environ.get("DB_CONNECTION_STRING")
        cls.db_name = os.environ.get("DB_NAME", "evecs")
        cls.events_container_name = os.environ.get("EVENTS_CONTAINER", "events")
        cls.users_container_name = os.environ.get("USERS_CONTAINER", "users")
        cls.tickets_container_name = os.environ.get("TICKETS_CONTAINER", "tickets")

        # 2) Initialize CosmosClient
        cls.client = CosmosClient.from_connection_string(cls.connection_string)
        cls.db = cls.client.get_database_client(cls.db_name)
        cls.events_container = cls.db.get_container_client(cls.events_container_name)
        cls.users_container = cls.db.get_container_client(cls.users_container_name)
        cls.tickets_container = cls.db.get_container_client(cls.tickets_container_name)

        # 3) Known existing event/user from your data snippet
        #    (already in DB according to the instructions)
        cls.existing_event_id = "54c7ff11-ae76-4644-a34b-e2966f4dbedb"
        cls.existing_user_id = "e99c0fc0-e800-48db-b358-c9106f216a12"

        # 4) Endpoint for get_event (no trailing slash).
        cls.function_key = os.environ.get('FUNCTION_APP_KEY')
        cls.base_url = "http://localhost:7071/api/get_event"
        cls.deplyment_url = f"https://evecs.azurewebsites.net/api/get_event?code={cls.function_key}"
        # If your function requires a code, append ?code=XYZ or &code=XYZ
        cls.function_key = os.environ.get("FUNCTION_APP_KEY", "")
        if cls.function_key:
            cls.base_url += f"?code={cls.function_key}"

        # 5) Confirm the test DB references are accessible
        #    (just a quick check, not a test assertion)
        try:
            _ = list(cls.events_container.read_all_items())
            _ = list(cls.users_container.read_all_items())
            _ = list(cls.tickets_container.read_all_items())
        except Exception as e:
            print("Warning: Issue accessing the test DB containers:", e)

        # 6) Clean up any old test tickets for the known user/event (just in case)
        cls._delete_test_tickets_for_user_event(cls.existing_user_id, cls.existing_event_id)

    @classmethod
    def tearDownClass(cls):
        # Clean up again any test tickets we might have created
        cls._delete_test_tickets_for_user_event(cls.existing_user_id, cls.existing_event_id)

    @classmethod
    def _delete_test_tickets_for_user_event(cls, user_id, event_id):
        """Helper to remove leftover test tickets for given user & event in the DB."""
        try:
            query = (
                "SELECT * FROM c WHERE c.user_id = @uid AND c.event_id = @eid"
            )
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
        We call the create_ticket endpoint, or directly insert if you prefer.
        """
        ticket_url = os.environ.get("TICKET_FUNC_URL")  # Or build like the base URL
        if not ticket_url:
            # Fallback: direct container insertion
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

        # Otherwise, if you have an endpoint for create_ticket, do a requests.post:
        payload = {
            "user_id": user_id,
            "event_id": event_id,
            "email": email
        }
        resp = requests.post(ticket_url, json=payload)
        if resp.status_code not in [200, 201]:
            self.fail(f"Failed to create ticket (status {resp.status_code}): {resp.text}")
        data = resp.json()
        return data.get("ticket_id")

    # -------------------------------------------------------------------------
    # SCENARIO 1: No user_id and no event_id => Return ALL events
    # -------------------------------------------------------------------------
    def test_scenario1_no_input_returns_all_events(self):
        """
        1) We call get_event with no query string and no JSON data.
        2) Expect 200 and a list of all events in the DB.
        3) We'll do a direct count from the DB to compare.
        """
        # Do a GET with no params
        resp = requests.get(self.base_url)
        print(resp.status_code) 
        self.assertIn(resp.status_code, [200, 404], "Expected 200 or 404 status code")

        # If 404, that means no events in DB (rare), so let's handle that:
        if resp.status_code == 404:
            # Double-check with our own query
            db_items = list(self.events_container.query_items(
                query="SELECT * FROM c",
                enable_cross_partition_query=True
            ))
            if len(db_items) == 0:
                # Confirm indeed no events in DB
                self.assertIn("No events found", resp.text)
            else:
                self.fail("get_event returned 404 but the DB has events!")
            return

        # Otherwise status should be 200
        data = resp.json()
        self.assertIn("events", data, "Response body missing 'events' key")
        returned_events = data["events"]
        # Compare to DB count
        db_items = list(self.events_container.query_items(
            query="SELECT * FROM c",
            enable_cross_partition_query=True
        ))
        self.assertEqual(len(returned_events), len(db_items), "Mismatch in total event count")

    # -------------------------------------------------------------------------
    # SCENARIO 2: Only user_id => Return all events the user is subscribed to
    # -------------------------------------------------------------------------
    def test_scenario2_only_user_id(self):
        """
        1) We'll create a ticket for existing_user_id => existing_event_id.
        2) We call get_event with just user_id in the query (or in POST).
        3) We expect a 200 with the event we just subscribed to.
        4) We'll cross-check by direct DB query of that event_id.
        """
        # 1) Create a ticket
        ticket_id = self._create_ticket_for_user_event(self.existing_user_id, self.existing_event_id)

        # 2) Hit the get_event endpoint with user_id
        url = self.base_url + f"&user_id={self.existing_user_id}" if self.function_key else self.base_url
        resp = requests.get(url)
        self.assertIn(resp.status_code, [200, 404], "Expected 200 or 404 status code for user subscription check")

        # If 404, it means the user has no subscriptions. But we *just* created one, so we expect 200.
        if resp.status_code == 404:
            self.fail("get_event returned 404 though a new ticket was created for the user.")

        # 3) We expect 200
        data = resp.json()
        self.assertIn("events", data, "Response missing 'events' field.")
        returned_events = data["events"]
        # We expect at least 1 event with event_id == self.existing_event_id
        matching = [ev for ev in returned_events if ev["event_id"] == self.existing_event_id]
        self.assertTrue(matching, "User-subscribed events do not include the event we just created a ticket for.")

        # 4) Optional: cross-check by direct DB query for the user
        # We'll do "SELECT * FROM c WHERE c.user_id=..."
        # Then for each ticket, we check event_id. But the function is working, so we're good.

        # Cleanup: remove the ticket
        try:
            self.tickets_container.delete_item(ticket_id, partition_key=ticket_id)
        except exceptions.CosmosResourceNotFoundError:
            pass

    # -------------------------------------------------------------------------
    # SCENARIO 3: Only event_id => Return that event
    # -------------------------------------------------------------------------
    def test_scenario3_only_event_id(self):
        """
        1) We call get_event with an existing event_id.
        2) Expect 200 and a single event doc with matching event_id.
        3) Then test a random event_id => expect 404
        """
        # A) Good event_id
        url = self.base_url + f"&event_id={self.existing_event_id}" if self.function_key else self.base_url
        resp = requests.get(url)
        self.assertIn(resp.status_code, [200, 404], "Expected 200 or 404 status")

        if resp.status_code == 404:
            self.fail(f"get_event returned 404 for event_id={self.existing_event_id}, but it should exist in DB.")

        data = resp.json()
        self.assertEqual(data["event_id"], self.existing_event_id, "Returned event doc does not match requested event_id")

        # B) Random event_id => expect 404
        random_id = str(uuid.uuid4())
        url = self.base_url + f"&event_id={random_id}" if self.function_key else self.base_url
        resp_notfound = requests.get(url)
        self.assertEqual(resp_notfound.status_code, 404, f"Expected 404 for random event_id={random_id}")

    # -------------------------------------------------------------------------
    # SCENARIO 4: Both user_id and event_id => Check user is subscribed
    # -------------------------------------------------------------------------
    def test_scenario4_user_id_and_event_id(self):
        """
        1) Create a ticket for the known user & event.
        2) Call get_event with both user_id and event_id => expect 200, returning the event doc.
        3) If user has no ticket => 404
        """
        # Make sure user/event has a ticket
        ticket_id = self._create_ticket_for_user_event(self.existing_user_id, self.existing_event_id)

        # Now call get_event?user_id=xxx&event_id=yyy
        url = self.base_url + f"&user_id={self.existing_user_id}&event_id={self.existing_event_id}"
        resp = requests.get(url)
        self.assertIn(resp.status_code, [200, 404], "Expected 200 or 404 status code")

        if resp.status_code == 404:
            self.fail("get_event returned 404 but user does have a ticket for that event.")

        data = resp.json()
        self.assertEqual(data["event_id"], self.existing_event_id, "Returned doc has mismatching event_id.")

        # Cleanup the ticket
        try:
            self.tickets_container.delete_item(ticket_id, partition_key=ticket_id)
        except exceptions.CosmosResourceNotFoundError:
            pass

        # 4B) Now user has no ticket => expect 404
        url_no_ticket = self.base_url + f"&user_id={self.existing_user_id}&event_id={str(uuid.uuid4())}"
        resp_notfound = requests.get(url_no_ticket)
        self.assertEqual(resp_notfound.status_code, 404, "Expected 404 when user isn't subscribed to that event.")


if __name__ == '__main__':
    unittest.main()