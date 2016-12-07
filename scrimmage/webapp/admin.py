from flask import render_template, request, abort, session
from functools import wraps

from scrimmage import app, db
from scrimmage.webapp.login import login_required, logged_in

def is_admin():
  return logged_in() and session['kerberos'] in set(['jserrino', 'sidds'])

@app.context_processor
def inject_admin():
  return { 'is_admin': is_admin() }

def admin_required(f):
  @wraps(f)
  def decorated_function(*args, **kwargs):
    if not is_admin():
      abort(404)
    return f(*args, **kwargs)
  return decorated_function

@app.route('/admin/')
@admin_required
def admin_index():
  return render_template('admin.html')
