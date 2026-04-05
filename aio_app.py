"""
aiohttp сервер с WebSocket (работает с Python 3.13)
"""
import asyncio
import json
import os
import uuid
from datetime import datetime
from collections import defaultdict

from aiohttp import web
import websockets

# Хранилище
ws_connections = {}  # {user_id: websocket}
online_users = {}   # {user_id: nickname}
offline_messages = {}  # {user_id: [messages...]}
received_messages = {}  # {user_id: set(msg_id)}
active_calls = {}  # {call_id: {from, to, status}}

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

# Flask совместимость - базовые endpoints
def create_app():
    app = web.Application()
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/health', lambda r: web.Response(text='ok'))
    
    # API endpoints для конфигурации
    async def get_connection_config(request):
        import os
        ws_url = os.getenv('WS_URL', os.getenv('RENDER_EXTERNAL_URL', 'https://getapk.onrender.com'))
        # Убедимся что URL начинается с wss:// для WebSocket
        if not ws_url.startswith('wss://') and not ws_url.startswith('ws://'):
            ws_url = 'wss://' + ws_url.replace('https://', '').replace('http://', '')
        
        return web.json_response({
            "useWebsocket": True,
            "websocketUrl": ws_url,
            "pollingUrl": str(request.url.origin()),
            "pollingInterval": 5000
        })
    
    async def get_turn_credentials(request):
        # STUN сервера (бесплатные Google)
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
    
    app.router.add_get('/api/config/connection', get_connection_config)
    app.router.add_get('/turn_credentials', get_turn_credentials)
    
    return app

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app = create_app()
    web.run_app(app, host='0.0.0.0', port=port)
