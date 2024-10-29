import sqlite3
from datetime import datetime, timedelta
import os
from typing import List, Dict, Any
import logging
from concurrent.futures import ThreadPoolExecutor
import time
from openai import OpenAI
import requests
import html
import re

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

def update_summaries():
    """Function to update summaries. Called both on startup and by scheduler."""
    logger.info("Starting summary update")
    summarizer = HNSummarizer()
    
    try:
        # Clear old summaries before updating
        summarizer.clear_old_summaries()
        
        stories = summarizer.fetch_top_stories()
        total_stories = len(stories)
        logger.info(f"Fetched {total_stories} stories to process")
        
        processed_stories = 0
        with ThreadPoolExecutor(max_workers=summarizer.MAX_WORKERS) as executor:
            for story_id in stories:
                try:
                    story = summarizer.fetch_item(story_id)
                    if story:
                        comments = summarizer.fetch_comments(story_id)
                        summary = summarizer.summarize_comments(story, comments)
                        summarizer.save_summary(summary)
                        processed_stories += 1
                        logger.info(f"Processed story {processed_stories}/{total_stories}")
                        time.sleep(1)  # Rate limiting
                except Exception as e:
                    logger.error(f"Error processing story {story_id}: {str(e)}")
                    continue
        
        logger.info(f"Successfully completed summary update. Processed {processed_stories} stories.")
        
    except Exception as e:
        logger.error(f"Error during summary update: {str(e)}")
        raise

class HNSummarizer:
    def __init__(self):
        logger.info("Initializing HNSummarizer")
        self.client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        self.HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
        self.MAX_STORIES = 30
        self.MAX_COMMENTS_PER_STORY = 30
        self.MAX_WORKERS = 5
        self.db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'summaries.db')

    def get_db(self):
        """Get database connection."""
        return sqlite3.connect(self.db_path)

    def fetch_item(self, item_id: int) -> Dict[str, Any]:
        """Fetch a single item from HackerNews API."""
        logger.debug(f"Fetching item {item_id} from HN API")
        try:
            response = requests.get(f"{self.HN_API_BASE}/item/{item_id}.json")
            if response.status_code == 200:
                data = response.json()
                logger.debug(f"Successfully fetched item {item_id}: {data.get('type', 'unknown type')}")
                return data
            else:
                logger.warning(f"Failed to fetch item {item_id}: Status code {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching item {item_id}: {str(e)}")
            return None

    def fetch_top_stories(self) -> List[int]:
        """Fetch top story IDs from HackerNews."""
        logger.info("Fetching top stories")
        try:
            response = requests.get(f"{self.HN_API_BASE}/topstories.json")
            if response.status_code == 200:
                stories = response.json()[:self.MAX_STORIES]
                logger.info(f"Successfully fetched {len(stories)} top stories")
                return stories
            logger.warning(f"Failed to fetch top stories: Status code {response.status_code}")
            return []
        except Exception as e:
            logger.error(f"Error fetching top stories: {str(e)}")
            return []

    def fetch_comments(self, story_id: int) -> List[Dict[str, Any]]:
        """Recursively fetch comments for a story."""
        logger.info(f"Fetching comments for story {story_id}")
        story = self.fetch_item(story_id)
        if not story or 'kids' not in story:
            logger.debug(f"No comments found for story {story_id}")
            return []

        comments = []
        comment_ids = story['kids'][:self.MAX_COMMENTS_PER_STORY]
        logger.debug(f"Fetching {len(comment_ids)} comments for story {story_id}")
        
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            comment_futures = [executor.submit(self.fetch_item, cid) for cid in comment_ids]
            comments = [f.result() for f in comment_futures if f.result()]

        logger.info(f"Successfully fetched {len(comments)} comments for story {story_id}")
        return comments

    def process_gpt_response(self, summary: str, story_id: int, comments: List[Dict[str, Any]]) -> str:
        """Process GPT response to convert citation markers into HTML links."""
        # Create a mapping of comment indices to actual comment IDs
        comment_map = {str(i + 1): comment['id'] for i, comment in enumerate(comments)}
        
        def replace_citation(match):
            citation_numbers = re.findall(r'\d+', match.group(0))
            citation_links = []
            for num in citation_numbers:
                if num in comment_map:
                    comment_id = comment_map[num]
                    citation_links.append(
                        f'<a href="https://news.ycombinator.com/item?id={comment_id}" '
                        f'class="text-blue-600 hover:text-blue-800" target="_blank">[{num}]</a>'
                    )
            return ' '.join(citation_links)

        # Replace citation markers with HTML links
        processed_summary = re.sub(r'\[(\d+(?:,\s*\d+)*)\]', replace_citation, summary)
        return processed_summary

    def summarize_comments(self, story: Dict[str, Any], comments: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Use GPT-4 to summarize comments and categorize discussions with citations."""
        story_id = story['id']
        logger.info(f"Summarizing comments for story {story_id}: {story.get('title', 'No title')}")
        
        # Count total comments for the story (including nested comments)
        total_comments = len(comments)
        if 'descendants' in story:
            total_comments = story['descendants']
        
        # Prepare the context for GPT-4
        comment_texts = []
        for i, comment in enumerate(comments, 1):
            if comment and 'text' in comment:
                comment_texts.append(f"[{i}] {html.unescape(comment.get('text', ''))}")
        
        logger.debug(f"Processing {len(comment_texts)} comments for summarization")
        
        prompt = f"""
        Title: {story.get('title')}
        URL: {story.get('url', 'No URL')}
        Number of comments: {len(comments)}

        Please analyze these comments from a Hacker News discussion and:
        1. Identify 3-5 main discussion themes/categories
        2. Provide a brief summary of the key points within each category
        3. Note any significant consensus or disagreements

        IMPORTANT: When referencing specific comments or points made by commenters, cite them using their numbers in square brackets.
        For example: "Several users argued for this approach [1,2,3]" or "One commenter suggested [4]"

        Comments:
        {chr(10).join(comment_texts[:15])}

        Remember to cite specific comments using their numbers in square brackets when summarizing points or opinions.
        """

        try:
            logger.debug(f"Sending request to OpenAI API for story {story_id}")
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an expert at analyzing and summarizing technical discussions. Always cite specific comments using [N] notation when referencing them."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1000,
                temperature=0.7
            )
            
            summary = response.choices[0].message.content
            processed_summary = self.process_gpt_response(summary, story_id, comments)
            logger.info(f"Successfully generated summary with citations for story {story_id}")
            
            return {
                'story_id': story['id'],
                'title': story['title'],
                'url': story.get('url', ''),
                'points': story.get('score', 0),
                'commentCount': total_comments,
                'summary': processed_summary
            }
        except Exception as e:
            logger.error(f"Error generating summary for story {story_id}: {str(e)}")
            return {
                'story_id': story['id'],
                'title': story['title'],
                'url': story.get('url', ''),
                'points': story.get('score', 0),
                'commentCount': total_comments,
                'summary': f"Error generating summary: {str(e)}"
            }

    def save_summary(self, summary_data: Dict[str, Any]):
        """Save a summary to the database."""
        with self.get_db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO summaries 
                (story_id, title, url, points, comment_count, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                summary_data['story_id'],
                summary_data['title'],
                summary_data['url'],
                summary_data['points'],
                summary_data['commentCount'],
                summary_data['summary']
            ))

    def get_cached_summaries(self) -> List[Dict[str, Any]]:
        """Get cached summaries from the database."""
        with self.get_db() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM summaries 
                WHERE created_at > datetime('now', '-24 hours')
                ORDER BY points DESC
            """)
            return [dict(row) for row in cursor.fetchall()]

    def needs_update(self) -> bool:
        """Check if we need to update the summaries."""
        with self.get_db() as conn:
            cursor = conn.execute("""
                SELECT last_updated FROM last_update WHERE id = 1
            """)
            result = cursor.fetchone()
            if not result:
                return True
            last_update = datetime.fromisoformat(result[0])
            return datetime.utcnow() - last_update > timedelta(hours=23)

    def clear_old_summaries(self):
        """Clear out old summaries before updating."""
        logger.info("Clearing old summaries")
        with self.get_db() as conn:
            conn.execute("DELETE FROM summaries")
            conn.execute("""
                UPDATE last_update 
                SET last_updated = datetime('now')
                WHERE id = 1
            """)
        logger.info("Old summaries cleared")

    def update_last_updated(self):
        """Update the last_updated timestamp."""
        with self.get_db() as conn:
            conn.execute("""
                UPDATE last_update SET last_updated = datetime('now') WHERE id = 1
            """)
