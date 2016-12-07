import time
from flask import redirect, request, session, url_for
from functools import wraps
from hashlib import sha256
from urlparse import urlparse, urlunparse
from urllib import urlencode

from scrimmage import app, db

def logged_in():
  return 'kerberos' in session

def login_required(f):
  @wraps(f)
  def decorated_function(*args, **kwargs):
    if not logged_in():
      return redirect(url_for('login', next=request.url))
    return f(*args, **kwargs)
  return decorated_function

def verify_token(email, time, token):
  if app.debug:
    return True, None
  if abs(time.time() - int(time)) < 2:
    return False, "Token is too old."
  h = sha256()
  h.update(email + time + app.config['SECRET_KEY'])
  if h.hexdigest() != token:
    return False, "Token does not match."
  if email[-8:].lower() != '@mit.edu':
    return False, "Not an @mit.edu email"
  return True, None

def create_redirect(**kwargs):
  url_parts = list(urlparse(app.config['AUTH_URL_BASE']))
  return_url = url_for('login_return', _external=True)
  params = { 'return_url': return_url }
  params.update(kwargs)
  url_parts[4] = urlencode(params)
  return urlunparse(url_parts)


@app.route('/login')
def login():
  if 'next' in request.args:
    return redirect(create_redirect(next=request.args['next']))
  else:
    return redirect(create_redirect())

@app.route('/login_return')
def login_return():
  success, err_msg = verify_token(request.args['email'],
                                  request.args['time'],
                                  request.args['token'])

  if not success:
    return "Login was unsuccessful: " + err_msg, 400

  session['kerberos'] = request.args['email'][:-8]
  session['name'] = request.args['name']
  return redirect(request.args['next'] if 'next' in request.args else url_for('index'))


@app.route('/logout')
def logout():
  session.pop('kerberos', None)
  session.pop('name', None)
  return redirect(url_for('index'))
