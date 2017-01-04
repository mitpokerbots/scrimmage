import os
import boto3

from flask import g, redirect, render_template, request, url_for

from scrimmage import app, db
from scrimmage.decorators import team_required
from scrimmage.models import Bot, GameRequest, Game

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


@app.route('/team/create_bot', methods=['POST'])
@team_required
def create_bot():
  fil = request.files['file']
  name = request.form['name']

  if name == '' or name is None:
    name = os.urandom(4).encode('hex')

  key = os.path.join('bots', str(g.team.id), os.urandom(10).encode('hex'))
  s3_client = boto3.client('s3')
  s3_client.put_object(Body=fil, Bucket=app.config['S3_BUCKET'], Key=key)

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
