import os
from celery import Celery
from kombu import Exchange, Queue
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
    broker_transport_opts = getattr(celery.conf, 'broker_transport_options', {}) or {}
    broker_transport_opts.update({'global_qos': False})
    celery.conf.broker_transport_options = broker_transport_opts

    # Explicitly declare the default queue as a classic queue (not quorum),
    # so it fully supports QoS and works with older Celery/kombu versions.
    # Use a new queue name to avoid clashing with the existing quorum queue.
    default_exchange = Exchange('celery', type='direct')
    celery.conf.task_default_queue = 'celery-classic'
    celery.conf.task_default_exchange = 'celery'
    celery.conf.task_default_routing_key = 'celery'
    celery.conf.task_queues = (
        Queue(
            'celery-classic',
            default_exchange,
            routing_key='celery',
            queue_arguments={'x-queue-type': 'classic'},
        ),
    )
    
    # Configure task time limits to prevent stuck games
    # Hard limit: 20 minutes (1200s) - task is killed after this
    # Soft limit: 18 minutes (1080s) - task receives SoftTimeLimitExceeded exception
    celery.conf.task_time_limit = 1200  # 20 minutes hard limit
    celery.conf.task_soft_time_limit = 1080  # 18 minutes soft limit
    
    # Configure periodic tasks (beat schedule)
    # Run cleanup task every 5 minutes to detect stuck games
    celery.conf.beat_schedule = {
        'cleanup-stuck-games': {
            'task': 'scrimmage.tasks.cleanup_stuck_games_task',
            'schedule': 300.0,  # Run every 5 minutes
        },
    }
    
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
