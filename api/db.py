import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()

# Fetch database parameters
user = os.getenv("DB_USER", "football")
password = os.getenv("DB_PASSWORD") 
host = os.getenv("DB_HOST", "localhost")
port = os.getenv("DB_PORT", "5432")
name = os.getenv("DB_NAME", "premierleague")

# Construct the URL
uri = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"

engine = create_engine(uri, pool_size=5, max_overflow=10)

def get_connection():
    with engine.connect() as conn:
       yield conn