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
from article_analyzer import ArticleAnalyzer

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class HNSummarizer:
    def __init__(self):
        logger.info("Initializing HNSummarizer")
        self.client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        self.article_analyzer = ArticleAnalyzer()
        self.HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
        self.MAX_STORIES = 30
        self.MAX_COMMENTS_PER_STORY = 300
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
            # Handle both [N] format and (N1, N2, N3) format
            citation_numbers = re.findall(r'\d+', match.group(0))
            citation_links = []
            for num in citation_numbers:
                if num in comment_map:
                    comment_id = comment_map[num]
                    citation_links.append(
                        f'<a href="https://news.ycombinator.com/item?id={comment_id}" '
                        f'class="text-blue-600 hover:text-blue-800" target="_blank">[{num}]</a>'
                    )
            return ''.join(citation_links)

        # First, convert any parenthetical citations to bracket format
        summary = re.sub(r'\((\d+(?:,\s*\d+)*)\)', lambda m: '[' + ']['.join(m.group(1).replace(' ', '').split(',')) + ']', summary)
        
        # Then process all citations in bracket format
        processed_summary = re.sub(r'\[(\d+)\]', replace_citation, summary)
        return processed_summary

    def summarize_comments(self, story: Dict[str, Any], comments: List[Dict[str, Any]]) -> Dict[str, Any]:
        story_id = story['id']
        story_url = story.get('url', '')
        
        # Get total comments count
        total_comments = len(comments)
        if 'descendants' in story:
            total_comments = story['descendants']
        
        # First, try to get article content
        article_context = ""
        if story_url:
            logger.info(f"Fetching article content from {story_url}")
            article_content = self.article_analyzer.extract_article_content(story_url)
            if article_content:
                article_context = self.article_analyzer.get_summary_context(article_content)
                logger.info("Successfully extracted article content")
            else:
                logger.warning("Could not extract article content")
        
        # Prepare comments
        comment_texts = []
        for i, comment in enumerate(comments, 1):
            if comment and 'text' in comment:
                comment_texts.append(f"[{i}] {html.unescape(comment.get('text', ''))}")

        prompt = f"""
        First, understand the article being discussed:
        {article_context}

        Now, analyze the discussion thread about this article, understanding both the article's content and the comments.
        Provide:

        1. A 1-2 sentence summary of the article itself.

        2. A controversy rating based on the overall discussion:
           - 0: Complete consensus, minimal disagreement
           - 5: Healthy debate with different viewpoints
           - 10: Intense disagreement, strong opposing views

        3. The 3-5 most significant themes or points from the OVERALL discussion, considering both the article content
        and the community's response. After identifying each key point, find supporting evidence in the comments.

        Title: {story.get('title')}
        Number of comments analyzed: {len(comments)} out of {total_comments} total comments

        RESPONSE FORMAT:
        ARTICLE SUMMARY:
        [1-2 sentence summary of the article content]

        CONTROVERSY: [rating]

        KEY POINTS:
        - [plain text point without any formatting] [1][2][3]
        - [plain text point without any formatting] [4][5][6]
        - [plain text point without any formatting] [7][8][9]
        - [etc]...

        IMPORTANT: 
        - Keep the article summary very concise (1-2 sentences maximum)
        - Do not use any markdown formatting (no asterisks, no bold text)
        - Write points in plain text only
        - Consider both the article's content and the community's response
        - First understand the overall discussion, then find citations to support each point
        - Focus on points that represent significant themes in the discussion
        - Citations must be in the format [N] with square brackets
        - Multiple citations should be adjacent: [1][2][3]
        - Keep each point concise and clear
        """

        try:
            logger.debug(f"Sending request to OpenAI API for story {story_id}")
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system", 
                        "content": """You are an expert at analyzing technical discussions and articles. 
                        Your task is to:
                        1. First understand the article being discussed
                        2. Then analyze how the community responded to and discussed the article
                        3. Identify the most significant points that represent the broader conversation
                        4. Find specific comments that best support or illustrate each point
                        
                        Be extremely concise and focus on the most important points.
                        Never use markdown formatting - provide all text in plain format.
                        Never reference individual users - instead, state points that reflect broader themes.
                        Always format citations as [N] with square brackets, adjacent to each other like [1][2][3]."""
                    },
                    {
                        "role": "user", 
                        "content": prompt
                    }
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

    def save_summary(self, summary_data: Dict[str, Any], position: int):
        """Save a summary to the database."""
        with self.get_db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO summaries 
                (story_id, title, url, points, comment_count, summary, position, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                summary_data['story_id'],
                summary_data['title'],
                summary_data['url'],
                summary_data['points'],
                summary_data['commentCount'],
                summary_data['summary'],
                position
            ))

    def get_cached_summaries(self) -> List[Dict[str, Any]]:
        """Get cached summaries from the database."""
        with self.get_db() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM summaries 
                WHERE created_at > datetime('now', '-24 hours')
                ORDER BY position ASC
            """)
            return [dict(row) for row in cursor.fetchall()]

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

def update_summaries():
    """Function to update summaries. Called by scheduler or API endpoint."""
    logger.info("Starting summary update")
    summarizer = HNSummarizer()

    try:
        # Create a temporary table for the new summaries
        with summarizer.get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS summaries_temp (
                    story_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT,
                    points INTEGER,
                    comment_count INTEGER,
                    summary TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
            """)
        
        stories = summarizer.fetch_top_stories()
        total_stories = len(stories)
        logger.info(f"Fetched {total_stories} stories to process")
        
        processed_stories = 0
        # Save new summaries to temporary table
        with ThreadPoolExecutor(max_workers=summarizer.MAX_WORKERS) as executor:
            for position, story_id in enumerate(stories):
                try:
                    story = summarizer.fetch_item(story_id)
                    if story:
                        comments = summarizer.fetch_comments(story_id)
                        summary = summarizer.summarize_comments(story, comments)
                        
                        # Save to temp table instead of main table
                        with summarizer.get_db() as conn:
                            conn.execute("""
                                INSERT OR REPLACE INTO summaries_temp 
                                (story_id, title, url, points, comment_count, summary, position, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                            """, (
                                summary['story_id'],
                                summary['title'],
                                summary['url'],
                                summary['points'],
                                summary['commentCount'],
                                summary['summary'],
                                position
                            ))
                        
                        processed_stories += 1
                        logger.info(f"Processed story {processed_stories}/{total_stories}")
                        time.sleep(1)  # Rate limiting
                except Exception as e:
                    logger.error(f"Error processing story {story_id}: {str(e)}")
                    continue
        
        # If we processed at least some stories successfully, swap the tables
        if processed_stories > 0:
            with summarizer.get_db() as conn:
                conn.execute("BEGIN TRANSACTION")
                try:
                    # Rename existing table to _old
                    conn.execute("ALTER TABLE summaries RENAME TO summaries_old")
                    # Rename new table to main name
                    conn.execute("ALTER TABLE summaries_temp RENAME TO summaries")
                    # Drop old table
                    conn.execute("DROP TABLE summaries_old")
                    # Update last_update timestamp
                    conn.execute("""
                        UPDATE last_update 
                        SET last_updated = datetime('now')
                        WHERE id = 1
                    """)
                    conn.execute("COMMIT")
                    logger.info(f"Successfully swapped in {processed_stories} new summaries")
                except Exception as e:
                    conn.execute("ROLLBACK")
                    logger.error(f"Error swapping tables: {str(e)}")
                    raise
        else:
            # If no stories were processed, clean up temp table
            with summarizer.get_db() as conn:
                conn.execute("DROP TABLE IF EXISTS summaries_temp")
            logger.error("No stories were successfully processed")
            
        logger.info(f"Successfully completed summary update. Processed {processed_stories} stories.")
        
    except Exception as e:
        logger.error(f"Error during summary update: {str(e)}")
        # Clean up temp table if it exists
        try:
            with summarizer.get_db() as conn:
                conn.execute("DROP TABLE IF EXISTS summaries_temp")
        except:
            pass
        raise