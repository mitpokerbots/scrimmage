from flask import render_template, g, url_for, redirect, request

from scrimmage import app, db
from scrimmage.decorators import team_required
from scrimmage.models import Bot

@app.route('/team')
@team_required
def manage_team():
  settable_bots = [bot for bot in g.team.bots if bot.is_settable()]
  return render_template('manage_team.html', settable_bots=settable_bots)


@app.route('/team/create_bot', methods=['POST'])
@team_required
def create_bot():
  # TODO: Handle bot uploads
  name = request.form['name']
  new_bot = Bot(g.team, name)
  db.session.add(new_bot)
  db.session.commit()
  new_bot.compile()
  return redirect(url_for('manage_team'))


@app.route('/team/set_bot', methods=['POST'])
@team_required
def set_bot():
  bot_id = int(request.form['bot_id'])
  bot = Bot.query.get(bot_id)
  g.team.set_current_bot(bot)
  db.session.commit()
  return redirect(url_for('manage_team'))
