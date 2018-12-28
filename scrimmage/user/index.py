from flask import render_template, g, url_for, redirect, request

from scrimmage import app, db
from scrimmage.models import Team, GameRequest, GameRequestStatus, Game, Announcement
from scrimmage.decorators import team_required, set_flash

@app.route('/')
def index():
  games_to_show = int(g.settings['recent_games_to_show'])
  recent_games = Game.query.order_by(Game.create_time.desc()).limit(games_to_show).all()
  if not g.is_logged_in:
    announcements = Announcement.query.filter(Announcement.is_public == True).order_by(Announcement.create_time.desc()).all()
    return render_template('logged_out.html', recent_games=recent_games, announcements=announcements)

  announcements = Announcement.query.order_by(Announcement.create_time.desc()).limit(1).all()
  if not g.team:
    return render_template('no_team.html', announcements=announcements)
  
  teams = Team.query.filter(Team.is_disabled == False).all()
  challengeable_teams = [team for team in teams if team.can_be_challenged()]
  return render_template('homepage.html',
                         challengeable_teams=challengeable_teams,
                         pending_requests=g.team.pending_requests(),
                         recent_games=recent_games,
                         announcements=announcements)


@app.route('/announcements')
def announcements():
  announcements = Announcement.query.order_by(Announcement.create_time.desc()).all()
  return render_template('announcements.html', announcements=announcements)


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

  if g_request.should_autoaccept():
    if g.team.can_initiate():
      game = g_request.accept(was_automatic=True)
      db.session.add(game)
      set_flash("Challenged {}. The game is now in the queue".format(g_request.opponent.name), level='success')
      db.session.commit()
      game.spawn()
    else:
      set_flash("Game could not be spawned since you have too many games currently running. Please wait a little bit.", level='warning')
  else:
    set_flash("Challenged {}. Since they are lower ELO, they must accept the request.".format(g_request.opponent.name), level='success')
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
    set_flash("Rejected {}'s game request.".format(g_request.challenger.name), level='success')
  elif action == 'accept':
    if g.team.can_initiate():
      game = g_request.accept(was_automatic=False)
      db.session.add(game)
      db.session.commit()
      game.spawn()
      set_flash("Accepted {}'s game request. It is now in the game queue.".format(g_request.challenger.name), level='success')
    else:
      set_flash("Could not accept the game request, since you have too many games currently running. Please wait a little bit", level='warning')
  return redirect(url_for('index'))
