import time
import re
import boto3
import os
import subprocess
import datetime
import zipfile
import jinja2

from backports import tempfile

from scrimmage import celery_app, app, db
from scrimmage.models import Bot, Game, GameStatus
from scrimmage.helpers import get_s3_object, put_s3_object

ENGINE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, 'deps', 'engine.jar'))
MAX_ZIP_SIZE = 10 * 1024 * 1024 * 1024

def render_template(tpl_path, **context):
    tpl_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'templates', tpl_path))
    path, filename = os.path.split(tpl_path)
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(path or './')
    ).get_template(filename).render(context)


def _verify_zip(zip_file_path):
  try:
    total_size = 0
    with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
      for info in zip_ref.infolist():
        total_size += info.file_size
    return total_size <= MAX_ZIP_SIZE
  except zipfile.BadZipFile:
    return False
  except zipfile.LargeZipFile:
    return False


def _safe_name(name):
  return re.sub(r'[^a-z0-9_\-]', '-', name.lower())


def _clean_dir(path):
  subprocess.check_call(['scons', '--clean'], cwd=path, env=_get_environment())


def _download_and_verify(player, bot, tmp_dir):
  try:
    bot_dir = os.path.join(tmp_dir, os.urandom(10).encode('hex'))
    os.mkdir(bot_dir)
    bot_download_dir = os.path.join(bot_dir, 'download')
    os.mkdir(bot_download_dir)
    bot_extract_dir = os.path.join(bot_dir, 'source')
    os.mkdir(bot_extract_dir)

    bot_zip_path = os.path.join(bot_download_dir, 'bot.zip')
    with open(bot_zip_path, 'w') as bot_zip_file:
      bot_zip_file.write(get_s3_object(bot.s3_key).read())

    if not _verify_zip(bot_zip_path):
      return False

    with zipfile.ZipFile(bot_zip_path, 'r') as z:
      z.extractall(bot_extract_dir)

    for root, dirs, files in os.walk(bot_extract_dir):
      if 'SConstruct' in files:
        os.chmod(os.path.join(root, 'pokerbot.sh'), 0777)
        _clean_dir(root)
        return root

    return False
  except OSError:
    return False


def _did_challenger_win(game_log):
  matches = re.search(r'FINAL: [^ ]+ \((-?\d+)\) [^ ]+ \((-?\d+)\)', game_log)
  challenger_score = int(matches.group(1))
  opponent_score = int(matches.group(2))
  if challenger_score > opponent_score:
    return True
  elif challenger_score < opponent_score:
    return False
  else:
    return bool(ord(os.urandom(1)) % 2)


K = 40
def _elo(winner, loser):
  wr = 10**(winner/400.0)
  lr = 10**(loser/400.0)

  e_w = wr/(wr + lr)
  e_l = lr/(wr + lr)

  return (winner + (1.0 - e_w)*K, loser + (0.0 - e_l)*K)


def _finish_game(game, challenger, challenger_bot, opponent, opponent_bot, game_log, challenger_won):
  log_key = os.path.join('logs', '{}_{}_{}.txt'.format(challenger.id, opponent.id, os.urandom(10).encode('hex')))
  put_s3_object(log_key, game_log)

  ### CRITICAL SECTION STARTS HERE
  ### if you do add a lock here, make sure to use a context manager or similar, since something could
  ### cause an exception during this section and the lock would never get released.
  db.session.expire_all()

  if challenger_won:
    game.winner = challenger
    game.loser = opponent
    challenger.wins += 1
    challenger_bot.wins += 1
    opponent.losses += 1
    opponent_bot.losses += 1
    challenger.elo, opponent.elo = _elo(challenger.elo, opponent.elo)
  else:
    game.winner = opponent
    game.loser = challenger
    opponent.wins += 1
    opponent_bot.wins += 1
    challenger.losses += 1
    challenger_bot.losses += 1
    opponent.elo, challenger.elo = _elo(opponent.elo, challenger.elo)

  game.status = GameStatus.completed
  game.completed_time = datetime.datetime.now()
  game.log_s3_key = log_key

  db.session.commit()
  ### CRITICAL SECTION ENDS HERE


def _get_environment():
  base = os.environ.copy()
  for key in app.config.keys():
    if key in os.environ:
      del base[key]
  return base


def _play_game(game):
  game.status = GameStatus.in_progress
  db.session.commit()

  challenger = game.challenger
  challenger_bot = challenger.current_bot
  challenger_name = "challenger_{}".format(_safe_name(game.challenger.name))

  opponent = game.opponent
  opponent_bot = opponent.current_bot
  opponent_name = "opponent_{}".format(_safe_name(game.opponent.name))

  with tempfile.TemporaryDirectory() as tmp_dir:
    challenger_bot_path = _download_and_verify(challenger, challenger_bot, tmp_dir)
    if not challenger_bot_path:
      game_log = "{} lost since the bot's zip was malformed\n".format(challenger_name)
      return _finish_game(game, challenger, challenger_bot, opponent, opponent_bot, game_log, False)

    opponent_bot_path = _download_and_verify(opponent, opponent_bot, tmp_dir)
    if not opponent_bot_path:
      game_log = "{} lost since the bot's zip was malformed\n".format(opponent_name)
      return _finish_game(game, challenger, challenger_bot, opponent, opponent_bot, game_log, True)

    challenger_dict = { 'name': challenger_name, 'path': challenger_bot_path }
    opponent_dict = { 'name': opponent_name, 'path': opponent_bot_path }

    game_dir = os.path.join(tmp_dir, 'game')
    os.mkdir(game_dir)

    with open(os.path.join(game_dir, 'config.txt'), 'w') as config_file:
      config_txt = render_template('config.txt', challenger=challenger_dict, opponent=opponent_dict)
      print(config_txt)
      config_file.write(config_txt)

    subprocess.check_call(['java', '-jar', ENGINE_PATH], cwd=game_dir, env=_get_environment())

    with open(os.path.join(game_dir, 'gamelog.txt'), 'r') as game_log_file:
      game_log = game_log_file.read()

    challenger_won = _did_challenger_win(game_log)

    return _finish_game(game, challenger, challenger_bot, opponent, opponent_bot, game_log, challenger_won)


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
    raise

