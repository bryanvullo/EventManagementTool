import unittest
import requests
import json
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceExistsError, CosmosResourceNotFoundError
from azure.cosmos import CosmosClient

from pathlib import Path

class TestRegister(unittest.TestCase):   
    LOCAL_DEV_URL = "http://localhost:7071/api/"
    PUBLIC_URL = "https://evecs.azurewebsites.net/api/"
    TEST_FUNCTION = "register_user"
    TEST_URL = LOCAL_DEV_URL + TEST_FUNCTION
    
    path = Path(__file__).parent.parent / 'local.settings.json'
    with open(path) as settings_file:
        settings = json.load(settings_file)

    MyCosmos = CosmosClient.from_connection_string(settings['Values']['DB_CONNECTION_STRING'])
    EvecsDBProxy = MyCosmos.get_database_client(settings['Values']['DB_NAME'])
    UserContainerProxy = EvecsDBProxy.get_container_client(settings['Values']['USERS_CONTAINER'])
    FunctionAppKey = settings['Values']['FUNCTION_APP_KEY']

    validPlayer = {
        "email": "bryanvullo@gmail.com",
        "password": "password123!!"
    }

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
    def tearDownClass(cls):
        response = requests.post(cls.TEST_URL, json=cls.testUser1, 
                                 headers={"x-functions-key": cls.FunctionAppKey})
        response = requests.post(cls.TEST_URL, json=cls.testUser2, 
                                 headers={"x-functions-key": cls.FunctionAppKey})
        response = requests.post(cls.TEST_URL, json=cls.testUser3, 
                                 headers={"x-functions-key": cls.FunctionAppKey})

    def tearDown(self):
        for doc in self.UserContainerProxy.read_all_items():
          self.UserContainerProxy.delete_item(item=doc, partition_key=doc['user_id'])

    def test_register_valid_player(self):
        response = requests.post(self.TEST_URL, json=self.validPlayer, 
                                 headers={"x-functions-key": self.FunctionAppKey})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["result"], f"User '{self.validPlayer.get('email')}' has been registered.")

    if __name__ == '__main__':
        unittest.main()
