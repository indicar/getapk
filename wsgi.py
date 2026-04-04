# Без monkey patching для Python 3.13
from app import app, socketio

if __name__ == '__main__':
    socketio.run(app, debug=False, allow_unsafe_werkzeug=True)
