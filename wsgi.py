# eventlet для Python 3.13
import eventlet
eventlet.monkey_patch()

import os
from app import app, socketio

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, debug=False, host='0.0.0.0', port=port)
