from flask import render_template, g, url_for, redirect, request

from scrimmage import app
from scrimmage.models import Team, GameRequest, GameRequestStatus
from scrimmage.decorators import team_required

@app.route('/')
def index():
  if not g.is_logged_in:
    return render_template('logged_out.html')
  elif not g.team:
    return render_template('no_team.html')
  teams = Team.query.all()
  challengeable_teams = [team for team in teams if team != g.team and team.can_be_challenged()]
  return render_template('homepage.html', teams=teams, challengeable_teams=challengeable_teams)


@app.route('/challenge', methods=['POST'])
@team_required
def challenge():
  team_id = int(request.form['team_id'])
  team = Team.query.get(team_id)
  assert g.team.can_challenge()
  assert team.can_be_challenged()

  request = GameRequest(g.team, team)
  db.session.add(request)

  if team.elo > g.team.elo:
    game = request.accept()
    db.session.add(game)
    db.session.commit()
    game.spawn()
  else:
    db.session.commit()

  return redirect(url_for('index'))


@app.route('/answer_request/<int:request_id>', methods=['POST'])
@team_required
def answer_request(request_id):
  request = GameRequest.query.get(request_id)
  assert request.opponent == g.team
  action = request.form['action']

  if action == 'reject':
    request.reject()
    db.session.commit()
  elif action == 'accept':
    game = request.accept()
    db.session.add(game)
    db.session.commit()
    game.spawn()
  return redirect(url_for('index'))
