import requests
from bs4 import BeautifulSoup
import logging
from typing import Optional, Dict
from urllib.parse import urlparse
import io
import PyPDF2
import base64
from github import Github
import os
import tempfile

logger = logging.getLogger(__name__)

class ArticleAnalyzer:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        # Initialize GitHub client if token is available
        self.github_token = os.getenv('GITHUB_TOKEN')
        self.github_client = Github(self.github_token) if self.github_token else None

    def extract_article_content(self, url: str) -> Optional[Dict[str, str]]:
        """Extract content based on URL type."""
        if not url:
            return None

        try:
            parsed_url = urlparse(url)
            
            # Handle GitHub URLs
            if 'github.com' in parsed_url.netloc:
                return self.handle_github_url(url)
            
            # Handle PDF URLs
            if url.lower().endswith('.pdf'):
                return self.handle_pdf_url(url)
            
            # Default web page handling
            return self.handle_webpage_url(url)

        except Exception as e:
            logger.error(f"Error extracting content from {url}: {str(e)}")
            return None

    def handle_github_url(self, url: str) -> Optional[Dict[str, str]]:
        """Handle GitHub repository or file URLs."""
        if not self.github_client:
            logger.warning("GitHub token not configured, falling back to web scraping")
            return self.handle_webpage_url(url)

        try:
            # Extract owner and repo from URL
            parts = urlparse(url).path.split('/')
            if len(parts) < 3:
                return None

            owner = parts[1]
            repo_name = parts[2]
            repo = self.github_client.get_repo(f"{owner}/{repo_name}")

            # Initialize content string
            content_parts = []

            # Add basic repo info
            content_parts.append(f"Repository: {repo.full_name}")
            content_parts.append(f"Description: {repo.description}")
            content_parts.append(f"Stars: {repo.stargazers_count}")
            content_parts.append(f"Language: {repo.language}")

            # If it's a specific file
            if len(parts) > 4 and parts[3] == "blob":
                file_path = '/'.join(parts[4:])
                try:
                    file_content = repo.get_contents(file_path)
                    if isinstance(file_content, list):
                        # It's a directory
                        content_parts.append("\nDirectory contents:")
                        for item in file_content:
                            content_parts.append(f"- {item.path}")
                    else:
                        # It's a file
                        if file_content.size <= 1000000:  # limit to ~1MB
                            decoded_content = base64.b64decode(file_content.content).decode('utf-8')
                            content_parts.append("\nFile contents:")
                            content_parts.append(decoded_content[:10000])  # Limit content length
                except Exception as e:
                    logger.error(f"Error getting file contents: {str(e)}")

            # Add README content if available
            try:
                readme = repo.get_readme()
                content_parts.append("\nREADME:")
                content_parts.append(base64.b64decode(readme.content).decode('utf-8')[:5000])
            except Exception as e:
                logger.error(f"Error getting README: {str(e)}")

            return {
                'url': url,
                'content': '\n\n'.join(content_parts),
                'domain': 'github.com'
            }

        except Exception as e:
            logger.error(f"Error processing GitHub URL {url}: {str(e)}")
            return self.handle_webpage_url(url)

    def handle_pdf_url(self, url: str) -> Optional[Dict[str, str]]:
        """Handle PDF URLs."""
        try:
            # Download the PDF
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()

            # Create a temporary file to store the PDF
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                tmp_file.write(response.content)
                tmp_file_path = tmp_file.name

            try:
                # Read the PDF
                text_content = []
                with open(tmp_file_path, 'rb') as file:
                    pdf_reader = PyPDF2.PdfReader(file)
                    
                    # Extract metadata if available
                    metadata = pdf_reader.metadata
                    if metadata:
                        text_content.append("Document Information:")
                        for key, value in metadata.items():
                            if value and key.startswith('/'):
                                text_content.append(f"{key[1:]}: {value}")
                    
                    # Extract text from each page (limit to first 10 pages)
                    text_content.append("\nDocument Content:")
                    for page_num in range(min(20, len(pdf_reader.pages))):
                        page = pdf_reader.pages[page_num]
                        text_content.append(f"\n--- Page {page_num + 1} ---")
                        text_content.append(page.extract_text())

            finally:
                # Clean up temporary file
                os.unlink(tmp_file_path)

            return {
                'url': url,
                'content': '\n'.join(text_content)[:10000],  # Limit content length
                'domain': 'pdf-document'
            }

        except Exception as e:
            logger.error(f"Error processing PDF URL {url}: {str(e)}")
            return None

    def handle_webpage_url(self, url: str) -> Optional[Dict[str, str]]:
        """Handle regular webpage URLs."""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove script and style elements
            for script in soup(['script', 'style', 'nav', 'footer', 'header']):
                script.decompose()
            
            # Get text content
            text = soup.get_text(separator='\n', strip=True)
            
            # Basic text cleaning
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            content = '\n'.join(lines)
            
            return {
                'url': url,
                'content': content[:10000],  # Limit content length
                'domain': urlparse(url).netloc
            }

        except Exception as e:
            logger.error(f"Error processing webpage URL {url}: {str(e)}")
            return None

    def get_summary_context(self, content: Dict[str, str]) -> str:
        """Format the article content as context for GPT."""
        if not content:
            return "No article content available for analysis."
        
        domain_type = "PDF Document" if content['domain'] == 'pdf-document' else content['domain']
        
        return f"""
        Source: {domain_type}
        URL: {content['url']}

        Content Summary:
        {content['content']}
        
        Note: The above is a summary of the content being discussed in the comments below.
        """