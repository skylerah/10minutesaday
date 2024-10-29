from application import create_app
from dotenv import load_dotenv
import os

load_dotenv()  # Load environment variables from .env file

if not os.getenv('OPENAI_API_KEY'):
    raise ValueError("OPENAI_API_KEY environment variable is required")

app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
