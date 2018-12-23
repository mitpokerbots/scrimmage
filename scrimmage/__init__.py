import os
from celery import Celery
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from werkzeug.contrib.cache import SimpleCache

app = Flask(__name__)
config_object_str = 'scrimmage.config.ProdConfig' if os.environ.get('PRODUCTION', False) else 'scrimmage.config.DevConfig'
app.config.from_object(config_object_str)
db = SQLAlchemy(app)

def make_celery(flask_app):
    celery = Celery(flask_app.import_name, broker=flask_app.config['CELERY_BROKER_URL'])
    celery.conf.update(flask_app.config)
    TaskBase = celery.Task
    class ContextTask(TaskBase):
        abstract = True
        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)
    celery.Task = ContextTask
    return celery


def make_cache(flask_app):
    # Use RedisCache later
    return SimpleCache()


celery_app = make_celery(app)
cache = make_cache(app)

import scrimmage.user
import scrimmage.admin
import scrimmage.tasks
