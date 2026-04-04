from flask import Flask, request, send_file, jsonify
import os
import base64
import shutil
import json
import time
import random
from functools import wraps
from flasgger import Swagger
from dotenv import load_dotenv
from flask_cors import CORS

# === WEBSOCKET SUPPORT ===
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect

# SocketIO будет инициализирован после создания app

# Хранилище WebSocket соединений: {userId: sid}
ws_connections = {}

# Очередь офлайн сообщений для пользователей
offline_messages = {}  # {userId: [messages...]}
# Отслеживание уже полученных сообщений для предотвращения дубликатов
received_messages = {}  # {userId: set(message_id)}

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

# SocketIO с поддержкой long-polling и WebSocket
# Используем threading для совместимости
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ping_timeout=60, ping_interval=25)

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
        "notes_users": len(notes_storage),
        "signaling_users": len(signaling_storage),
        "signals_count": sum(len(v) for v in signaling_storage.values())
    }), 200

# === SIGNALING (замена Firebase) ===
signaling_storage = {}  # {userId: [signals]}
online_users = {}  # {userId: nickname}

@app.route('/api/signaling/register/<user_id>', methods=['POST'])
def register_user(user_id):
    """Register user for signaling"""
    nickname = request.args.get('nickname', user_id)
    if user_id not in signaling_storage:
        signaling_storage[user_id] = []
    online_users[user_id] = nickname
    return jsonify({"success": True}), 200

@app.route('/api/signaling/online/<user_id>', methods=['POST'])
def set_online(user_id):
    """Set user online status"""
    online = request.args.get('online', 'true').lower() == 'true'
    if online:
        online_users[user_id] = request.args.get('nickname', user_id)
    else:
        online_users.pop(user_id, None)
    return jsonify({"success": True}), 200

@app.route('/api/signaling/<user_id>', methods=['POST'])
def send_signal(user_id):
    """Send signal to user"""
    data = request.get_json()
    if user_id not in signaling_storage:
        signaling_storage[user_id] = []
    signaling_storage[user_id].append(data)
    # Keep only last 100 signals per user
    if len(signaling_storage[user_id]) > 100:
        signaling_storage[user_id] = signaling_storage[user_id][-100:]
    return '', 200

@app.route('/api/signaling/<user_id>', methods=['GET'])
def get_signals(user_id):
    """Get signals for user"""
    since = request.args.get('since', 0, type=int)
    signals = signaling_storage.get(user_id, [])
    # Filter signals since timestamp
    new_signals = [s for s in signals if s.get('timestamp', 0) > since]
    return jsonify(new_signals), 200

@app.route('/api/users/online', methods=['GET'])
def get_online_users():
    """Get all online users"""
    return jsonify(online_users), 200

# === FILE TRANSFER (через сервер) ===
file_transfers = {}  # {file_id: {"from": user, "to": user, "filename": str, "data": bytes}}

@app.route('/api/files/upload', methods=['POST'])
def upload_file_transfer():
    """Upload file for transfer to another user"""
    import uuid
    if 'file' not in request.files:
        return jsonify({"error": "No file"}), 400
    
    file = request.files['file']
    to_user = request.form.get('to')
    from_user = request.form.get('from')
    
    if not to_user or not from_user:
        return jsonify({"error": "Missing from/to"}), 400
    
    file_id = str(uuid.uuid4())
    file_data = file.read()
    file_transfers[file_id] = {
        "from": from_user,
        "to": to_user,
        "filename": file.filename,
        "data": file_data,
        "size": len(file_data)
    }
    
    return jsonify({"file_id": file_id, "filename": file.filename, "size": len(file_data)}), 200

@app.route('/api/files/<file_id>', methods=['GET'])
def download_file_transfer(file_id):
    """Download transferred file"""
    if file_id not in file_transfers:
        return jsonify({"error": "File not found"}), 404
    
    transfer = file_transfers[file_id]
    from io import BytesIO
    return send_file(
        BytesIO(transfer["data"]),
        as_attachment=True,
        download_name=transfer["filename"]
    )

@app.route('/api/files/pending/<user_id>', methods=['GET'])
def get_pending_files(user_id):
    """Get list of pending files for user"""
    pending = []
    for fid, transfer in file_transfers.items():
        if transfer["to"] == user_id:
            pending.append({
                "file_id": fid,
                "filename": transfer["filename"],
                "size": transfer["size"],
                "from": transfer["from"]
            })
    return jsonify(pending), 200

# === TURN CREDENTIALS ===
@app.route('/turn_credentials', methods=['GET'])
def get_turn_credentials():
    """
    Get TURN/STUN server credentials for WebRTC
    ---
    tags: [WebRTC]
    responses:
      200:
        description: TURN/STUN credentials
        schema:
          type: object
          properties:
            iceServers:
              type: array
              items:
                type: object
    """
    # STUN сервера (бесплатные Google)
    # TURN сервера - используй свои или metered.ca
    ice_servers = [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
        {"urls": "stun:stun2.l.google.com:19302"},
        # TURN сервера (замени на свои)
        {
            "urls": ["turn:appp.metered.ca:80?transport=tcp", "turn:appp.metered.ca:443?transport=tcp"],
            "username": "e87750020052a6fdd244ef0d",
            "credential": "9SNLVc6Ji/ti7aJg"
        }
    ]
    return jsonify({"iceServers": ice_servers}), 200

# === APK UPLOAD (с авторизацией) ===
@app.route('/upload_apk', methods=['POST'])
@require_auth
def upload_apk():
    """
    Upload new APK file (replaces previous)
    ---
    tags: [APK]
    security: [{ basicAuth: [] }]
    consumes: [multipart/form-data]
    parameters:
      - name: apk
        in: formData
        type: file
        required: true
        description: APK file to upload
    responses:
      200:
        description: APK uploaded successfully
        schema:
          type: object
          properties:
            message:
              type: string
            filename:
              type: string
            download_url:
              type: string
      400:
        description: No APK file provided
    """
    if 'apk' not in request.files:
        return jsonify({"error": "No APK file part"}), 400
    
    apk_file = request.files['apk']
    if apk_file.filename == '' or not apk_file.filename.endswith('.apk'):
        return jsonify({"error": "File must be an APK (.apk)"}), 400
    
    # Удаляем предыдущий APK
    apk_path = os.path.join(UPLOAD_FOLDER, 'latest.apk')
    if os.path.exists(apk_path):
        os.remove(apk_path)
    
    # Сохраняем новый APK
    apk_file.save(apk_path)
    
    # Публичная ссылка для скачивания
    download_url = f"{request.url_root}download_apk"
    
    return jsonify({
        "message": "APK uploaded successfully",
        "filename": apk_file.filename,
        "download_url": download_url
    }), 200

# === APK DOWNLOAD (без авторизации - публичный) ===
@app.route('/download_apk', methods=['GET'])
def download_apk():
    """
    Download the latest APK (public, no auth required)
    ---
    tags: [APK]
    responses:
      200:
        description: APK file
      404:
        description: No APK uploaded yet
    """
    apk_path = os.path.join(UPLOAD_FOLDER, 'latest.apk')
    if not os.path.exists(apk_path):
        return jsonify({"error": "No APK uploaded yet. Upload one first via /upload_apk"}), 404
    
    return send_file(apk_path, 
                     as_attachment=True, 
                     download_name='messenger-p2p.apk',
                     mimetype='application/vnd.android.package-archive')

# === WEBSOCKET EVENT HANDLERS ===

@socketio.on('connect')
def handle_connect():
    """Обработка подключения клиента"""
    print(f"🔌 Client connected: {request.sid}")
    emit('connected', {'status': 'ok'})

@socketio.on('disconnect')
def handle_disconnect():
    """Обработка отключения клиента"""
    # Удаляем из хранилища соединений
    user_id_to_remove = None
    for uid, sid in ws_connections.items():
        if sid == request.sid:
            user_id_to_remove = uid
            break
    if user_id_to_remove:
        del ws_connections[user_id_to_remove]
        online_users.pop(user_id_to_remove, None)  # Также удаляем из online_users
        print(f"🔌 Client disconnected: {user_id_to_remove}")

@socketio.on('register')
def handle_register(data):
    """Регистрация пользователя в WebSocket"""
    user_id = data.get('userId')
    nickname = data.get('nickname', user_id)
    
    if user_id:
        ws_connections[user_id] = request.sid
        online_users[user_id] = nickname  # Сохраняем никнейм
        join_room(user_id)
        
        # Отправляем офлайн сообщения (с проверкой на дубликаты)
        if user_id in offline_messages:
            for msg in offline_messages[user_id]:
                msg_id = msg.get('msgId')
                # Проверяем, не было ли уже получено это сообщение
                user_received = received_messages.setdefault(user_id, set())
                if msg_id and msg_id not in user_received:
                    emit('signal', msg, room=request.sid)
                    user_received.add(msg_id)
                    print(f"📬 Sent offline message: {msg.get('type')} to {user_id}")
            # Очищаем очередь после отправки
            offline_messages[user_id] = []
        
        print(f"📱 User registered via WS: {user_id} (nickname: {nickname}, sid: {request.sid})")
        emit('registered', {'userId': user_id, 'status': 'ok'})

@socketio.on('signal')
def handle_signal(data):
    """Пересылка сигналов между пользователями"""
    to_user = data.get('to')
    signal_type = data.get('type')
    signal_data = data.get('data')
    from_user = data.get('from')
    
    # Дополнительные данные для WebRTC
    sdp_mid = data.get('sdpMid')
    sdp_mline_index = data.get('sdpMLineIndex')
    
    # Генерируем уникальный ID сообщения
    msg_id = f"{from_user}_{to_user}_{signal_type}_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
    
    signal_message = {
        'type': signal_type,
        'from': from_user,
        'to': to_user,
        'data': signal_data,
        'sdpMid': sdp_mid,
        'sdpMLineIndex': sdp_mline_index,
        'timestamp': int(time.time() * 1000),
        'msgId': msg_id  # Уникальный ID для дедупликации
    }
    
    # Если пользователь онлайн через WebSocket - отправляем сразу (с проверкой на дубликаты)
    if to_user in ws_connections:
        target_sid = ws_connections[to_user]
        print(f"📡 User {to_user} is online (sid: {target_sid}), sending {signal_type}")
        # Проверяем, не было ли уже получено это сообщение
        user_received = received_messages.setdefault(to_user, set())
        if msg_id not in user_received:
            emit('signal', signal_message, room=target_sid)
            user_received.add(msg_id)
            print(f"📡 Signal sent: {signal_type} -> {to_user}, msgId: {msg_id}")
        else:
            print(f"⚠️ Duplicate signal skipped: {signal_type} -> {to_user}, msgId: {msg_id}")
    else:
        # Сохраняем для офлайн пользователей (макс 100)
        if to_user not in offline_messages:
            offline_messages[to_user] = []
        offline_messages[to_user].append(signal_message)
        if len(offline_messages[to_user]) > 100:
            offline_messages[to_user] = offline_messages[to_user][-100:]
        print(f"📡 Signal queued for offline user: {signal_type} -> {to_user}")

@socketio.on('get_online_users')
def handle_get_online_users(data):
    """Получить список онлайн пользователей"""
    online_list = []
    for uid, sid in ws_connections.items():
        nickname = online_users.get(uid, uid)  # Используем никнейм или UID как fallback
        online_list.append({'userId': uid, 'nickname': nickname})
    emit('online_users', online_list)

@socketio.on('set_status')
def handle_set_status(data):
    """Установить статус (онлайн/офлайн)"""
    user_id = data.get('userId')
    online = data.get('online', True)
    
    if user_id:
        if online:
            ws_connections[user_id] = request.sid
            join_room(user_id)
        else:
            if user_id in ws_connections:
                del ws_connections[user_id]
            leave_room(user_id)

# === API ENDPOINT: Получить конфиг режима ===
@app.route('/api/config/connection', methods=['GET'])
def get_connection_config():
    """
    Get connection configuration (WebSocket vs Polling)
    ---
    tags: [Configuration]
    responses:
      200:
        description: Connection config
    """
    use_websocket = os.getenv('USE_WEBSOCKET', 'true').lower() == 'true'
    ws_url = os.getenv('WS_URL', os.getenv('RENDER_EXTERNAL_URL', 'http://localhost:5000'))
    
    return jsonify({
        "useWebsocket": use_websocket,
        "websocketUrl": f"https://{ws_url.replace('http://', '').replace('https://', '')}" if 'https' not in ws_url else ws_url,
        "pollingUrl": f"{request.url_root}",
        "pollingInterval": 5000
    }), 200

# === API ENDPOINT: Получить список онлайн пользователей ===
@app.route('/api/ws/online', methods=['GET'])
def get_ws_online_users():
    """Получить список онлайн пользователей через WebSocket"""
    return jsonify([
        {"userId": uid, "online": True} for uid in ws_connections.keys()
    ]), 200

# === ГОЛОСОВЫЕ ЗВОНКИ ЧЕРЕЗ WEBSOCKET ===
# Хранилище активных звонков: {call_id: {from, to, status, start_time}}
active_calls = {}

@socketio.on('call_request')
def handle_call_request(data):
    """Инициация звонка"""
    from_user = data.get('from')
    to_user = data.get('to')
    call_id = data.get('callId', f"{from_user}_{to_user}_{int(time.time())}")
    
    print(f"📞 Call request: {from_user} -> {to_user} (callId: {call_id})")
    print(f"   Online users: {list(ws_connections.keys())}")
    print(f"   To user in connections: {to_user in ws_connections}")
    
    if to_user not in ws_connections:
        print(f"   ❌ User {to_user} is NOT online")
        emit('call_error', {'error': 'User is offline', 'callId': call_id}, room=request.sid)
        return
    
    active_calls[call_id] = {
        'from': from_user,
        'to': to_user,
        'status': 'ringing',
        'start_time': int(time.time() * 1000)
    }
    
    # Отправляем входящий звонок получателю
    target_sid = ws_connections[to_user]
    print(f"   📤 Sending incoming_call to {to_user} (sid: {target_sid})")
    emit('incoming_call', {
        'callId': call_id,
        'from': from_user,
        'to': to_user
    }, room=target_sid)
    
    print(f"   ✅ Call request sent successfully")

@socketio.on('call_answer')
def handle_call_answer(data):
    """Ответ на звонок (принять/отклонить)"""
    call_id = data.get('callId')
    accepted = data.get('accepted', False)
    user_id = data.get('userId')
    
    if call_id not in active_calls:
        emit('call_error', {'error': 'Call not found', 'callId': call_id}, room=request.sid)
        return
    
    call = active_calls[call_id]
    # Определяем получателя (того, кто не отвечал)
    to_sid = None
    if call['from'] == user_id:
        # Звонящий получает ответ
        if call['to'] in ws_connections:
            to_sid = ws_connections[call['to']]
    else:
        # Принимающий отвечает
        if call['from'] in ws_connections:
            to_sid = ws_connections[call['from']]
    
    if to_sid:
        if accepted:
            call['status'] = 'active'
            emit('call_accepted', {'callId': call_id, 'accepted': True}, room=to_sid)
            emit('call_accepted', {'callId': call_id, 'accepted': True}, room=request.sid)
            print(f"✅ Call accepted: {call_id}")
        else:
            call['status'] = 'rejected'
            emit('call_rejected', {'callId': call_id, 'accepted': False}, room=to_sid)
            print(f"❌ Call rejected: {call_id}")
            # Удаляем звонок
            del active_calls[call_id]
    else:
        emit('call_error', {'error': 'User disconnected', 'callId': call_id}, room=request.sid)

@socketio.on('call_end')
def handle_call_end(data):
    """Завершение звонка"""
    call_id = data.get('callId')
    user_id = data.get('userId')
    
    if call_id in active_calls:
        call = active_calls[call_id]
        # Уведомляем другого участника
        other_user = call['to'] if call['from'] == user_id else call['from']
        if other_user in ws_connections:
            emit('call_ended', {'callId': call_id, 'endedBy': user_id}, room=ws_connections[other_user])
        
        print(f"📴 Call ended: {call_id} by {user_id}")
        del active_calls[call_id]

@socketio.on('audio_data')
def handle_audio_data(data):
    """Передача аудио-данных через сервер"""
    call_id = data.get('callId')
    audio_base64 = data.get('audio')  # base64-encoded PCM/opus
    from_user = data.get('from')
    
    print(f"🎵 Audio received from {from_user}, callId: {call_id}, size: {len(audio_base64) if audio_base64 else 0}")
    
    if call_id not in active_calls:
        print(f"   ❌ Call not found: {call_id}")
        return
    
    call = active_calls[call_id]
    print(f"   Call status: {call.get('status')}")
    
    if call['status'] != 'active':
        print(f"   ❌ Call not active yet")
        return
    
    # Определяем получателя
    to_user = call['to'] if call['from'] == from_user else call['from']
    print(f"   Forwarding to: {to_user}")
    
    if to_user in ws_connections:
        # Ретранслируем аудио другому абоненту
        emit('audio_data', {
            'callId': call_id,
            'audio': audio_base64,
            'from': from_user,
            'timestamp': int(time.time() * 1000)
        }, room=ws_connections[to_user])
        print(f"   ✅ Audio forwarded to {to_user}")
    else:
        print(f"   ❌ Recipient {to_user} not online")

@socketio.on('ice_candidate')
def handle_ice_candidate(data):
    """Обмен ICE-кандидатами для звонка"""
    call_id = data.get('callId')
    candidate = data.get('candidate')
    from_user = data.get('from')
    
    if call_id not in active_calls:
        return
    
    call = active_calls[call_id]
    to_user = call['to'] if call['from'] == from_user else call['from']
    
    if to_user in ws_connections:
        emit('ice_candidate', {
            'callId': call_id,
            'candidate': candidate,
            'from': from_user
        }, room=ws_connections[to_user])

# === ЗАПУСК ===
if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    print(f"📄 Swagger UI: http://0.0.0.0:{port}/docs/")
    print(f"🔌 WebSocket enabled: {os.getenv('USE_WEBSOCKET', 'true').lower() == 'true'}")
    
    # Используем eventlet для WebSocket
    try:
        import eventlet
        eventlet.monkey_patch()
    except ImportError:
        pass
    
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)