import os

from flask import g, redirect, render_template, request, url_for, send_file

from scrimmage import app, db
from scrimmage.decorators import team_required
from scrimmage.models import Bot, GameRequest, Game, GameStatus
from scrimmage.helpers import get_s3_object, put_s3_object

@app.route('/team')
@team_required
def manage_team():
  outgoing_requests = g.team.outgoing_requests()
  return render_template('manage_team.html', outgoing_requests=outgoing_requests)


@app.route('/team/games')
@team_required
def show_games():
  pagination = (Game.query.filter((Game.challenger_id == g.team.id) | (Game.opponent_id == g.team.id))
                          .order_by(Game.create_time.desc())
                          .paginate())
  return render_template('show_games.html', pagination=pagination)


@app.route('/team/game/<int:game_id>/log')
@team_required
def game_log(game_id):
  game = Game.query.get(game_id)
  assert game.status == GameStatus.completed
  assert game.opponent == g.team or game.challenger == g.team
  return send_file(get_s3_object(game.log_s3_key), mimetype="text/plain")


@app.route('/team/create_bot', methods=['POST'])
@team_required
def create_bot():
  fil = request.files['file']
  name = request.form['name']

  if name == '' or name is None:
    name = os.urandom(4).encode('hex')

  key = os.path.join('bots', str(g.team.id), os.urandom(10).encode('hex') + '.zip')
  put_s3_object(key, fil)

  new_bot = Bot(g.team, name, key)
  db.session.add(new_bot)
  db.session.commit()
  return redirect(url_for('manage_team'))


@app.route('/team/set_bot', methods=['POST'])
@team_required
def set_bot():
  bot_id = int(request.form['bot_id'])
  bot = Bot.query.get(bot_id)
  g.team.set_current_bot(bot)
  db.session.commit()
  return redirect(url_for('manage_team'))
