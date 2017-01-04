# scrimmage

MIT Pokerbots Scrimmage Server, released under the MIT License

To run locally, do:

- `brew install rabbitmq`
- `pip install -r requirements.txt`
- `brew install scons`
- `brew install boost`

then do

To run the server and worker, run in three separate tabs:

- `rabbitmq-server`
- `python runserver.py`
- `celery -A scrimmage.celery_app worker --loglevel=info`
