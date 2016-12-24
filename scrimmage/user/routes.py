from flask import render_template, g

from scrimmage import app

@app.route('/')
def index():
  if not g.is_logged_in:
    return render_template('logged_out.html')
  elif not g.team:
    return render_template('no_team.html')

  return render_template('homepage.html')

