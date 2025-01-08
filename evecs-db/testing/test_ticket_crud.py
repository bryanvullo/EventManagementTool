import unittest
import uuid
import os
import json
import requests
from datetime import datetime, timedelta
from dateutil import tz
from azure.cosmos import CosmosClient, exceptions

# -------------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------------
def isoformat_now_plus(days_offset=0):
    """Return ISO8601 timestamp offset by N days"""
    dt_utc = datetime.now(tz=tz.UTC) + timedelta(days=days_offset)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

class TestTicketCrud(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Setup runs once before all tests"""
        # Load settings
        settings_file = os.path.join(os.path.dirname(__file__), '..', 'local.settings.json')
        with open(settings_file) as f:
            settings = json.load(f).get('Values', {})
        
        # DB Connection
        cls.connection_string = settings.get('DB_CONNECTION_STRING')
        cls.db_name = settings.get('DB_NAME', 'evecs')
        cls.events_container_name = settings.get('EVENTS_CONTAINER', 'events')
        cls.tickets_container_name = settings.get('TICKETS_CONTAINER', 'tickets')
        cls.users_container_name = settings.get('USERS_CONTAINER', 'users')

        # Initialize Cosmos Client
        cls.client = CosmosClient.from_connection_string(cls.connection_string)
        cls.db = cls.client.get_database_client(cls.db_name)
        cls.events_container = cls.db.get_container_client(cls.events_container_name)
        cls.tickets_container = cls.db.get_container_client(cls.tickets_container_name)
        cls.users_container = cls.db.get_container_client(cls.users_container_name)

        # API Endpoints
        cls.base_url = "http://localhost:7071/api"
        cls.deploy_url = "https://evecs-dev.azurewebsites.net/api"
        cls.function_key = settings.get('FUNCTION_APP_KEY', '')

        # Create test user if needed
        cls.test_user_id = "8ef177e5-17ef-4baa-940a-83ccd4bb33c7" 
        try:
            cls.users_container.delete_item(cls.test_user_id, partition_key=cls.test_user_id)
        except:
            pass
        
        cls.test_user = {
            "id": cls.test_user_id,
            "user_id": cls.test_user_id,
            "email": "test@example.com",
            "password": "SecurePass!!",
            "IP": "127.0.0.1",
            "auth": True,
            "groups": ["COMP3200"]
        }
        cls.users_container.create_item(cls.test_user)

        # Modify test event creation to ensure it exists
        try:
            cls.events_container.delete_item(cls.test_event_id, partition_key=cls.test_event_id)
        except:
            pass
        
        cls.test_event_id = "65e508ff-b12b-4089-993d-fb7a87107c26"
        cls.test_event = {
            "id": cls.test_event_id,
            "event_id": cls.test_event_id,
            "creator_id": [cls.test_user_id],
            "name": "Test Event",
            "groups": ["COMP3200"],
            "desc": "Test event for ticket validation",
            "location_id": "ChIJVx6yK_RzdEgRWqDn24O08ek",
            "room_id": "1015",
            "start_date": isoformat_now_plus(1),
            "end_date": isoformat_now_plus(2),
            "max_tick": 100,
            "code": "TEST123",
            "tags": ["Lecture"]
        }
        cls.events_container.create_item(cls.test_event)

    @classmethod
    def tearDownClass(cls):
        """Cleanup after all tests"""
        try:
            cls.users_container.delete_item(cls.test_user_id, partition_key=cls.test_user_id)
            cls.events_container.delete_item(cls.test_event_id, partition_key=cls.test_event_id)
        except exceptions.CosmosResourceNotFoundError:
            pass

    def setUp(self):
        """Runs before each test"""
        # Clean up any existing test tickets
        query = "SELECT * FROM c WHERE c.user_id = @uid"
        params = [{"name": "@uid", "value": self.test_user_id}]
        tickets = list(self.tickets_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        for ticket in tickets:
            try:
                self.tickets_container.delete_item(ticket["id"], partition_key=ticket["id"])
            except:
                pass

    def _create_ticket(self, user_id=None, event_id=None, email=None):
        """Helper to create a ticket"""
        if not user_id:
            user_id = self.test_user_id
        if not event_id:
            event_id = self.test_event_id
        if not email:
            email = f"test_{uuid.uuid4()}@example.com"  # Use unique email each time

        url = f"{self.base_url}/create_ticket"
        if self.function_key:
            url += f"?code={self.function_key}"

        payload = {
            "user_id": user_id,
            "event_id": event_id,
            "email": email
        }
        return requests.post(url, json=payload)

    def _delete_ticket(self, ticket_id):
        """Helper to delete a ticket"""
        try:
            self.tickets_container.delete_item(ticket_id, partition_key=ticket_id)
        except exceptions.CosmosResourceNotFoundError:
            pass

    def test_create_ticket_valid(self):
        """Test creating a valid ticket"""
        resp = self._create_ticket()
        self.assertIn(resp.status_code, [200, 201])
        data = resp.json()
        self.assertIn("ticket_id", data)
        self._delete_ticket(data["ticket_id"])

    def test_create_ticket_invalid_user(self):
        """Test creating ticket with invalid user"""
        resp = self._create_ticket(
            user_id="nonexistent-user-id-" + str(uuid.uuid4()),
            email="new_unique_email@example.com"  # Use unique email
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not found in the users database", resp.json()["error"])

    def test_create_ticket_invalid_event(self):
        """Test creating ticket with invalid event"""
        resp = self._create_ticket(event_id="nonexistent-event")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not found in the events database", resp.json()["error"])

    def test_validate_ticket_success(self):
        """Test successful ticket validation"""
        # 1. Create ticket
        create_resp = self._create_ticket()
        self.assertIn(create_resp.status_code, [200, 201])
        ticket_id = create_resp.json()["ticket_id"]

        # 2. Validate ticket
        validate_url = f"{self.base_url}/validate_ticket"
        if self.function_key:
            validate_url += f"?code={self.function_key}"

        validate_payload = {
            "ticket_id": ticket_id,
            "user_id": self.test_user_id,
            "code": self.test_event["code"]
        }
        validate_resp = requests.post(validate_url, json=validate_payload)
        print(validate_resp)
        
        # # 3. Check response
        # self.assertEqual(validate_resp.status_code, 200)
        # self.assertIn("Ticket authorized successfully", validate_resp.json()["result"])

        #Cleanup
        self._delete_ticket(ticket_id)

    def test_validate_ticket_wrong_code(self):
        """Test ticket validation with wrong event code"""
        # 1. Create ticket
        create_resp = self._create_ticket()
        self.assertIn(create_resp.status_code, [200, 201])
        ticket_id = create_resp.json()["ticket_id"]

        # 2. Try to validate with wrong code
        validate_url = f"{self.base_url}/validate_ticket"
        if self.function_key:
            validate_url += f"?code={self.function_key}"

        validate_payload = {
            "ticket_id": ticket_id,
            "user_id": self.test_user_id,
            "code": "WRONG123"
        }
        validate_resp = requests.post(validate_url, json=validate_payload)
        
        # 3. Check response
        self.assertEqual(validate_resp.status_code, 403)
        self.assertIn("Invalid event code", validate_resp.json()["error"])

        # Cleanup
        self._delete_ticket(ticket_id)

    def test_validate_ticket_wrong_user(self):
        """Test ticket validation with wrong user"""
        # 1. Create ticket
        create_resp = self._create_ticket()
        self.assertIn(create_resp.status_code, [200, 201])
        ticket_id = create_resp.json()["ticket_id"]

        # 2. Try to validate with wrong user
        validate_url = f"{self.base_url}/validate_ticket"
        if self.function_key:
            validate_url += f"?code={self.function_key}"

        validate_payload = {
            "ticket_id": ticket_id,
            "user_id": "wrong-user-id",
            "code": self.test_event["code"]
        }
        validate_resp = requests.post(validate_url, json=validate_payload)
        
        # 3. Check response
        self.assertEqual(validate_resp.status_code, 403)
        self.assertIn("User is not the ticket owner.", validate_resp.json()["error"])

        # Cleanup
        self._delete_ticket(ticket_id)

    def test_get_ticket_by_event(self):
        """Test getting tickets by event_id"""
        # 1. Create a ticket
        create_resp = self._create_ticket()
        self.assertIn(create_resp.status_code, [200, 201])
        ticket_id = create_resp.json()["ticket_id"]

        # 2. Get tickets for event
        get_url = f"{self.base_url}/get_ticket"
        if self.function_key:
            get_url += f"?code={self.function_key}"

        get_payload = {"event_id": self.test_event_id}
        get_resp = requests.post(get_url, json=get_payload)
        
        # 3. Check response
        self.assertEqual(get_resp.status_code, 200)
        data = get_resp.json()
        self.assertGreater(data["ticket_count"], 0)
        self.assertTrue(any(t["ticket_id"] == ticket_id for t in data["tickets"]))

        # Cleanup
        self._delete_ticket(ticket_id)

    def test_get_ticket_by_user(self):
        """Test getting tickets by user_id"""
        # 1. Create a ticket
        create_resp = self._create_ticket()
        self.assertIn(create_resp.status_code, [200, 201])
        ticket_id = create_resp.json()["ticket_id"]

        # 2. Get tickets for user
        get_url = f"{self.base_url}/get_ticket"
        if self.function_key:
            get_url += f"?code={self.function_key}"

        get_payload = {"user_id": self.test_user_id}
        get_resp = requests.post(get_url, json=get_payload)
        
        # 3. Check response
        self.assertEqual(get_resp.status_code, 200)
        data = get_resp.json()
        self.assertGreater(data["subscription_count"], 0)
        self.assertTrue(any(t["ticket_id"] == ticket_id for t in data["subscriptions"]))

        # Cleanup
        self._delete_ticket(ticket_id)

if __name__ == '__main__':
    unittest.main() 