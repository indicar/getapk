# Пробуем eventlet, если не работает - используем без него
try:
    import eventlet
    eventlet.monkey_patch()
    print("Using eventlet")
except Exception as e:
    print(f"Eventlet not available: {e}")

from app import app, socketio

if __name__ == '__main__':
    socketio.run(app, debug=False)
