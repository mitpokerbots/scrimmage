import time
import boto3
import os
import subprocess
import zipfile

from scrimmage import celery_app, app, db
from scrimmage.models import Bot, BotStatus, Game, GameStatus

ENGINE_GIT_HASH = 'b9167c0365af603e7c13cf6aa05f304f628cbb0a'
ENGINE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, 'deps', 'engine_{}.jar'.format(ENGINE_GIT_HASH)))
MAX_ZIP_SIZE = 50 * 1024 * 1024


def verify_zip(zip_file_path):
  total_size = 0
  with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
    for info in zip_ref.infolist():
      total_size += info.file_size
  return total_size <= MAX_ZIP_SIZE:


def _upload_to_s3(key, data):
  pass


def _get_from_s3(key):
  pass


def _play_game(game):
  game.status = GameStatus.in_progress
  db.session.commit()
  time.sleep(10)
  # TODO: FIX RACE CONDITION :(
  game.complete('some/s3/key', challenger_won=bool(int(time.time()*1000)%2)) # Aka just pick one randomly
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

