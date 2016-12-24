from flask import abort, session, g
from functools import wraps

from scrimmage import app, db
from scrimmage.models import User

ADMINS = set(['jserrino', 'sidds'])

@app.before_request
def set_up_g():
  """Sets up the following:
    g.is_logged_in
    g.is_admin
    g.kerberos
    g.team
  """
  g.is_logged_in = False
  g.is_admin = False
  g.team = None
  g.kerberos = None
  if 'kerberos' not in session:
    return
  g.is_logged_in = True
  g.kerberos = session['kerberos']
  g.is_admin = session['real_kerberos'] in ADMINS
  user = User.query.filter(User.kerberos == g.kerberos).one_or_none()
  if user is not None:
    g.team = user.team


def login_required(f):
  @wraps(f)
  def decorated_function(*args, **kwargs):
    if not g.is_logged_in:
      return redirect(url_for('login', next=request.url))
    return f(*args, **kwargs)
  return decorated_function


def team_required(f):
  @wraps(f)
  def decorated_function(*args, **kwargs):
    if g.team is None:
      abort(404)
    return f(*args, **kwargs)
  return decorated_function


def admin_required(f):
  @wraps(f)
  def decorated_function(*args, **kwargs):
    if not g.is_admin:
      abort(404)
    return f(*args, **kwargs)
  return decorated_function
