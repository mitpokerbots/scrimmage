from flask import g, render_template, request, session, redirect, url_for

from scrimmage import app, db
from scrimmage.decorators import admin_required

@app.route('/admin/')
@admin_required
def admin_index():
  return render_template('admin/index.html')

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
