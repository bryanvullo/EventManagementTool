import unittest
import requests
import json
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceExistsError, CosmosResourceNotFoundError
from azure.cosmos import CosmosClient

from pathlib import Path

class TestLogin(unittest.TestCase):   
    LOCAL_DEV_URL = "http://localhost:7071/api/"
    PUBLIC_URL = "https://evecs.azurewebsites.net/api/"
    TEST_FUNCTION = "login_user"
    TEST_URL = LOCAL_DEV_URL + TEST_FUNCTION
    
    path = Path(__file__).parent.parent / 'local.settings.json'
    with open(path) as settings_file:
        settings = json.load(settings_file)

    MyCosmos = CosmosClient.from_connection_string(settings['Values']['DB_CONNECTION_STRING'])
    EvecsDBProxy = MyCosmos.get_database_client(settings['Values']['DB_NAME'])
    UserContainerProxy = EvecsDBProxy.get_container_client(settings['Values']['USERS_CONTAINER'])
    FunctionAppKey = settings['Values']['FUNCTION_APP_KEY']

    testUser1 = {
        "email": "test-user@gmail.com",
        "password": "password123!!"
    }
    testUser2 = {
        "email": "test-user2@gmail.com",
        "password": "password123!!"
    }
    testUser3 = {
        "email": "test-user3@gmail.com",
        "password": "password123!!"
    }

    @classmethod
    def setUpClass(cls):
        response = requests.post(cls.TEST_URL, json=cls.testUser1, 
                                 headers={"x-functions-key": cls.FunctionAppKey})
        response = requests.post(cls.TEST_URL, json=cls.testUser2, 
                                 headers={"x-functions-key": cls.FunctionAppKey})
        response = requests.post(cls.TEST_URL, json=cls.testUser3, 
                                 headers={"x-functions-key": cls.FunctionAppKey})

    def test_login_valid_user1(self):
        response = requests.post(self.TEST_URL, json=self.testUser1, 
                                 headers={"x-functions-key": self.FunctionAppKey})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], f"User '{self.testUser1.get('email')}' has been logged in.")
    
    def test_login_valid_user2(self):
        response = requests.post(self.TEST_URL, json=self.testUser2, 
                                 headers={"x-functions-key": self.FunctionAppKey})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], f"User '{self.testUser2.get('email')}' has been logged in.")
    
    def test_empty_login(self):
        response = requests.post(self.TEST_URL, json={}, 
                                 headers={"x-functions-key": self.FunctionAppKey})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Email and password are required.")
    
    def test_empty_email(self):
        user = {
            "password": "password123!!"
        }
        response = requests.post(self.TEST_URL, json=user, 
                                 headers={"x-functions-key": self.FunctionAppKey})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Email and password are required.")
    
    def test_empty_password(self):
        user = {
            "email": "test-user@gmail.com"
        }
        response = requests.post(self.TEST_URL, json=user, 
                                 headers={"x-functions-key": self.FunctionAppKey})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Email and password are required.")
    
    def test_invalid_email(self):
        user = {
            "email": "invalid@gmail.com",
            "password": "password123!!"
        }
        response = requests.post(self.TEST_URL, json=user, 
                                 headers={"x-functions-key": self.FunctionAppKey})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], f"User with email '{user.get('email')}' not found.")
    
    def test_invalid_password(self):
        user = {
            "email": "test-user@gmail.com",
            "password": "wrongpassord!!"
        }
        response = requests.post(self.TEST_URL, json=user, 
                                 headers={"x-functions-key": self.FunctionAppKey})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Password is incorrect.")

    if __name__ == '__main__':
        unittest.main()
