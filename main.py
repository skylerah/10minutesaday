from flask import Blueprint, render_template, jsonify
import logging
from summarizer import HNSummarizer, update_summaries
import threading
import time

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
