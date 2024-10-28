from flask import Blueprint, render_template, jsonify
import logging
from .summarizer import HNSummarizer

main_bp = Blueprint('main', __name__)
logger = logging.getLogger(__name__)

@main_bp.route('/')
def index():
    logger.info("Serving index page")
    return render_template('index.html')

@main_bp.route('/api/summaries')
def get_summaries():
    logger.info("Fetching cached summaries")
    summarizer = HNSummarizer()
    summaries = summarizer.get_cached_summaries()
    
    if not summaries:
        logger.warning("No cached summaries found")
        return jsonify({"error": "No summaries available"}), 404
        
    return jsonify(summaries)
