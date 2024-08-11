import requests
import os
import urllib.parse
import json
import logging

# Set up logging
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Get Overseerr API details from environment variables
OVERSEERR_URL = os.environ.get('OVERSEERR_URL')
OVERSEERR_API_KEY = os.environ.get('OVERSEERR_API_KEY')

# Check if the URL and API key are set
if not OVERSEERR_URL:
    logger.error("I can't find the Overseerr URL. Please set the OVERSEERR_URL environment variable.")
    raise ValueError("Missing OVERSEERR_URL environment variable")

if not OVERSEERR_API_KEY:
    logger.error("I can't find the API key. Please set the OVERSEERR_API_KEY environment variable.")
    raise ValueError("Missing OVERSEERR_API_KEY environment variable")

def lambda_handler(event, context):
    # Extract parameters from the event
    intent = event['request']['intent']
    media_title = intent['slots'].get('MediaTitle', {}).get('value', '')
    request_all_seasons = intent['slots'].get('all', {}).get('value', 'false').lower() == 'true'

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
        logger.error(f"An error occurred: {e}")
        if hasattr(e, 'response'):
            logger.error(f"Server response: {e.response.text}")
        return build_response("An error occurred while processing your request. Please try again later.")

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
