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
        self.MAX_COMMENTS_PER_STORY = 200
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
        """Fetch comments while maintaining thread structure."""
        story = self.fetch_item(story_id)
        if not story or 'kids' not in story:
            logger.debug(f"No comments found for story {story_id}")
            return []

        def fetch_comment_tree(comment_id: int, depth: int = 0) -> Dict[str, Any]:
            """Recursively fetch a comment and its replies."""
            comment = self.fetch_item(comment_id)
            if not comment or comment.get('deleted') or comment.get('dead'):
                return None
            
            # Add depth info to track level in thread
            comment['depth'] = depth
            
            # Recursively fetch child comments
            if 'kids' in comment:
                comment['replies'] = []
                for child_id in comment['kids']:
                    child_comment = fetch_comment_tree(child_id, depth + 1)
                    if child_comment:
                        comment['replies'].append(child_comment)
            
            return comment

        # Process top-level comments and their threads
        threaded_comments = []
        top_level_comments = story['kids'][:self.MAX_COMMENTS_PER_STORY]
        
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            comment_futures = [
                executor.submit(fetch_comment_tree, cid, 0) 
                for cid in top_level_comments
            ]
            threaded_comments = [
                f.result() for f in comment_futures 
                if f.result() and not f.result().get('deleted')
            ]

        # Flatten the thread structure for GPT while maintaining depth info
        flattened_comments = []
        
        def flatten_thread(comment: Dict[str, Any]):
            """Flatten the thread structure while preserving depth information."""
            if not comment:
                return
                
            # Create flattened comment with depth info
            flat_comment = {
                'id': comment['id'],
                'text': comment.get('text', ''),
                'depth': comment['depth'],
                'parent': comment.get('parent'),
                'time': comment.get('time')
            }
            flattened_comments.append(flat_comment)
            
            # Process replies
            if 'replies' in comment:
                for reply in comment['replies']:
                    flatten_thread(reply)

        # Flatten all comment threads
        for comment in threaded_comments:
            flatten_thread(comment)

        logger.info(f"Successfully fetched {len(flattened_comments)} comments in thread structure for story {story_id}")
        return flattened_comments

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
        """Two-step process: first summarize, then find supporting citations."""
        story_id = story['id']
        story_url = story.get('url', '')
        logger.info(f"Summarizing comments for story {story_id}: {story.get('title', 'No title')}")
        
        try:
            # Get total comments count right at the start
            total_comments = len(comments)
            if 'descendants' in story:
                total_comments = story['descendants']

            # Format comments with thread structure for GPT
            comment_texts = []
            for i, comment in enumerate(comments, 1):
                if comment and 'text' in comment:
                    indent = "  " * comment.get('depth', 0)  # Visual indentation for thread depth
                    comment_texts.append(f"[{i}] {indent}{html.unescape(comment.get('text', ''))}")
                    
                    
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

            # Step 1: Generate high-level summary without citations
            summary_prompt = f"""
            
            First, understand the article being discussed:
            {article_context}

            Now, analyze this threaded discussion where indented comments are replies to comments above them.
            First, read through all comments to understand the broad themes and key points of this discussion thread.
            Pay attention to how discussions evolve within comment threads - notice when sub-discussions branch off
            and how points are debated or expanded in the replies. Then, analyze the discussion thread about this article, understanding both the article's content and the comments.
            
            Provide:

            1. A 1-2 sentence summary of the article itself.

            2. A controversy rating based on the overall discussion:
               - 0: Complete consensus, minimal disagreement
               - 5: Healthy debate with different viewpoints
               - 10: Intense disagreement, strong opposing views

            3. The 3-5 most significant themes or points from the OVERALL discussion. Consider both top-level points
            and important sub-discussions that developed in the replies. Each point should be between 1-2 sentences in length.

            Title: {story.get('title')}
            Number of comments analyzed: {len(comments)} out of {total_comments} total comments

            EXACTLY FOLLOW THIS RESPONSE FORMAT:
            ARTICLE SUMMARY:
            [1-2 sentence summary of the article]
            
            CONTROVERSY: [rating]

            KEY POINTS:
            - [broad point from overall discussion]
            - [broad point from overall discussion]
            - [broad point from overall discussion]
            
            DO NOT include the word "CONTROVERSY" in the discussion points.
            DO NOT use topic headers or categories in the points.
            DO NOT start sentences/points with words like "The discussion...", or "The conversation..."
            
            IMPORTANT:
            - Consider starting points without the use of particles like "The, This, That, etc", and instead opt for more direct statements.

            Comments (indentation shows reply structure):
            {chr(10).join(comment_texts)}
            """

            logger.debug(f"Generating initial summary for story {story_id}")
            initial_response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system", 
                        "content": """You are an expert at analyzing discussions and identifying key themes.
                        Focus on understanding and summarizing the main points of the conversation.
                        Do not reference individual comments - focus on the overall discussion."""
                    },
                    {
                        "role": "user", 
                        "content": summary_prompt
                    }
                ],
                max_tokens=1000,
                temperature=0.7
            )
            
            initial_summary = initial_response.choices[0].message.content

            # Step 2: Find supporting citations for each point
            citation_prompt = f"""
            Below is a summary of key points from a discussion. For each point, identify 2-3 comments that best support 
            or demonstrate that point. Use the comment numbers provided to cite relevant comments.

            Summary:
            {initial_summary}

            Comments:
            {chr(10).join(comment_texts)}

            For each key point in the summary, add supporting citations in [N] format from the comments above.
            If a point has multiple relevant comments, combine their citations (e.g., [1][4][7]).

            EXACTLY FOLLOW THIS RESPONSE FORMAT:
            
            ARTICLE SUMMARY:
            [1-2 sentence summary of the article]
            
            CONTROVERSY: [same rating as above]

            KEY POINTS:
            - [exact point from summary] [citations]
            - [exact point from summary] [citations]
            - [exact point from summary] [citations]
            
            DO NOT include the word "CONTROVERSY" in the discussion points.
            DO NOT use topic headers or categories in the points.

            IMPORTANT:
            - Keep the original points exactly as written
            - Add only the most relevant citations that directly support each point
            - Use the [N] format for citations
            - Citations should be adjacent with no spaces between them (e.g., [1][3][5])
            """

            logger.debug(f"Finding citations for story {story_id}")
            citation_response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system", 
                        "content": """You are an expert at identifying supporting evidence.
                        Your task is to find the most relevant comments that support each key point,
                        without changing the points themselves."""
                    },
                    {
                        "role": "user", 
                        "content": citation_prompt
                    }
                ],
                max_tokens=1000,
                temperature=0.7
            )
            
            final_summary = citation_response.choices[0].message.content
            processed_summary = self.process_gpt_response(final_summary, story_id, comments)
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
                'commentCount': len(comments),  # Fallback to simple count in case of error
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
