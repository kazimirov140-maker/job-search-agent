import os
import logging
from google.cloud import storage

BUCKET_NAME = "job-agent-501412-state"
DB_FILE = "jobs.db"

logger = logging.getLogger("cloud_storage")

def download_db():
    try:
        client = storage.Client(project="job-agent-501412")
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(DB_FILE)
        if blob.exists():
            blob.download_to_filename(DB_FILE)
            logger.info("Downloaded %s from Cloud Storage bucket %s.", DB_FILE, BUCKET_NAME)
        else:
            logger.info("%s not found in Cloud Storage, starting fresh.", DB_FILE)
    except Exception as e:
        logger.error("Error downloading DB: %s", e)

def upload_db():
    if not os.path.exists(DB_FILE):
        return
    try:
        client = storage.Client(project="job-agent-501412")
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(DB_FILE)
        blob.upload_from_filename(DB_FILE)
        logger.info("Uploaded %s to Cloud Storage bucket %s.", DB_FILE, BUCKET_NAME)
    except Exception as e:
        logger.error("Error uploading DB: %s", e)

def ensure_bucket_exists():
    try:
        client = storage.Client(project="job-agent-501412")
        try:
            client.get_bucket(BUCKET_NAME)
            logger.info("Bucket %s already exists.", BUCKET_NAME)
        except Exception:
            bucket = client.create_bucket(BUCKET_NAME, location="eu")
            logger.info("Created bucket %s.", BUCKET_NAME)
    except Exception as e:
        logger.error("Could not verify/create bucket: %s", e)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_bucket_exists()
