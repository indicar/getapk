# Без monkey patching для Python 3.13
import os
from app import app, socketio

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, debug=False, allow_unsafe_werkzeug=True, host='0.0.0.0', port=port)
