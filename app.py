from flask import Flask, request, send_file, jsonify
import os
import base64
import shutil
import json
from functools import wraps
from flasgger import Swagger
from dotenv import load_dotenv
from flask_cors import CORS

# === ЗАГРУЗКА .env ===
print("🚀 Loading environment variables from .env...")
load_dotenv()

API_USERNAME = os.getenv('API_USERNAME')
API_PASSWORD = os.getenv('API_PASSWORD')

# print("=" * 70)
# print(f"🔍 Loaded API_USERNAME = {repr(API_USERNAME)}")
# print(f"🔍 Loaded API_PASSWORD = {repr(API_PASSWORD)}")
# print("=" * 70)

if not API_USERNAME or not API_PASSWORD:
    raise RuntimeError("❌ Set API_USERNAME and API_PASSWORD in .env!")

# === КОНФИГУРАЦИЯ ===
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# === NOTES STORAGE ===
NOTES_FILE = 'notes_data.json'

# Загружаем заметки при старте
def load_notes():
    """Загрузка заметок из файла"""
    try:
        if os.path.exists(NOTES_FILE):
            with open(NOTES_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading notes: {e}")
    return {}

def save_notes(notes):
    """Сохранение заметок в файл"""
    try:
        with open(NOTES_FILE, 'w') as f:
            json.dump(notes, f)
    except Exception as e:
        print(f"Error saving notes: {e}")

# Глобальное хранилище заметок
notes_storage = load_notes()

# Глобальные переменные: путь к последнему загруженному файлу и URL сервера
last_file_path = None
SERVER_URL = 'http://localhost:5000'

# Глобальная переменная для хранения последнего запроса от Android клиента
last_request = {
    'text': None,
    'image_path': None,
    'has_been_read': True,  # флаг, показывающий, был ли запрос уже прочитан
    'processing_status': 'received',  # статус обработки запроса ('received', 'processing', 'completed', 'failed')
    'result': None  # результат обработки запроса
}

# Глобальная переменная для хранения ID последнего запроса
last_request_id = None

app = Flask(__name__)
CORS(app, supports_credentials=True)

# === SWAGGER ===
swagger_template = {
    "swagger": "2.0",
    "info": {
        "title": "Single File Upload API",
        "description": "Upload one file (overwrites previous). Original filename preserved.",
        "version": "1.0.0"
    },
    "securityDefinitions": {
        "basicAuth": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "Enter: <code>Basic YWRtaW46c2VjcmV0</code>"
        }
    }
}

swagger_config = {
    "headers": [],
    "specs": [{
        "endpoint": 'apispec',
        "route": '/apispec.json',
        "rule_filter": lambda rule: True,
        "model_filter": lambda tag: True,
    }],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/docs/"
}

Swagger(app, template=swagger_template, config=swagger_config)

# === АВТОРИЗАЦИЯ ===
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Basic '):
            return jsonify({"error": "Unauthorized"}), 401
        try:
            encoded = auth_header[6:]
            decoded = base64.b64decode(encoded).decode('utf-8')
            username, password = decoded.split(':', 1)
        except Exception:
            return jsonify({"error": "Unauthorized"}), 401
        if username != API_USERNAME or password != API_PASSWORD:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# === ЗАГРУЗКА ===
import uuid
from datetime import datetime, timedelta

# Глобальная переменная для хранения информации о доступе к файлу
file_access_token = None
file_access_expiration = None

@app.route('/upload', methods=['POST'])
@require_auth
def upload_file():
    """
    Upload file (replaces previous, keeps original name)
    ---
    tags: [File]
    security: [{ basicAuth: [] }]
    consumes: [multipart/form-data]
    parameters:
      - name: file
        in: formData
        type: file
        required: true
    responses:
      200:
        description: OK
        schema:
          type: object
          properties:
            message:
              type: string
              description: Upload status message
            filename:
              type: string
              description: Name of uploaded file
            url:
              type: string
              description: Authenticated download URL
            public_url:
              type: string
              description: Public download URL (valid for limited time)
            expires_at:
              type: string
              format: date-time
              description: Time when public URL expires
      400:
        description: No file
    """
    global last_file_path, file_access_token, file_access_expiration

    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    # Удаляем предыдущий файл
    if last_file_path and os.path.exists(last_file_path):
        os.remove(last_file_path)

    # Сохраняем с оригинальным именем
    filename = file.filename
    last_file_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(last_file_path)

    # Создаем токен и устанавливаем время его действия (по умолчанию 1 час)
    file_access_token = str(uuid.uuid4())
    file_access_expiration = datetime.now() + timedelta(hours=1)

    # Формируем публичную ссылку для скачивания
    public_download_url = f"{request.url_root}public_download/{file_access_token}"

    return jsonify({
        "message": "File uploaded successfully",
        "filename": filename,
        "url": "/download",
        "public_url": public_download_url,
        "expires_at": file_access_expiration.isoformat()
    }), 200


# === СКАЧИВАНИЕ БЕЗ АВТОРИЗАЦИИ ===
@app.route('/public_download/<token>', methods=['GET'])
def public_download_file(token):
    """
    Public download the latest uploaded file using a token
    ---
    tags: [File]
    parameters:
      - name: token
        in: path
        type: string
        required: true
        description: Access token for downloading the file
    responses:
      200:
        description: File
      404:
        description: No file uploaded yet
      403:
        description: Invalid or expired token
    """
    global last_file_path, file_access_token, file_access_expiration

    # Проверяем, действителен ли токен и не истекло ли время его действия
    if not file_access_token or token != file_access_token or datetime.now() > file_access_expiration:
        return jsonify({"error": "Invalid or expired token"}), 403

    if not last_file_path or not os.path.exists(last_file_path):
        return jsonify({"error": "No file uploaded yet"}), 404

    return send_file(last_file_path, as_attachment=True)

# === СКАЧИВАНИЕ ===
@app.route('/download', methods=['GET'])
@require_auth
def download_file():
    """
    Download the latest uploaded file (with original name)
    ---
    tags: [File]
    security: [{ basicAuth: [] }]
    responses:
      200:
        description: File
      404:
        description: No file uploaded yet
    """
    global last_file_path

    if not last_file_path or not os.path.exists(last_file_path):
        return jsonify({"error": "No file uploaded yet"}), 404

    return send_file(last_file_path, as_attachment=True)

# === SET SERVER URL ===
@app.route('/set_url', methods=['POST'])
@require_auth
def set_server_url():
    """
    Set server URL
    ---
    tags: [Configuration]
    security: [{ basicAuth: [] }]
    parameters:
      - name: url
        in: formData
        type: string
        required: true
        description: The new server URL
    responses:
      200:
        description: URL updated successfully
      400:
        description: Missing URL parameter
    """
    global SERVER_URL

    new_url = request.form.get('url')
    if not new_url:
        return jsonify({"error": "URL parameter is required"}), 400

    # Basic validation of URL format
    if not new_url.startswith(('http://', 'https://')):
        return jsonify({"error": "Invalid URL format. Must start with http:// or https://"}), 400

    SERVER_URL = new_url
    return jsonify({"message": "Server URL updated successfully", "url": SERVER_URL}), 200

# === GET SERVER URL ===
@app.route('/get_url', methods=['GET'])
@require_auth
def get_server_url():
    """
    Get current server URL
    ---
    tags: [Configuration]
    security: [{ basicAuth: [] }]
    responses:
      200:
        description: Current server URL
    """
    global SERVER_URL

    return jsonify({"url": SERVER_URL}), 200

# === SEND REQUEST FROM ANDROID CLIENT ===
@app.route('/send_request', methods=['POST'])
@require_auth
def send_request():
    """
    Send request from Android client (text and/or image)
    ---
    tags: [Request]
    security: [{ basicAuth: [] }]
    consumes: [multipart/form-data]
    parameters:
      - name: text
        in: formData
        type: string
        required: false
        description: Request text
      - name: image
        in: formData
        type: file
        required: false
        description: Request image
    responses:
      200:
        description: Request saved successfully
      400:
        description: No text or image provided
    """
    global last_request, last_request_id

    text = request.form.get('text', '')
    image = request.files.get('image')

    # Check if at least one of text or image is provided
    if not text and not image:
        return jsonify({"error": "Text or image must be provided"}), 400

    # Save the request
    last_request['text'] = text

    # If an image is provided, save it
    if image:
        # Remove previous image if exists
        if last_request['image_path'] and os.path.exists(last_request['image_path']):
            os.remove(last_request['image_path'])

        # Generate unique filename for the image
        filename = image.filename
        if not filename:
            filename = 'android_image.jpg'  # default name

        # Save image to uploads folder
        image_path = os.path.join(UPLOAD_FOLDER, filename)
        image.save(image_path)
        last_request['image_path'] = image_path
    else:
        # Clear image path if no image provided
        if last_request['image_path'] and os.path.exists(last_request['image_path']):
            os.remove(last_request['image_path'])
        last_request['image_path'] = None

    # Mark request as unread
    last_request['has_been_read'] = False

    # Generate a new request ID and set processing status to 'received'
    import uuid
    last_request_id = str(uuid.uuid4())
    last_request['processing_status'] = 'received'
    last_request['result'] = None  # Clear any previous result

    return jsonify({"message": "Request saved successfully", "request_id": last_request_id}), 200

# === UPDATE REQUEST PROCESSING STATUS ===
@app.route('/update_request_status', methods=['POST'])
@require_auth
def update_request_status():
    """
    Update the processing status of the current request
    ---
    tags: [Request]
    security: [{ basicAuth: [] }]
    parameters:
      - name: status
        in: formData
        type: string
        required: true
        description: New processing status (received, processing, completed, failed)
      - name: result
        in: formData
        type: string
        required: false
        description: Result of the processing
    responses:
      200:
        description: Status updated successfully
      400:
        description: Invalid status provided
    """
    global last_request

    new_status = request.form.get('status')
    result = request.form.get('result')

    # Validate the status
    valid_statuses = ['received', 'processing', 'completed', 'failed']
    if new_status not in valid_statuses:
        return jsonify({"error": f"Invalid status. Valid statuses are: {', '.join(valid_statuses)}"}), 400

    # Update the status
    last_request['processing_status'] = new_status

    # If a result is provided, store it
    if result is not None:
        last_request['result'] = result

    return jsonify({"message": "Status updated successfully", "request_id": last_request_id, "new_status": new_status}), 200

# === GET REQUEST STATUS ===
@app.route('/request_status', methods=['GET'])
@require_auth
def get_request_status():
    """
    Get status of the last request from Android client
    ---
    tags: [Request]
    security: [{ basicAuth: [] }]
    responses:
      200:
        description: Request status
        schema:
          type: object
          properties:
            has_unread_request:
              type: boolean
              description: Whether there is an unread request
            text:
              type: string
              description: Request text
            image_available:
              type: boolean
              description: Whether an image is available
    """
    global last_request

    has_unread_request = not last_request['has_been_read']

    response_data = {
        "has_unread_request": has_unread_request,
        "text": last_request['text'],
        "image_available": bool(last_request['image_path'])
    }

    return jsonify(response_data), 200

# === GET LAST REQUEST ===
@app.route('/get_last_request', methods=['GET'])
@require_auth
def get_last_request():
    """
    Get the last request from Android client and mark it as read
    ---
    tags: [Request]
    security: [{ basicAuth: [] }]
    responses:
      200:
        description: Last request data
        schema:
          type: object
          properties:
            text:
              type: string
              description: Request text
            image_base64:
              type: string
              description: Base64-encoded image data
      404:
        description: No unread request available
    """
    global last_request

    # Check if there's an unread request
    if last_request['has_been_read']:
        return jsonify({"error": "No unread request available"}), 404

    # Prepare response
    response_data = {
        "text": last_request['text'],
        "image_base64": None
    }

    # If there's an image, encode it to base64
    if last_request['image_path'] and os.path.exists(last_request['image_path']):
        try:
            with open(last_request['image_path'], 'rb') as img_file:
                img_data = img_file.read()
                img_base64 = base64.b64encode(img_data).decode('utf-8')
                response_data['image_base64'] = img_base64
        except Exception as e:
            print(f"Error reading image file: {str(e)}")

    # Mark request as read
    last_request['has_been_read'] = True

    return jsonify(response_data), 200

# === POLL REQUEST PROCESSING STATUS ===
@app.route('/poll_request_status', methods=['GET'])
@require_auth
def poll_request_status():
    """
    Poll the processing status of the last request from Android client (lightweight response)
    ---
    tags: [Request]
    security: [{ basicAuth: [] }]
    responses:
      200:
        description: Request processing status (minimal data)
        schema:
          type: object
          properties:
            request_id:
              type: string
              description: ID of the request
            processing_status:
              type: string
              description: Current processing status (received, processing, completed, failed)
            has_result:
              type: boolean
              description: Whether a result is available
      204:
        description: No active request to poll
    """
    global last_request, last_request_id

    # If there's no active request, return 204 (No Content) to save bandwidth
    if last_request_id is None or last_request['processing_status'] == 'received' and last_request['has_been_read']:
        return '', 204

    # Return only essential information to minimize bandwidth
    response_data = {
        "request_id": last_request_id,
        "processing_status": last_request['processing_status'],
        "has_result": last_request['result'] is not None
    }

    return jsonify(response_data), 200

# === GET REQUEST RESULT ===
@app.route('/get_request_result', methods=['GET'])
@require_auth
def get_request_result():
    """
    Get the result of the last processed request and update status to received
    ---
    tags: [Request]
    security: [{ basicAuth: [] }]
    responses:
      200:
        description: Request result data
        schema:
          type: object
          properties:
            request_id:
              type: string
              description: ID of the request
            processing_status:
              type: string
              description: Current processing status
            result:
              type: string
              description: Result of the request processing
      404:
        description: No result available for the request
    """
    global last_request, last_request_id

    # Check if there's a result available
    if last_request['result'] is None:
        return jsonify({"error": "No result available for the request"}), 404

    # Prepare response with only essential data
    response_data = {
        "request_id": last_request_id,
        "processing_status": last_request['processing_status'],
        "result": last_request['result']
    }

    # Update status to "received" after result is retrieved
    last_request['processing_status'] = 'received'
    last_request['result'] = None  # Clear the result after retrieval

    return jsonify(response_data), 200

# === NOTES BACKUP ENDPOINTS ===

@app.route('/notes/<user_token>', methods=['POST'])
def upload_notes(user_token):
    """
    Upload encrypted notes for a user
    ---
    tags: [Notes]
    consumes: [application/json]
    parameters:
      - name: user_token
        in: path
        type: string
        required: true
        description: User token (email or unique ID)
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            encryptedData:
              type: string
              description: Encrypted notes data (base64)
            iv:
              type: string
              description: Initialization vector (base64)
            version:
              type: integer
              description: Backup version
    responses:
      200:
        description: Notes uploaded successfully
      400:
        description: Invalid data
    """
    global notes_storage
    
    try:
        data = request.get_json()
        
        if not data or 'encryptedData' not in data or 'iv' not in data:
            return jsonify({'error': 'Missing encryptedData or iv'}), 400
        
        # Сохраняем зашифрованные данные
        notes_storage[user_token] = {
            'encryptedData': data['encryptedData'],
            'iv': data['iv'],
            'version': data.get('version', 1)
        }
        
        save_notes(notes_storage)
        
        return jsonify({
            'success': True,
            'message': 'Notes uploaded successfully'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/notes/<user_token>', methods=['GET'])
def download_notes(user_token):
    """
    Download encrypted notes for a user
    ---
    tags: [Notes]
    parameters:
      - name: user_token
        in: path
        type: string
        required: true
        description: User token (email or unique ID)
    responses:
      200:
        description: Encrypted notes data
      404:
        description: No notes found for this user
    """
    global notes_storage
    
    if user_token not in notes_storage:
        return jsonify({'error': 'No notes found for this user'}), 404
    
    return jsonify(notes_storage[user_token])


@app.route('/notes/<user_token>', methods=['DELETE'])
def delete_notes(user_token):
    """
    Delete notes for a user
    ---
    tags: [Notes]
    parameters:
      - name: user_token
        in: path
        type: string
        required: true
        description: User token (email or unique ID)
    responses:
      200:
        description: Notes deleted successfully
      404:
        description: No notes found
    """
    global notes_storage
    
    if user_token in notes_storage:
        del notes_storage[user_token]
        save_notes(notes_storage)
        return jsonify({'success': True, 'message': 'Notes deleted'})
    else:
        return jsonify({'error': 'No notes found'}), 404


@app.route('/notes/<user_token>/status', methods=['GET'])
def check_notes_status(user_token):
    """
    Check if notes exist for a user
    ---
    tags: [Notes]
    parameters:
      - name: user_token
        in: path
        type: string
        required: true
        description: User token (email or unique ID)
    responses:
      200:
        description: Status information
    """
    global notes_storage
    
    if user_token in notes_storage:
        return jsonify({
            'exists': True,
            'has_notes': True
        })
    else:
        return jsonify({
            'exists': False,
            'has_notes': False
        })


# === HEALTH CHECK ===
@app.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint
    ---
    tags: [Health]
    responses:
      200:
        description: Server is alive
    """
    return jsonify({
        "status": "healthy", 
        "message": "Server is running",
        "notes_users": len(notes_storage)
    }), 200

# === ЗАПУСК ===
if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    # print(f"✅ Server starting on http://0.0.0.0:{port}")
    print(f"📄 Swagger UI: http://0.0.0.0:{port}/docs/")
    app.run(host='0.0.0.0', port=port, debug=False)