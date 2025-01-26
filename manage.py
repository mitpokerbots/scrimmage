from flask.cli import FlaskGroup
from scrimmage import app, db

cli = FlaskGroup(app)

@cli.command("runserver")
def runserver():
    app.run(host='0.0.0.0', port=5000, debug=True)

if __name__ == '__main__':
    cli()
