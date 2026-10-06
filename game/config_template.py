# Engine config.py, rendered by run_match.py for every match.
# When the engine changes for a new season, update engine.py and this file
# together: every name the engine reads via `from config import *` must be
# defined here. Values come from the admin settings page via match.json.

PLAYER_1_NAME = "A"
PLAYER_1_PATH = $player_1_path
PLAYER_2_NAME = "B"
PLAYER_2_PATH = $player_2_path

GAME_LOG_FILENAME = "gamelog"
PLAYER_LOG_SIZE_LIMIT = $PLAYER_LOG_SIZE_LIMIT

ENFORCE_GAME_CLOCK = True
STARTING_GAME_CLOCK = $STARTING_GAME_CLOCK
BUILD_TIMEOUT = $BUILD_TIMEOUT
CONNECT_TIMEOUT = $CONNECT_TIMEOUT
PLAYER_TIMEOUT = 120

NUM_ROUNDS = $NUM_ROUNDS
STARTING_STACK = $STARTING_STACK
BIG_BLIND = $BIG_BLIND
SMALL_BLIND = $SMALL_BLIND
