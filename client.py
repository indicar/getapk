import os
import base64
import requests
from dotenv import load_dotenv
import json
import mimetypes
import re

# Load environment variables from .env file
load_dotenv()

# Get server URL from environment variable
SERVER_URL = os.getenv('SERVER_URL')
if not SERVER_URL:
    # Default to localhost if not set
    SERVER_URL = "http://localhost:5000"
    print(f"SERVER_URL environment variable not set, using default: {SERVER_URL}")

# Get credentials from environment variables
API_USERNAME = os.getenv('API_USERNAME')
API_PASSWORD = os.getenv('API_PASSWORD')

if not API_USERNAME or not API_PASSWORD:
    raise ValueError("API_USERNAME and API_PASSWORD must be set in .env file")

def get_filename_by_content_type(content_type):
    """Generate filename based on content type"""
    if content_type:
        # Remove any charset info from content-type
        main_type = content_type.split(';')[0].strip()
        # Get extension from mimetype
        ext = mimetypes.guess_extension(main_type)
        if ext:
            return f"file{ext}"

    # Default to 'file.bin' if content type is unknown
    return 'file.bin'


def create_auth_header():
    """Create authorization header using basic auth"""
    credentials = f"{API_USERNAME}:{API_PASSWORD}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode('utf-8')
    return f"Basic {encoded_credentials}"

def upload_file(file_path):
    """Upload a file to the server"""
    url = f"{SERVER_URL}/upload"

    headers = {
        'Authorization': create_auth_header()
    }

    try:
        with open(file_path, 'rb') as file:
            files = {'file': (os.path.basename(file_path), file, 'application/octet-stream')}
            print(f"Sending POST request to {url}")
            print(f"Authorization header: {headers['Authorization']}")
            response = requests.post(url, files=files, headers=headers)

            print(f"Server response status code: {response.status_code}")
            print(f"Server response headers: {dict(response.headers)}")

            # Log the response content
            try:
                response_json = response.json()
                print(f"Server response body (JSON): {json.dumps(response_json, indent=2)}")
            except ValueError:
                print(f"Server response body (text): {response.text}")

            if response.status_code == 200:
                print(f"File '{file_path}' uploaded successfully!")
                return True
            else:
                print(f"Failed to upload file.")
                return False

    except FileNotFoundError:
        print(f"File '{file_path}' not found.")
        return False
    except Exception as e:
        print(f"An error occurred during upload: {str(e)}")
        return False

def download_file(output_path=None):
    """Download the last uploaded file from the server"""
    url = f"{SERVER_URL}/download"

    headers = {
        'Authorization': create_auth_header()
    }

    print(f"Sending GET request to {url}")
    print(f"Authorization header: {headers['Authorization']}")

    try:
        response = requests.get(url, headers=headers)

        print(f"Server response status code: {response.status_code}")
        print(f"Server response headers: {dict(response.headers)}")

        # Log the response content
        if response.content:
            try:
                response_json = response.json()
                print(f"Server response body (JSON): {json.dumps(response_json, indent=2)}")
            except ValueError:
                # For binary content like file downloads, just show length
                print(f"Server response body (binary content, length: {len(response.content)} bytes)")

        if response.status_code == 200:
            # If no output path provided, try to get filename from Content-Disposition header
            if output_path is None:
                content_disposition = response.headers.get('Content-Disposition')
                if content_disposition:
                    # Extract filename from Content-Disposition header
                    filename_match = re.search(r'filename[^;=\n]*=(([\'"]).*?\2|[^;\n]*)', content_disposition)
                    if filename_match:
                        output_path = filename_match.group(1).strip('\'"')
                        # Always use 'file' as the base name and determine extension from Content-Type
                        content_type = response.headers.get('Content-Type')
                        if content_type:
                            # Remove any charset info from content-type
                            main_type = content_type.split(';')[0].strip()
                            # Get extension from mimetype
                            ext = mimetypes.guess_extension(main_type)
                            if ext:
                                output_path = f"file{ext}"
                            else:
                                output_path = "file.bin"
                        else:
                            output_path = "file.bin"
                    else:
                        output_path = get_filename_by_content_type(response.headers.get('Content-Type'))
                else:
                    output_path = get_filename_by_content_type(response.headers.get('Content-Type'))

            # Write the downloaded content to a file
            with open(output_path, 'wb') as file:
                file.write(response.content)
            print(f"File downloaded successfully to '{output_path}'")
            return True, output_path
        elif response.status_code == 404:
            print("No file uploaded yet on the server.")
            return False, None
        else:
            print(f"Failed to download file.")
            return False, None

    except Exception as e:
        print(f"An error occurred during download: {str(e)}")
        return False, None

def check_download_capability():
    """Check if the server supports downloading files"""
    url = f"{SERVER_URL}/download"

    headers = {
        'Authorization': create_auth_header()
    }

    print(f"Sending HEAD request to {url} to check download capability")
    print(f"Authorization header: {headers['Authorization']}")

    try:
        response = requests.head(url, headers=headers)

        print(f"Server response status code: {response.status_code}")
        print(f"Server response headers: {dict(response.headers)}")

        # Check if the server supports download functionality
        # Status codes 200 (success), 404 (no file uploaded yet), 401 (unauthorized), or 403 (forbidden) indicate the endpoint exists
        if response.status_code in [200, 404, 403, 401]:
            print(f"Server supports download functionality.")
            return True
        else:
            print(f"Server does not support download functionality or is unavailable for downloads.")
            return False

    except Exception as e:
        print(f"An error occurred during download capability check: {str(e)}")
        return False

def set_server_url(new_url):
    """Set a new server URL on the server"""
    url = f"{SERVER_URL}/set_url"

    headers = {
        'Authorization': create_auth_header()
    }

    print(f"Sending POST request to {url} to set new server URL")
    print(f"Authorization header: {headers['Authorization']}")

    try:
        response = requests.post(url, headers=headers, data={'url': new_url})

        print(f"Server response status code: {response.status_code}")
        print(f"Server response headers: {dict(response.headers)}")

        # Log the response content
        try:
            response_json = response.json()
            print(f"Server response body (JSON): {json.dumps(response_json, indent=2)}")
        except ValueError:
            print(f"Server response body (text): {response.text}")

        if response.status_code == 200:
            print(f"Server URL updated successfully to '{new_url}'.")
            return True
        else:
            print(f"Failed to update server URL.")
            return False

    except Exception as e:
        print(f"An error occurred during server URL update: {str(e)}")
        return False

def get_server_url():
    """Get the current server URL from the server"""
    url = f"{SERVER_URL}/get_url"

    headers = {
        'Authorization': create_auth_header()
    }

    print(f"Sending GET request to {url} to get current server URL")
    print(f"Authorization header: {headers['Authorization']}")

    try:
        response = requests.get(url, headers=headers)

        print(f"Server response status code: {response.status_code}")
        print(f"Server response headers: {dict(response.headers)}")

        # Log the response content
        try:
            response_json = response.json()
            print(f"Server response body (JSON): {json.dumps(response_json, indent=2)}")
        except ValueError:
            print(f"Server response body (text): {response.text}")

        if response.status_code == 200:
            current_url = response.json().get('url', 'Unknown')
            print(f"Current server URL is '{current_url}'.")
            return True, current_url
        else:
            print(f"Failed to get server URL.")
            return False, None

    except Exception as e:
        print(f"An error occurred during server URL retrieval: {str(e)}")
        return False, None

def send_request(text=None, image_path=None):
    """Send request from Android client (text and/or image)"""
    url = f"{SERVER_URL}/send_request"

    headers = {
        'Authorization': create_auth_header()
    }

    print(f"Sending POST request to {url} to send request")
    print(f"Authorization header: {headers['Authorization']}")

    try:
        # Prepare data and files
        data = {}
        files = {}

        if text:
            data['text'] = text

        if image_path:
            with open(image_path, 'rb') as img_file:
                files['image'] = (os.path.basename(image_path), img_file, 'application/octet-stream')

        # If neither text nor image is provided, raise an error
        if not text and not image_path:
            print("Error: Either text or image_path must be provided")
            return False

        response = requests.post(url, headers=headers, data=data, files=files)

        print(f"Server response status code: {response.status_code}")
        print(f"Server response headers: {dict(response.headers)}")

        # Log the response content
        try:
            response_json = response.json()
            print(f"Server response body (JSON): {json.dumps(response_json, indent=2)}")
        except ValueError:
            print(f"Server response body (text): {response.text}")

        if response.status_code == 200:
            print(f"Request sent successfully.")
            return True
        else:
            print(f"Failed to send request.")
            return False

    except Exception as e:
        print(f"An error occurred during request sending: {str(e)}")
        return False

def get_request_status():
    """Get status of the last request from Android client"""
    url = f"{SERVER_URL}/request_status"

    headers = {
        'Authorization': create_auth_header()
    }

    print(f"Sending GET request to {url} to get request status")
    print(f"Authorization header: {headers['Authorization']}")

    try:
        response = requests.get(url, headers=headers)

        print(f"Server response status code: {response.status_code}")
        print(f"Server response headers: {dict(response.headers)}")

        # Log the response content
        try:
            response_json = response.json()
            print(f"Server response body (JSON): {json.dumps(response_json, indent=2)}")
        except ValueError:
            print(f"Server response body (text): {response.text}")

        if response.status_code == 200:
            status_info = response.json()
            has_unread = status_info.get('has_unread_request', False)
            text = status_info.get('text', 'None')
            image_available = status_info.get('image_available', False)

            print(f"Has unread request: {has_unread}")
            print(f"Request text: {text}")
            print(f"Image available: {image_available}")

            return True, status_info
        else:
            print(f"Failed to get request status.")
            return False, None

    except Exception as e:
        print(f"An error occurred during request status retrieval: {str(e)}")
        return False, None

def get_last_request():
    """Get the last request from Android client and mark it as read"""
    url = f"{SERVER_URL}/get_last_request"

    headers = {
        'Authorization': create_auth_header()
    }

    print(f"Sending GET request to {url} to get last request")
    print(f"Authorization header: {headers['Authorization']}")

    try:
        response = requests.get(url, headers=headers)

        print(f"Server response status code: {response.status_code}")
        print(f"Server response headers: {dict(response.headers)}")

        # Log the response content
        try:
            response_json = response.json()
            print(f"Server response body (JSON): {json.dumps(response_json, indent=2)}")
        except ValueError:
            print(f"Server response body (text): {response.text}")

        if response.status_code == 200:
            request_data = response.json()
            text = request_data.get('text', 'None')
            image_path = request_data.get('image_path', 'None')

            print(f"Request text: {text}")
            print(f"Image path: {image_path}")

            return True, request_data
        elif response.status_code == 404:
            print("No unread request available.")
            return False, None
        else:
            print(f"Failed to get last request.")
            return False, None

    except Exception as e:
        print(f"An error occurred during last request retrieval: {str(e)}")
        return False, None


def check_health():
    """Check if the server is healthy"""
    url = f"{SERVER_URL}/health"

    print(f"Sending GET request to {url}")

    try:
        response = requests.get(url)

        print(f"Server response status code: {response.status_code}")
        print(f"Server response headers: {dict(response.headers)}")

        # Log the response content
        try:
            response_json = response.json()
            print(f"Server response body (JSON): {json.dumps(response_json, indent=2)}")
        except ValueError:
            print(f"Server response body (text): {response.text}")

        if response.status_code == 200:
            print(f"Server is healthy.")
            return True
        else:
            print(f"Server health check failed.")
            return False

    except Exception as e:
        print(f"An error occurred during health check: {str(e)}")
        return False

if __name__ == "__main__":
    print("File Upload/Download Client")
    print(f"Server URL: {SERVER_URL}")

    # Example usage:
    # Uncomment the following lines to test the functions

    # Check server health
    # check_health()

    # Check download capability
    # check_download_capability()

    # Get current server URL from server
    # get_server_url()

    # Set new server URL on server (example)
    # set_server_url("https://new-server.example.com")

    # Send a request from Android client (text only)
    # send_request(text="Hello from Android client!")

    # Send a request from Android client (text and image)
    # send_request(text="Request with image", image_path="виноградик.png")

    # Get request status
    # get_request_status()

    # Get last request (this will mark it as read)
    # get_last_request()

    # Upload the specific file
    # upload_file("виноградик.png")

    # Download the last uploaded file with automatic filename detection
    # download_file()  # Will try to detect filename from server response

    # Or download with specific filename
    # download_file("downloaded_виноградик.png")