from flask import Flask
from datetime import datetime, timedelta
import logging
from database import init_db

logger = logging.getLogger(__name__)

def create_app():
    app = Flask(__name__)
    
    # Initialize the database
    init_db()
    
    # Register blueprints
    from main import main_bp
    app.register_blueprint(main_bp)
    
    return app

app = create_app()
