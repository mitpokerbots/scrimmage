import os
from celery import Celery
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_sslify import SSLify
from healthcheck import HealthCheck, EnvironmentDump

app = Flask(__name__)
config_object_str = 'scrimmage.config.ProdConfig' if os.environ.get('PRODUCTION', False) else 'scrimmage.config.DevConfig'
app.config.from_object(config_object_str)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
sslify = SSLify(app)

health = HealthCheck(app, "/check")
envdump = EnvironmentDump(app, "/environment")

def make_celery(flask_app):
    celery = Celery(flask_app.import_name, broker=flask_app.config['CELERY_BROKER_URL'])
    celery.conf.update(flask_app.config)

    # AWS MQ RabbitMQ uses quorum queues for the default "celery" queue.
    # Quorum queues do NOT support "global" QoS. Celery 4 enables global
    # QoS by default which causes:
    #   AMQPNotImplementedError: Basic.consume: (540) NOT_IMPLEMENTED -
    #   queue 'celery' in vhost '/' does not support global qos
    # Force per-consumer QoS here to keep workers connected.
    broker_transport_opts = celery.conf.get('broker_transport_options', {}) or {}
    broker_transport_opts.update({'global_qos': False})
    celery.conf.broker_transport_options = broker_transport_opts
    TaskBase = celery.Task
    class ContextTask(TaskBase):
        abstract = True
        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)
    celery.Task = ContextTask
    return celery


celery_app = make_celery(app)

import scrimmage.user
import scrimmage.admin
import scrimmage.sponsor
import scrimmage.tasks
