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
from scrimmage.models import Bot, Game, Team, GameStatus
from scrimmage.helpers import get_s3_object, put_s3_object

from sqlalchemy.orm import raiseload

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
    if total_size > MAX_ZIP_SIZE:
      return False, 'Bot zip would be too large unzipped'
    return True, None
  except zipfile.BadZipfile, zipfile.LargeZipfile:
    return False, 'Bot zip file is malformed'


def _safe_name(name):
  return re.sub(r'[^a-z0-9_\-]', '-', name.lower())


def _download_and_verify(bot, tmp_dir):
  bot_dir = os.path.join(tmp_dir, os.urandom(10).encode('hex'))
  os.mkdir(bot_dir)
  bot_download_dir = os.path.join(bot_dir, 'download')
  os.mkdir(bot_download_dir)
  bot_extract_dir = os.path.join(bot_dir, 'source')
  os.mkdir(bot_extract_dir)

  bot_zip_path = os.path.join(bot_download_dir, 'bot.zip')
  with open(bot_zip_path, 'w') as bot_zip_file:
    bot_zip_file.write(get_s3_object(bot.s3_key).read())

  valid_zip, msg = _verify_zip(bot_zip_path)
  if not valid_zip:
    return False, msg

  try:
    with zipfile.ZipFile(bot_zip_path, 'r') as z:
      z.extractall(bot_extract_dir)

    for root, dirs, files in os.walk(bot_extract_dir):
      if 'SConstruct' in files:
        os.chmod(os.path.join(root, 'pokerbot.sh'), 0777)
        subprocess.check_call(['scons', '--clean'], cwd=root, env=_get_environment())
        return True, root

    return False, 'Bot zip has no SConstruct file.'
  except subprocess.CalledProcessError:
    return False, 'Bot failed to compile.'
  except OSError:
    return False, 'Bot zip is missing files. (Maybe missing pokerbot.sh?)'


def _get_winner(game_log):
  matches = re.search(r'FINAL: [^ ]+ \((-?\d+)\) [^ ]+ \((-?\d+)\)', game_log)
  bot_a_score = int(matches.group(1))
  bot_b_score = int(matches.group(2))
  if bot_a_score > bot_b_score:
    return 'a'
  elif bot_b_score < bot_a_score:
    return 'b'
  else:
    return 'tie'


K = 40
def _elo(team_a, team_b, winner):
  maximum = max(team_a, team_b)
  ar = 10**((team_a - maximum)/400.0)
  br = 10**((team_b - maximum)/400.0)

  expected_a = ar/(ar + br)
  actual_a = 0.5 if winner == 'tie' else (1.0 if winner == 'a' else 0.0)

  expected_b = br/(ar + br)
  actual_b = 0.5 if winner == 'tie' else (1.0 if winner == 'b' else 0.0)

  new_a_elo = team_a + (actual_a - expected_a)*K
  new_b_elo = team_b + (actual_b - expected_b)*K

  return new_a_elo, new_b_elo


def _finish_game(game, challenger, challenger_bot, opponent, opponent_bot, game_log, winner):
  log_key = os.path.join('logs', '{}_{}_{}.txt'.format(challenger.id, opponent.id, os.urandom(10).encode('hex')))
  put_s3_object(log_key, game_log)

  ### CRITICAL SECTION STARTS HERE
  ### if you do add a lock here, make sure to use a context manager or similar, since something could
  ### cause an exception during this section and the lock would never get released.
  db.session.expire_all()

  challenger_won = bool(ord(os.urandom(1)) % 2) if winner == 'tie' else (True if winner == 'challenger' else False)

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


def _run_bots(bot_a, bot_a_name, bot_b, bot_b_name):
  with tempfile.TemporaryDirectory() as tmp_dir:
    is_valid_a_bot, a_path = _download_and_verify(bot_a, tmp_dir)
    is_valid_b_bot, b_path = _download_and_verify(bot_b, tmp_dir)

    if not is_valid_a_bot and not is_valid_b_bot:
      return 'tie', 'Both bots are invalid, so the game is tied\n\n{}: {}\n{}: {}'.format(bot_a_name, a_path, bot_b_name, b_path)
    elif not is_valid_a_bot:
      return 'b', 'Bot {} is invalid, so {} wins.\n\n{}: {}'.format(bot_a_name, bot_b_name, bot_a_name, a_path)
    elif not is_valid_b_bot:
      return 'a', 'Bot {} is invalid, so {} wins.\n\n{}: {}'.format(bot_b_name, bot_a_name, bot_b_name, b_path)

    game_dir = os.path.join(tmp_dir, 'game')
    os.mkdir(game_dir)

    with open(os.path.join(game_dir, 'config.txt'), 'w') as config_file:
      config_txt = render_template(
        'config.txt',
        bot_a={
          'name': bot_a_name,
          'path': a_path
        },
        bot_b={
          'name': bot_b_name,
          'path': b_path
        }
      )
      config_file.write(config_txt)

    subprocess.check_call(['java', '-jar', ENGINE_PATH], cwd=game_dir, env=_get_environment())

    with open(os.path.join(game_dir, 'gamelog.txt'), 'r') as game_log_file:
      game_log = game_log_file.read()

    return _get_winner(game_log), game_log




def _run_bots_and_upload(bot_a, bot_a_name, bot_b, bot_b_name):
  winner, game_log = _run_bots(bot_a, bot_a_name, bot_b, bot_b_name)
  log_key = os.path.join('logs', '{}_{}.txt'.format(int(time.time()), os.urandom(20).encode('hex')))
  put_s3_object(log_key, game_log)
  return winner, log_key



def _multiple_with_for_update(cls, pks):
  query = cls.query.options(raiseload('*')).filter(cls.id.in_(pks)).with_for_update()
  results = query.all()
  mapping = { result.id: result for result in results }
  return tuple([mapping[pk] for pk in pks])



@celery_app.task(ignore_result=True)
def play_game_task(game_id):
  game = Game.query.get(game_id)
  assert game.status == GameStatus.created or game.status == GameStatus.internal_error
  game.status = GameStatus.in_progress
  db.session.commit()

  challenger = game.challenger
  challenger_bot = game.challenger_bot
  challenger_bot_id = challenger_bot.id
  challenger_name = "challenger_{}".format(_safe_name(game.challenger.name))

  opponent = game.opponent
  opponent_bot = game.opponent_bot
  opponent_bot_id = opponent_bot.id
  opponent_name = "opponent_{}".format(_safe_name(game.opponent.name))

  try:
    winner, log_key = _run_bots_and_upload(challenger_bot, challenger_name, opponent_bot, opponent_name)
    db.session.expire_all()

    # Reload stuff from DB.
    game = Game.query.options(raiseload('*')).with_for_update().get(game_id)
    challenger, opponent = _multiple_with_for_update(Team, [game.challenger_id, game.opponent_id])
    challenger_bot, opponent_bot = _multiple_with_for_update(Bot, [challenger_bot_id, opponent_bot_id])

    winner = 'ab'[ord(os.urandom(1)) % 2] if winner == 'tie' else winner

    # Relevant database updates.
    game.winner_id = challenger.id if winner == 'a' else opponent.id
    game.loser_id = opponent.id if winner == 'a' else challenger.id
    challenger.wins += int(winner == 'a')
    challenger.losses += int(winner == 'b')
    challenger_bot.wins += int(winner == 'a')
    challenger_bot.losses += int(winner == 'b')
    opponent.wins += int(winner == 'b')
    opponent.losses += int(winner == 'a')
    opponent_bot.wins += int(winner == 'b')
    opponent_bot.losses += int(winner == 'a')
    
    game.challenger_elo = challenger.elo
    game.opponent_elo = opponent.elo
    challenger.elo, opponent.elo = _elo(challenger.elo, opponent.elo, winner)

    game.status = GameStatus.completed
    game.completed_time = datetime.datetime.now()
    game.log_s3_key = log_key

    db.session.commit()

  except:
    db.session.rollback()
    game = Game.query.get(game_id)
    game.status = GameStatus.internal_error
    db.session.commit()
    raise



