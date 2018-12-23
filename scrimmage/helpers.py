import boto3
from botocore.client import Config

from scrimmage import app

def _get_s3_context():
  s3_client = boto3.client('s3', config=Config(signature_version='s3v4'))
  return s3_client

def get_s3_object(key):
  client = _get_s3_context()
  return client.get_object(Bucket=app.config['S3_BUCKET'], Key=key)['Body']

def put_s3_object(key, body):
  client = _get_s3_context()
  client.put_object(Body=body, Bucket=app.config['S3_BUCKET'], Key=key)
