from flask import render_template, g, url_for, redirect, request

from scrimmage import app, db
from scrimmage.models import Team, GameRequest, GameRequestStatus, Game
from scrimmage.decorators import team_required, set_flash

@app.route('/')
def index():
  games_to_show = int(g.settings['recent_games_to_show'])
  recent_games = Game.query.order_by(Game.create_time.desc()).limit(games_to_show).all()
  if not g.is_logged_in:
    return render_template('logged_out.html', message="Please log in to continue", recent_games=recent_games)
  elif not g.team:
    return render_template('logged_out.html', message="Thanks for logging in. Unfortuantely, you do not have a team, so this page is hidden.", recent_games=recent_games)
  teams = Team.query.all()
  challengeable_teams = [team for team in teams if team != g.team and team.can_be_challenged()]
  return render_template('homepage.html',
                         teams=teams,
                         challengeable_teams=challengeable_teams,
                         pending_requests=g.team.pending_requests(),
                         recent_games=recent_games)


@app.route('/challenge', methods=['POST'])
@team_required
def challenge():
  team_id = int(request.form['team_id'])
  team = Team.query.get(team_id)
  assert g.settings['challenges_enabled'].lower() == 'true'
  assert g.team.can_challenge()
  assert team.can_be_challenged()

  g_request = GameRequest(g.team, team)
  db.session.add(g_request)

  if team.elo > g.team.elo:
    game = g_request.accept()
    db.session.add(game)
    set_flash("Challenged {}. Since they are higher ELO, the game is now in the queue".format(g_request.opponent.name))
    db.session.commit()
    game.spawn()
  else:
    set_flash("Challenged {}. Since they are lower ELO, they must accept the request.".format(g_request.opponent.name))
    db.session.commit()

  return redirect(url_for('index'))


@app.route('/answer_request/<int:request_id>', methods=['POST'])
@team_required
def answer_request(request_id):
  g_request = GameRequest.query.get(request_id)
  assert g_request.opponent == g.team
  action = request.form['action']

  if action == 'reject':
    g_request.reject()
    db.session.commit()
    set_flash("Rejected {}'s game request.".format(g_request.challenger.name))
  elif action == 'accept':
    game = g_request.accept()
    db.session.add(game)
    db.session.commit()
    game.spawn()
    set_flash("Accepted {}'s game request. It is now in the game queue.".format(g_request.challenger.name))
  return redirect(url_for('index'))
