import unittest
import requests
import json
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceExistsError, CosmosResourceNotFoundError
from azure.cosmos import CosmosClient

from pathlib import Path

class TestRegisterPlayer(unittest.TestCase):   
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

    def tearDown(self):
        for doc in self.UserContainerProxy.read_all_items():
          self.UserContainerProxy.delete_item(item=doc,partition_key=doc['id'])

    def test_register_valid_player(self):
        response = requests.post(self.TEST_URL, json=self.validPlayer, 
                                 headers={"x-functions-key": self.FunctionAppKey})

        self.assertEqual(response.json(), 201)
        self.assertEqual(response.json()["result"], f"User '{self.validPlayer.get('email')}' has been registered.")

    if __name__ == '__main__':
        unittest.main()
