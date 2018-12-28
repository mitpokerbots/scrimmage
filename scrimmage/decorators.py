from flask import abort, session, g
from functools import wraps

from scrimmage import app, db
from scrimmage.models import User
from scrimmage.settings import settings

import humanize
from pytz import timezone
import datetime

ADMINS = set(['jserrino', 'nilai', 'davidja', 'larryq', 'shreyass'])

def is_admin(kerberos):
  return kerberos in ADMINS or kerberos in [kerb.lower().strip() for kerb in settings['extra_admins'].split(',')]

@app.before_request
def set_up_g():
  """Sets up the following:
    g.is_logged_in
    g.is_admin
    g.kerberos
    g.real_kerberos
    g.team
  """
  g.settings = settings
  g.is_logged_in = False
  g.is_admin = False
  g.team = None
  g.kerberos = None
  g.real_kerberos = None
  if 'kerberos' not in session:
    return
  g.is_logged_in = True
  g.kerberos = session['kerberos']
  g.real_kerberos = session['real_kerberos']
  g.is_admin = is_admin(session['real_kerberos'])
  user = User.query.filter(User.kerberos == g.kerberos).one_or_none()
  if user is not None:
    g.team = user.team


@app.context_processor
def flash():
  return dict(flash=session.pop('flash', None), flash_level=session.pop('flash_level', None))


@app.template_filter('naturaltime')
def naturaltime(d):
  return humanize.naturaltime(datetime.datetime.now(timezone('America/New_York')) - d.replace(tzinfo=timezone('UTC')))


def set_flash(message, level=''):
  session['flash'] = message
  session['flash_level'] = level


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
