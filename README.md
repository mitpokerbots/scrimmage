# scrimmage

MIT Pokerbots Scrimmage Server, released under the MIT License

To run locally, do:

- `brew install rabbitmq scons boost`
- `pip install -r requirements.txt`
- `python manage.py db upgrade`

then do

To run the server and worker, run in three separate tabs:

- `rabbitmq-server`
- `python manage.py runserver`
- `celery -A scrimmage.celery_app worker --loglevel=info --concurrency=1`

Production
----------

1. We use [Convox](https://convox.com/) for deploys. Once you make a change, simply run `convox deploy`.


Development
-----------

When you add new database features, do `python manage.py db migrate` and `python manage.py db upgrade`.
