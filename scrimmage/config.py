import os

# Note, it's very important that keys read from the environment have the same name as in the config

class Config(object):
  DEBUG = False
  TESTING = False
  SQLALCHEMY_DATABASE_URI = 'sqlite://:memory:'
  SQLALCHEMY_TRACK_MODIFICATIONS = False
  AUTH_URL_BASE = 'https://jserrino.scripts.mit.edu:444/auth/auth.php'
  MAX_CONTENT_LENGTH = 5 * 1024 * 1024

class ProdConfig(Config):
  SQLALCHEMY_DATABASE_URI = os.getenv('SQLALCHEMY_DATABASE_URI', None)
  CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', None)
  SECRET_KEY = os.getenv('SECRET_KEY', None)
  AUTH_KEY = os.getenv('AUTH_KEY', None)
  SERVER_NAME = 'pokerbots-scrimmage.mit.edu'
  PREFERRED_URL_SCHEME = 'https'
  S3_BUCKET = 'pokerbots-prod'

class DevConfig(Config):
  DEBUG = True
  SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(os.path.dirname(__file__), '..', 'dev.db')
  SQLALCHEMY_TRACK_MODIFICATIONS = True
  SECRET_KEY = 'SUPER SECRET KEY'
  SERVER_NAME = 'localhost:5000'
  PREFERRED_URL_SCHEME = 'http'
  S3_BUCKET = 'pokerbots-dev'
  CELERY_BROKER_URL = 'pyamqp://guest@localhost//'
