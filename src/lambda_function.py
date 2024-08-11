import requests
import os
import urllib.parse
import json
import logging
import boto3
from botocore.exceptions import ClientError

# Set up logging
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Initialize DynamoDB client
dynamodb = boto3.client('dynamodb', region_name=os.environ.get('DYNAMODB_PERSISTENCE_REGION'))

# Function to fetch configuration from DynamoDB
def fetch_config_from_dynamodb():
    table_name = os.environ.get('DYNAMODB_PERSISTENCE_TABLE_NAME')
    if not table_name:
        raise ValueError("DYNAMODB_PERSISTENCE_TABLE_NAME environment variable is not set")

    # Set the key for the DynamoDB query
    key = {'id': {'S': 'amzn1.ask.account.AMAQOAHY55IP7CPFL4DXCPGBE3AHFF4ZGV7RULCMEE2ON2QUYDMSJIKOUEKN6DDIRFD5S46WSHAQCKZMWSCROSQEFWUZHEZBVJVSU7TTEHGHXTE6KL3VFCZYFFNOTQI6ZOWBWCWANBO2JMWLKZ2VG3WA25MM6JW2263BWJVQ5Z3H7ZV4BYFTIOIPESELENYSGRPQNEMHV7TWQVXMARYJJVJ4TH5FVW7RA2GMFH6TD27A'}}
    logger.debug(f"Fetching config from DynamoDB table '{table_name}' with key: {key}")

    try:
        response = dynamodb.get_item(TableName=table_name, Key=key)
        item = response.get('Item', {})
        logger.debug(f"DynamoDB response: {item}")
        return {
            'OVERSEERR_URL': item.get('OVERSEERR_URL', {}).get('S'),
            'OVERSEERR_API_KEY': item.get('OVERSEERR_API_KEY', {}).get('S')
        }
    except ClientError as e:
        logger.error(f"Failed to fetch config from DynamoDB: {e}")
        return {}

# Fetch configuration
config = fetch_config_from_dynamodb()

# Get Overseerr API details, prioritizing environment variables
OVERSEERR_URL = os.environ.get('OVERSEERR_URL', config.get('OVERSEERR_URL'))
OVERSEERR_API_KEY = os.environ.get('OVERSEERR_API_KEY', config.get('OVERSEERR_API_KEY'))

# Check if the URL and API key are set
if not OVERSEERR_URL:
    logger.error("I can't find the Overseerr URL. Please set the OVERSEERR_URL environment variable or configure it in DynamoDB.")
    raise ValueError("Missing OVERSEERR_URL configuration")

if not OVERSEERR_API_KEY:
    logger.error("I can't find the API key. Please set the OVERSEERR_API_KEY environment variable or configure it in DynamoDB.")
    raise ValueError("Missing OVERSEERR_API_KEY configuration")

def lambda_handler(event, context):
    # Log the userId for debugging purposes
    user_id = event['context']['System']['user']['userId']
    logger.info(f"Request received from userId: {user_id}")

    # Ensure the request is an IntentRequest
    if event['request']['type'] != 'IntentRequest':
        return build_response("I'm sorry, I can only handle intent requests.")

    # Extract parameters from the intent
    intent = event['request']['intent']
    slots = intent.get('slots', {})
    media_title = slots.get('MediaTitle', {}).get('value', '')
    request_all_seasons = slots.get('all', {}).get('value', 'false').lower() == 'true'

    if not media_title:
        return build_response("Please provide the title of the movie or TV show.")

    encoded_query = urllib.parse.quote(media_title)

    # Set up headers for API requests
    headers = {
        'X-Api-Key': OVERSEERR_API_KEY,
        'Content-Type': 'application/json'
    }

    try:
        # Step 1: Search for the media
        search_url = f"{OVERSEERR_URL}/api/v1/search"
        search_params = {
            'query': encoded_query,
            'page': 1,
            'language': 'en'
        }
        
        logger.debug(f"Searching for '{media_title}' at {search_url}")
        search_response = requests.get(search_url, params=search_params, headers=headers)
        search_response.raise_for_status()
        search_data = search_response.json()

        # Select the first matching item
        if not search_data['results']:
            return build_response(f"I'm sorry, but I couldn't find any media matching '{media_title}'.")

        selected_item = search_data['results'][0]
        media_type = selected_item['mediaType']
        title_key = 'name' if media_type == 'tv' else 'title'
        logger.debug(f"Selected item: {selected_item[title_key]} (Type: {media_type})")
        
        # Step 2: Get detailed information about the media
        detail_url = f"{OVERSEERR_URL}/api/v1/{media_type}/{selected_item['id']}"
        logger.debug(f"Fetching details from {detail_url}")
        detail_response = requests.get(detail_url, headers=headers)
        detail_response.raise_for_status()
        detail_data = detail_response.json()

        # Step 3: Prepare the request data for Overseerr
        request_url = f"{OVERSEERR_URL}/api/v1/request"
        request_data = {
            'mediaType': media_type,
            'mediaId': detail_data['id'],
            'imdbId': detail_data.get('externalIds', {}).get('imdbId'),
        }

        # Add tvdbId only if it exists and is a number
        tvdbId = detail_data.get('externalIds', {}).get('tvdbId')
        if tvdbId and isinstance(tvdbId, (int, float)):
            request_data['tvdbId'] = int(tvdbId)

        # Handle seasons for TV shows
        if media_type == 'tv':
            seasons = detail_data.get('seasons', [])
            if seasons:
                if request_all_seasons:
                    # Request all seasons except season 0 (usually specials)
                    request_data['seasons'] = [season['seasonNumber'] for season in seasons if season['seasonNumber'] != 0]
                else:
                    # Request only the latest season
                    latest_season = max(season['seasonNumber'] for season in seasons if season['seasonNumber'] != 0)
                    request_data['seasons'] = [latest_season]
            else:
                # If no season information is available, request all seasons
                request_data['seasons'] = 'all'
        
        logger.debug(f"Sending request to {request_url}")
        logger.debug(f"Request data: {json.dumps(request_data, indent=2)}")

        # Send the request to Overseerr
        request_response = requests.post(request_url, json=request_data, headers=headers)
        request_response.raise_for_status()
        
        logger.debug("Request was successful")
        
        # Use the correct key for the title based on the media type
        title = detail_data.get(title_key)

        if request_response.status_code == 201:
            if media_type == 'tv':
                if request_all_seasons:
                    return build_response(f"I have successfully added all seasons of '{title}' to your requests.")
                else:
                    return build_response(f"I have successfully added the latest season of '{title}' to your requests.")
            else:
                return build_response(f"I have successfully added '{title}' to your requests.")
        else:
            logger.error("Failed to add request. Please check the details and try again.")
            return build_response("I couldn't add your request. Please check the details and try again.")

    except requests.exceptions.RequestException as e:
        error_message = f"An error occurred: {e}"
        logger.error(error_message)
        if hasattr(e, 'response'):
            logger.error(f"Server response: {e.response.text}")
            error_message += f" Server response: {e.response.text}"
        return build_response(error_message)

def build_response(output):
    return {
        'version': '1.0',
        'response': {
            'outputSpeech': {
                'type': 'PlainText',
                'text': output
            },
            'shouldEndSession': True
        }
    }