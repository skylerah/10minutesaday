from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import pytz
from database import init_db
from summarizer import update_summaries
import logging

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()

def create_app():
    app = Flask(__name__)
    
    # Initialize the database
    init_db()
    
    # Register blueprints
    from main import main_bp
    app.register_blueprint(main_bp)
    
    # Perform initial update
    logger.info("Performing initial summary update on startup")
    try:
        update_summaries()
        logger.info("Initial summary update completed successfully")
    except Exception as e:
        logger.error(f"Error during initial summary update: {str(e)}")
    
    # Schedule the next update for 24 hours from now
    next_run = datetime.now(pytz.UTC) + timedelta(days=1)
    next_run = next_run.replace(hour=6, minute=0, second=0, microsecond=0)
    
    # Schedule recurring updates
    scheduler.add_job(
        update_summaries,
        'cron',
        hour=6,
        minute=0,
        timezone=pytz.UTC,
        id='update_summaries'
    )
    
    logger.info(f"Scheduled next update for: {next_run}")
    
    # Start the scheduler
    scheduler.start()
    
    return app

# Create the app instance
app = create_app()

