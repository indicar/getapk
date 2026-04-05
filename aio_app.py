"""
aiohttp сервер с WebSocket (работает с Python 3.13)
"""
import asyncio
import json
import os
import uuid
import base64
import shutil
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
from aiohttp import web
import aiofiles

# Конфигурация
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Хранилище
ws_connections = {}  # {user_id: websocket}
online_users = {}   # {user_id: nickname}
offline_messages = {}  # {user_id: [messages...]}
received_messages = {}  # {user_id: set(msg_id)}
active_calls = {}  # {call_id: {from, to, status}}

# APK
last_apk_path = os.path.join(UPLOAD_FOLDER, 'latest.apk')
file_access_token = None
file_access_expiration = None
last_file_path = None

# Auth
API_USERNAME = os.getenv('API_USERNAME', 'admin')
API_PASSWORD = os.getenv('API_PASSWORD', 'secret')

def check_auth(request):
    """Проверка авторизации"""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Basic '):
        return False
    try:
        encoded = auth_header[6:]
        decoded = base64.b64decode(encoded).decode('utf-8')
        username, password = decoded.split(':', 1)
        return username == API_USERNAME and password == API_PASSWORD
    except:
        return False

async def websocket_handler(request):
    """Обработчик WebSocket"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    client_id = None
    
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')
                    
                    # Регистрация
                    if msg_type == 'register':
                        user_id = data.get('userId')
                        nickname = data.get('nickname', user_id)
                        ws_connections[user_id] = ws
                        online_users[user_id] = nickname
                        client_id = user_id
                        
                        print(f"📱 User registered: {user_id}")
                        
                        # Офлайн сообщения
                        if user_id in offline_messages:
                            for msg in offline_messages[user_id]:
                                msg_id = msg.get('msgId')
                                user_received = received_messages.setdefault(user_id, set())
                                if msg_id and msg_id not in user_received:
                                    await ws.send_json(msg)
                                    user_received.add(msg_id)
                            offline_messages[user_id] = []
                        
                        await ws.send_json({'type': 'registered', 'userId': user_id})
                    
                    # Сигнал
                    elif msg_type == 'signal':
                        to_user = data.get('to')
                        from_user = data.get('from')
                        signal_type = data.get('signalType') or data.get('type')
                        
                        msg_id = f"{from_user}_{to_user}_{signal_type}_{int(datetime.now().timestamp() * 1000)}_{uuid.uuid4().int % 10000}"
                        data['msgId'] = msg_id
                        
                        print(f"📡 Signal: {signal_type} -> {to_user}")
                        
                        if to_user in ws_connections:
                            target_ws = ws_connections[to_user]
                            user_received = received_messages.setdefault(to_user, set())
                            if msg_id not in user_received:
                                await target_ws.send_json(data)
                                user_received.add(msg_id)
                        else:
                            if to_user not in offline_messages:
                                offline_messages[to_user] = []
                            offline_messages[to_user].append(data)
                            if len(offline_messages[to_user]) > 100:
                                offline_messages[to_user] = offline_messages[to_user][-100:]
                    
                    # Онлайн пользователи
                    elif msg_type == 'get_online_users':
                        users = [{'userId': uid, 'nickname': nick} for uid, nick in online_users.items()]
                        await ws.send_json({'type': 'online_users', 'users': users})
                    
                    # Звонки
                    elif msg_type == 'call_request':
                        call_id = data.get('callId') or f"{data.get('from')}_{data.get('to')}_{int(datetime.now().timestamp() * 1000)}"
                        to_user = data.get('to')
                        from_user = data.get('from')
                        
                        active_calls[call_id] = {'from': from_user, 'to': to_user, 'status': 'ringing'}
                        
                        if to_user in ws_connections:
                            await ws_connections[to_user].send_json({
                                'type': 'incoming_call',
                                'callId': call_id,
                                'from': from_user,
                                'to': to_user
                            })
                    
                    elif msg_type == 'call_answer':
                        call_id = data.get('callId')
                        accepted = data.get('accepted', False)
                        user_id = data.get('userId')
                        
                        if call_id in active_calls:
                            call = active_calls[call_id]
                            other_user = call['to'] if call['from'] == user_id else call['from']
                            
                            if other_user in ws_connections:
                                await ws_connections[other_user].send_json({
                                    'type': 'call_accepted' if accepted else 'call_rejected',
                                    'callId': call_id,
                                    'accepted': accepted
                                })
                                if accepted:
                                    call['status'] = 'active'
                                else:
                                    del active_calls[call_id]
                    
                    elif msg_type == 'call_end':
                        call_id = data.get('callId')
                        user_id = data.get('userId')
                        
                        if call_id in active_calls:
                            call = active_calls[call_id]
                            other_user = call['to'] if call['from'] == user_id else call['from']
                            
                            if other_user in ws_connections:
                                await ws_connections[other_user].send_json({
                                    'type': 'call_ended',
                                    'callId': call_id,
                                    'endedBy': user_id
                                })
                            del active_calls[call_id]
                    
                    elif msg_type == 'audio_data':
                        call_id = data.get('callId')
                        from_user = data.get('from')
                        
                        if call_id in active_calls:
                            call = active_calls[call_id]
                            to_user = call['to'] if call['from'] == from_user else call['from']
                            
                            if to_user in ws_connections:
                                await ws_connections[to_user].send_json(data)
                    
                    elif msg_type == 'ice_candidate':
                        call_id = data.get('callId')
                        from_user = data.get('from')
                        
                        if call_id in active_calls:
                            call = active_calls[call_id]
                            to_user = call['to'] if call['from'] == from_user else call['from']
                            
                            if to_user in ws_connections:
                                await ws_connections[to_user].send_json(data)
                
                except json.JSONDecodeError:
                    pass
    
    except Exception as e:
        print(f"❌ Error: {e}")
    
    finally:
        if client_id and client_id in ws_connections:
            del ws_connections[client_id]
            if client_id in online_users:
                del online_users[client_id]
            print(f"🔌 Disconnected: {client_id}")
    
    return ws

# === APK UPLOAD ===
async def upload_apk(request):
    global last_apk_path, file_access_token, file_access_expiration
    
    if not check_auth(request):
        return web.json_response({'error': 'Unauthorized'}, status=401)
    
    reader = await request.multipart()
    field = await reader.next()
    if field is None:
        return web.json_response({'error': 'No APK file part'}, status=400)
    
    filename = field.filename
    if not filename or not filename.endswith('.apk'):
        return web.json_response({'error': 'File must be an APK (.apk)'}, status=400)
    
    # Удаляем предыдущий APK
    if os.path.exists(last_apk_path):
        os.remove(last_apk_path)
    
    # Сохраняем новый APK
    path = os.path.join(UPLOAD_FOLDER, 'latest.apk')
    async with aiofiles.open(path, 'wb') as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            await f.write(chunk)
    
    download_url = f"{request.url.origin}/download_apk"
    
    return web.json_response({
        'message': 'APK uploaded successfully',
        'filename': filename,
        'download_url': download_url
    })

# === APK DOWNLOAD (публичный) ===
async def download_apk(request):
    global last_apk_path
    
    if not os.path.exists(last_apk_path):
        return web.json_response({'error': 'No APK uploaded yet. Upload one first via /upload_apk'}, status=404)
    
    return web.FileResponse(last_apk_path, headers={'Content-Disposition': 'attachment; filename="messenger-p2p.apk"'})


# === FILE UPLOAD (с авторизацией) ===
async def upload_file_with_auth(request):
    global last_file_path, file_access_token, file_access_expiration
    
    if not check_auth(request):
        return web.json_response({'error': 'Unauthorized'}, status=401)
    
    reader = await request.multipart()
    field = await reader.next()
    if field is None:
        return web.json_response({'error': 'No file part'}, status=400)
    
    filename = field.filename or 'file'
    if filename == '':
        return web.json_response({'error': 'No selected file'}, status=400)
    
    # Удаляем предыдущий файл
    if last_file_path and os.path.exists(last_file_path):
        os.remove(last_file_path)
    
    # Сохраняем с оригинальным именем
    last_file_path = os.path.join(UPLOAD_FOLDER, filename)
    async with aiofiles.open(last_file_path, 'wb') as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            await f.write(chunk)
    
    # Создаем токен
    file_access_token = str(uuid.uuid4())
    file_access_expiration = datetime.now() + timedelta(hours=1)
    
    public_download_url = f"{request.url.origin}/public_download/{file_access_token}"
    
    return web.json_response({
        'message': 'File uploaded successfully',
        'filename': filename,
        'url': '/download',
        'public_url': public_download_url,
        'expires_at': file_access_expiration.isoformat()
    })

# === FILE UPLOAD (без авторизации для /api/files/upload) ===
async def upload_file(request):
    reader = await request.multipart()
    
    file_field = None
    to_user = None
    from_user = None
    
    async for field in reader:
        if field.name == 'file':
            file_field = field
        elif field.name == 'to':
            to_user = await field.text()
        elif field.name == 'from':
            from_user = await field.text()
    
    if not file_field or not to_user or not from_user:
        return web.json_response({'error': 'Missing from/to/file'}, status=400)
    
    filename = file_field.filename or 'file'
    file_id = str(uuid.uuid4())
    
    # Читаем файл в память
    file_data = await file_field.read()
    
    # Для простоты - не сохраняем на диск, отправляем уведомление
    return web.json_response({
        'file_id': file_id,
        'filename': filename,
        'size': len(file_data)
    })


# === API endpoints ===
async def get_connection_config(request):
    ws_url = os.getenv('WS_URL', os.getenv('RENDER_EXTERNAL_URL', 'https://getapk.onrender.com'))
    if not ws_url.startswith('wss://') and not ws_url.startswith('ws://'):
        ws_url = 'wss://' + ws_url.replace('https://', '').replace('http://', '')
    
    return web.json_response({
        "useWebsocket": True,
        "websocketUrl": ws_url,
        "pollingUrl": str(request.url.origin()),
        "pollingInterval": 5000
    })

async def get_turn_credentials(request):
    ice_servers = [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
        {"urls": "stun:stun2.l.google.com:19302"},
        {
            "urls": ["turn:appp.metered.ca:80?transport=tcp", "turn:appp.metered.ca:443?transport=tcp"],
            "username": "e87750020052a6fdd244ef0d",
            "credential": "9SNLVc6Ji/ti7aJg"
        }
    ]
    return web.json_response({"iceServers": ice_servers})

async def health_check(request):
    return web.json_response({
        "status": "healthy",
        "message": "Server is running",
        "signaling_users": len(ws_connections)
    })


def create_app():
    app = web.Application()
    
    # WebSocket
    app.router.add_get('/ws', websocket_handler)
    
    # Health
    app.router.add_get('/health', health_check)
    
    # API
    app.router.add_get('/api/config/connection', get_connection_config)
    app.router.add_get('/turn_credentials', get_turn_credentials)
    
    # APK
    app.router.add_post('/upload', upload_file_with_auth)  # С авторизацией
    app.router.add_post('/upload_apk', upload_apk)
    app.router.add_get('/download_apk', download_apk)
    app.router.add_get('/download', download_apk)
    
    # Swagger UI
    async def docs(request):
        html = """<!DOCTYPE html>
<html>
<head>
    <title>API Documentation</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        h1 { color: #333; }
        .endpoint { background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 5px; }
        .method { font-weight: bold; padding: 3px 8px; border-radius: 3px; }
        .get { background: #61affe; color: white; }
        .post { background: #49cc90; color: white; }
    </style>
</head>
<body>
    <h1>📄 API Documentation</h1>
    <div class="endpoint">
        <span class="method get">GET</span> <code>/health</code> - Health check
    </div>
    <div class="endpoint">
        <span class="method get">GET</span> <code>/api/config/connection</code> - Connection config
    </div>
    <div class="endpoint">
        <span class="method get">GET</span> <code>/turn_credentials</code> - TURN/STUN credentials
    </div>
    <div class="endpoint">
        <span class="method post">POST</span> <code>/upload</code> - Upload file (Basic Auth)
    </div>
    <div class="endpoint">
        <span class="method post">POST</span> <code>/upload_apk</code> - Upload APK (Basic Auth)
    </div>
    <div class="endpoint">
        <span class="method get">GET</span> <code>/download</code> - Download latest APK
    </div>
    <div class="endpoint">
        <span class="method get">GET</span> <code>/ws</code> - WebSocket endpoint
    </div>
    <div class="endpoint">
        <span class="method post">POST</span> <code>/api/logs</code> - Send logs
    </div>
    <div class="endpoint">
        <span class="method get">GET</span> <code>/api/logs?key=admin123</code> - Get logs
    </div>
</body>
</html>"""
        return web.Response(text=html, content_type='text/html')
    
    app.router.add_get('/docs/', docs)
    app.router.add_get('/docs', docs)
    
    # Files
    app.router.add_post('/api/files/upload', upload_file)
    
    # Public download
    async def public_download(request, token):
        global last_file_path, file_access_token, file_access_expiration
        
        if not file_access_token or token != file_access_token or datetime.now() > file_access_expiration:
            return web.json_response({'error': 'Invalid or expired token'}, status=403)
        
        if not last_file_path or not os.path.exists(last_file_path):
            return web.json_response({'error': 'No file uploaded yet'}, status=404)
        
        return web.FileResponse(last_file_path, as_attachment=True)
    
    app.router.add_get('/public_download/{token}', public_download)
    
    return app

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app = create_app()
    web.run_app(app, host='0.0.0.0', port=port)