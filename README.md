# scrimmage

MIT Pokerbots Scrimmage Server, released under the MIT License

To run locally, do:

- `brew install rabbitmq scons boost postgres`
- `initdb --username=postgres ~/pbots; pg_ctl -D /Users/gabrielramirez/pbots -l logfile start; createdb -U postgres pbots`
- `pip install -r requirements.txt`
- Run `from scrimmage import db; db.create_all()` from a python3 shell

then do

To run the server and worker, run in three separate tabs:

- `rabbitmq-server`
- `python manage.py runserver`
- `celery -A scrimmage.celery_app worker --loglevel=info --concurrency=1`

Production
----------

1. We use [Convox](https://convox.com/) for deploys. Once you make a change, simply run `convox deploy`.

Setting up convox is a little bit of a pain - you have to create the necessary resources and set the required environment variables. Once you have a successful deploy, however, you need to run `convox run python manage.py shell` and then run `from scrimmage import db; db.create_all()` on the first time.


Development
-----------

When you add new database features, do `python manage.py db migrate` and `python manage.py db upgrade`.
