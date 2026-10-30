import os
from dotenv import load_dotenv
load_dotenv()

from extensions import create_app, db

app = create_app()

if __name__ == '__main__':
    # Local development only. In production use a WSGI server (gunicorn / PythonAnywhere) with FLASK_DEBUG=False.
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', 5000)))
