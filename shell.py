import os

from pprint import pprint

from flask import *

from scrimmage import app, db
from scrimmage.models import *

def use_config(config):
  app.config.from_object(config)

def create_db():
  db.create_all()

app.config.from_object('scrimmage.config.DevConfig')

os.environ['PYTHONINSPECT'] = 'True'
