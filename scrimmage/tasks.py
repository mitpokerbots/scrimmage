import time
import boto3

from scrimmage import celery_app, app, db
from scrimmage.models import Bot, BotStatus, Game, GameStatus

def _upload_to_s3(key, data):
  pass

def _get_from_s3(key):
  pass

def _compile_bot(bot):
  bot.status = BotStatus.compiling
  db.session.commit()
  time.sleep(10)
  bot.status = BotStatus.ready
  db.session.commit()


def _play_game(game):
  game.status = GameStatus.in_progress
  db.session.commit()
  time.sleep(10)
  # TODO: FIX RACE CONDITION :(
  game.complete('some/s3/key', challenger_won=bool(int(time.time()*1000)%2)) # Aka just pick one randomly
  db.session.commit()


@celery_app.task(ignore_result=True)
def compile_bot_task(bot_id):
  bot = Bot.query.get(bot_id)
  assert bot.status == BotStatus.uploaded
  try:
    _compile_bot(bot)
  except:
    db.session.rollback()
    bot.status = BotStatus.internal_error
    db.session.commit()


@celery_app.task(ignore_result=True)
def play_game_task(game_id):
  game = Game.query.get(game_id)
  assert game.status == GameStatus.created
  try:
    _play_game(game)
  except:
    db.session.rollback()
    game.status = GameStatus.internal_error
    db.session.commit()

