from flask import Flask, request, send_file, jsonify
import os
import base64
import shutil
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

# Глобальная переменная: путь к последнему загруженному файлу
last_file_path = None

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
      400:
        description: No file
    """
    global last_file_path

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

    return jsonify({
        "message": "File uploaded successfully",
        "filename": filename,
        "url": "/download"
    }), 200

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
    return jsonify({"status": "healthy", "message": "Server is running"}), 200

# === ЗАПУСК ===
if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    # print(f"✅ Server starting on http://0.0.0.0:{port}")
    print(f"📄 Swagger UI: http://0.0.0.0:{port}/docs/")
    app.run(host='0.0.0.0', port=port, debug=False)