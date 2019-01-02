import boto3
from botocore.client import Config

from scrimmage import app

def _get_s3_context():
  if app.debug:
    return boto3.client('s3', config=Config(signature_version='s3v4'))
  else:
    return boto3.client(
      's3',
      region=app.config['S3_REGION'],
      aws_access_key_id=app.config['AWS_ACCESS_KEY_ID'],
      aws_secret_access_key=app.config['AWS_SECRET_ACCESS_KEY'],
      config=Config(signature_version='s3v4')
    )

def get_s3_object(key):
  client = _get_s3_context()
  return client.get_object(Bucket=app.config['S3_BUCKET'], Key=key)['Body']

def put_s3_object(key, body):
  client = _get_s3_context()
  client.put_object(Body=body, Bucket=app.config['S3_BUCKET'], Key=key)
