from celery import Celery
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config.from_object('scrimmage.config.DevConfig')
db = SQLAlchemy(app)
celery_app = Celery(__name__, broker=app.config['BROKER_URL'])

import scrimmage.user
import scrimmage.admin
import scrimmage.tasks
