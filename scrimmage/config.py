import os

class Config(object):
  DEBUG = False
  TESTING = False
  SQLALCHEMY_DATABASE_URI = 'sqlite://:memory:'
  SQLALCHEMY_TRACK_MODIFICATIONS = False
  AUTH_URL_BASE = 'https://jserrino.scripts.mit.edu:444/auth/auth.php'
  UPLOAD_FOLDER = 'uploads'

class ProductionConfig(Config):
  SQLALCHEMY_DATABASE_URI = os.getenv('SQLALCHEMY_DATABASE_URI', None)
  SECRET_KEY = os.getenv('SECRET_KEY', None)
  AUTH_KEY = os.getenv('AUTH_KEY', None)
  SERVER_NAME = 'pokerbots-scrimmage.mit.edu'
  PREFERRED_URL_SCHEME = 'https'
  S3_BUCKET = 'pokerbots-prod'
  BROKER_URL = 'pyamqp://guest@localhost//'

class DevConfig(Config):
  DEBUG = True
  SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(os.path.dirname(__file__), '..', 'dev.db')
  SQLALCHEMY_TRACK_MODIFICATIONS = True
  SECRET_KEY = 'SUPER SECRET KEY'
  SERVER_NAME = 'localhost:5000'
  PREFERRED_URL_SCHEME = 'http'
  S3_BUCKET = 'pokerbots-dev'
  BROKER_URL = 'pyamqp://guest@localhost//'
