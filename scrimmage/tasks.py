import time

from scrimmage import celery_app, db
from scrimmage.models import Bot, BotStatus

@celery_app.task(ignore_result=True)
def compile(bot_id):
  bot = Bot.query.filter(Bot.id == bot_id).one_or_none()
  assert bot.status == BotStatus.uploaded
  bot.status = BotStatus.compiling
  db.session.commit()
  time.sleep(10)
  bot.status = BotStatus.ready
  db.session.commit()
