from flask import render_template

from scrimmage import app
from scrimmage.user.helpers import login_required, logged_in

@app.route('/')
def index():
  if not logged_in():
    return render_template('index.html')
  else:
    return render_template('homepage.html')

