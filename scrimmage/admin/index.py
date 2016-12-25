from flask import render_template, request, session, redirect, url_for

from scrimmage import app
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
