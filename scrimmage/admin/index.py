from flask import g, render_template, request, session, redirect, url_for, send_file

from scrimmage import app, db
from scrimmage.decorators import admin_required, set_flash
from scrimmage.helpers import get_s3_object
from scrimmage.models import Game, GameStatus, Announcement

@app.route('/admin/')
@admin_required
def admin_index():
  return render_template('admin/index.html')


@app.route('/admin/announcements', methods=['GET', 'POST'])
@admin_required
def admin_announcements():
  if request.method == 'POST':
    if request.form['action'] == 'create':
      new_announcement = Announcement(
        author_kerberos=g.real_kerberos,
        title=request.form['title'],
        text=request.form['text'],
        is_public=bool(request.form.get('is_public', False))
      )
      db.session.add(new_announcement)
      set_flash('Announcement posted!', level='success')
    elif request.form['action'] == 'delete':
      db.session.delete(Announcement.query.get(request.form['announcement_id']))
      set_flash('Announcement deleted!', level='negative')
    db.session.commit()
  return render_template('admin/announcements.html', announcements=Announcement.query.order_by(Announcement.create_time.desc()).all())


@app.route('/admin/impersonate', methods=['GET', 'POST'])
@admin_required
def admin_impersonate():
  if request.method == 'POST':
    session['kerberos'] = request.form['kerberos']
    return redirect(url_for('index'))
  return render_template('admin/impersonate.html')


@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
  if request.method == 'POST':
    g.settings[request.form['key']] = request.form['value']
    db.session.commit()
    set_flash('Setting changed!', level='success')
  return render_template('admin/settings.html')


@app.route('/admin/games')
@admin_required
def admin_all_games():
  pagination = Game.query.order_by(Game.create_time.desc()).paginate()
  return render_template('admin/all_games.html', pagination=pagination)


@app.route('/admin/game/<int:game_id>/log')
@admin_required
def admin_game_log(game_id):
  game = Game.query.get(game_id)
  assert game.status == GameStatus.completed
  return send_file(get_s3_object(game.log_s3_key), mimetype="text/plain")
