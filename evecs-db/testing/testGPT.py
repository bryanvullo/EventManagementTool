import unittest
import requests
import json
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceExistsError, CosmosResourceNotFoundError
from azure.cosmos import CosmosClient

from pathlib import Path

class TestGPT(unittest.TestCase):   
    LOCAL_DEV_URL = "http://localhost:7071/api/"
    PUBLIC_URL = "https://evecs.azurewebsites.net/api/"
    TEST_FUNCTION = "create_event_gpt"
    TEST_URL = LOCAL_DEV_URL + TEST_FUNCTION
    
    path = Path(__file__).parent.parent / 'local.settings.json'
    with open(path) as settings_file:
        settings = json.load(settings_file)

    MyCosmos = CosmosClient.from_connection_string(settings['Values']['DB_CONNECTION_STRING'])
    EvecsDBProxy = MyCosmos.get_database_client(settings['Values']['DB_NAME'])
    UserContainerProxy = EvecsDBProxy.get_container_client(settings['Values']['USERS_CONTAINER'])
    FunctionAppKey = settings['Values']['FUNCTION_APP_KEY']

    data = {
        "text": """I am the ECSS society president (bv1g22). 
        Create an society event for the ECSS society with name 'Welcome Evening'. 
        The event will be hosted in location Centenary Building, in room 3013.
        The event should start on the 1st of October 2021 at 6pm and end at 9pm.
        This is a limited event so max tickets will be of only 200. 
        Please generate a random image url for the event. 
        Also generate an event ID and a short description for the event."""
    }

    def test_register_valid_player(self):
        response = requests.post(self.TEST_URL, json=self.data, 
                                 headers={"x-functions-key": self.FunctionAppKey})
        
        print(response.text)

        self.assertEqual(response.status_code, 200)

        outputPath = Path(__file__).parent / 'GPT-output.json'
        with open(outputPath, 'w') as f:
            f.write(response.json()['result'])

    if __name__ == '__main__':
        unittest.main()
