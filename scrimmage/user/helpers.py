from functools import wraps
from flask import redirect, request, session, url_for

def logged_in():
  return 'kerberos' in session

def login_required(f):
  @wraps(f)
  def decorated_function(*args, **kwargs):
    if not logged_in():
      return redirect(url_for('login', next=request.url))
    return f(*args, **kwargs)
  return decorated_function
