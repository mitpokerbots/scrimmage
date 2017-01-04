import boto3

from scrimmage import app

def _get_s3_context():
  s3_client = boto3.client('s3')

def get_s3_object(key):
  client = boto3.client('s3')
  return client.get_object(Bucket=app.config['S3_BUCKET'], Key=key)['Body']

def put_s3_object(key, body):
  client = boto3.client('s3')
  client.put_object(Body=body, Bucket=app.config['S3_BUCKET'], Key=key)
