from flask import Blueprint, render_template, jsonify
import logging
from summarizer import HNSummarizer, update_summaries
import threading
import time
from datetime import datetime
import sqlite3
import pytz

main_bp = Blueprint('main', __name__)
logger = logging.getLogger(__name__)

# Track if an update is currently running
is_updating = False

def run_update():
    """Function to run update in background and manage is_updating state"""
    global is_updating
    try:
        logger.info("Starting manual summary update")
        update_summaries()
        logger.info("Manual summary update completed successfully")
    except Exception as e:
        logger.error(f"Error during manual summary update: {str(e)}")
    finally:
        is_updating = False

@main_bp.route('/')
def index():
    logger.info("Serving index page")
    return render_template('index.html')

@main_bp.route('/api/summaries')
def get_summaries():
    logger.info("Fetching cached summaries")
    summarizer = HNSummarizer()
    summaries = summarizer.get_cached_summaries()
    return jsonify(summaries)

@main_bp.route('/api/update_summaries')
def trigger_update():
    global is_updating
    
    if is_updating:
        return jsonify({
            "status": "error",
            "message": "Update already in progress",
            "timestamp": time.time()
        }), 429  # Too Many Requests
    
    is_updating = True
    
    # Start update in background thread
    thread = threading.Thread(target=run_update)
    thread.daemon = True
    thread.start()
    
    return jsonify({
        "status": "success",
        "message": "Update process started",
        "timestamp": time.time()
    })

@main_bp.route('/api/update_status')
def get_update_status():
    """Optional endpoint to check if an update is in progress"""
    return jsonify({
        "is_updating": is_updating,
        "timestamp": time.time()
    })
    
@main_bp.route('/api/last_update')
def get_last_update():
    logger.info("Fetching last update time")
    summarizer = HNSummarizer()
    
    try:
        with summarizer.get_db() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT last_updated FROM last_update WHERE id = 1
            """)
            result = cursor.fetchone()
            if result:
                # Convert the SQLite timestamp to datetime with timezone
                timestamp_str = result['last_updated']
                utc_dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                utc_dt = utc_dt.replace(tzinfo=pytz.UTC)
                
                # Convert to PT
                pt_timezone = pytz.timezone('America/Los_Angeles')
                pt_dt = utc_dt.astimezone(pt_timezone)
                
                logger.debug(f"Last update time - UTC: {utc_dt}, PT: {pt_dt}")
                
                return jsonify({
                    'last_updated': pt_dt.isoformat()
                })
            return jsonify({
                'last_updated': None
            })
    except Exception as e:
        logger.error(f"Error fetching last update time: {str(e)}")
        return jsonify({
            'error': 'Could not fetch last update time'
        }), 500