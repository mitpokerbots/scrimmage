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

Production
----------

1. Copy the start server and start worker scripts from the scripts folder and place them in the top level directory (same directory as runserver.py)
2. Rename them and get rid of the `-template`
3. Fill them in with the correct config values
4. Run the scripts to run them in production