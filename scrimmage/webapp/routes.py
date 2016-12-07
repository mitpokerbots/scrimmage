from flask import render_template, request
from functools import wraps

from scrimmage import app, db
from scrimmage.webapp.login import login_required, logged_in

@app.route('/')
def index():
  if not logged_in():
    return render_template('index.html')
  else:
    return render_template('homepage.html')

