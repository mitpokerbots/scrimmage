from flask import abort, session
from functools import wraps

from scrimmage import app
from scrimmage.user.helpers import logged_in

def is_admin():
  return logged_in() and session['kerberos'] in set(['jserrino', 'sidds'])

def admin_required(f):
  @wraps(f)
  def decorated_function(*args, **kwargs):
    if not is_admin():
      abort(404)
    return f(*args, **kwargs)
  return decorated_function
