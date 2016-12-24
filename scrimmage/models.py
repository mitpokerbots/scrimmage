import enum

from scrimmage import app, db

class User(db.Model):
  __tablename__ = 'users'
  id = db.Column(db.Integer, primary_key=True)
  kerberos = db.Column(db.String(128), unique=True, index=True)
  team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  team = db.relationship("Team", back_populates="members")

  def __init__(self, kerberos, team):
    self.kerberos = kerberos
    self.team = team


class Team(db.Model):
  __tablename__ = 'teams'
  id = db.Column(db.Integer, primary_key=True)
  name = db.Column(db.String(128), unique=True)
  members = db.relationship("User", back_populates="team")
  elo = db.Column(db.Float)
  wins = db.Column(db.Integer)
  losses = db.Column(db.Integer)

  # TODO: Bot uploads, versions

  def __init__(self, name):
    self.name = name
    self.wins = 0
    self.losses = 0
    self.elo = 1500.0


class GameRequestStatus(enum.Enum):
  challenged = 'challenged'      # Someone has been challenged to a game
  game_spawned = 'game_created'  # The game has been created, this request is completed
  rejected = 'rejected'          # The game has been rejected, this request is completed


class GameRequest(db.Model):
  __tablename__ = 'game_requests'
  id = db.Column(db.Integer, primary_key=True)
  challenger_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  opponent_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  status = db.Column(db.Enum(GameRequestStatus), nullable=False)
  create_time = db.Column(db.DateTime, default=db.func.now())

  challenger = db.relationship("Team")
  opponent = db.relationship("Team")

  def __init__(self, challenger, opponent):
    self.challenger = challenger
    self.opponent = opponent
    self.status = GameRequestStatus.challenged


class GameStatus(enum.Enum):
  created = 'created'          # Game has been created, has not been spawned.
  spawned = 'spawned'          # Game has been spawned, has not been played yet
  in_progress = 'in_progress'  # Game is currently being played
  error = 'error'              # Game was unable to be played, due to an internal error
  completed = 'completed'      # Game has been completed, there is a winner.


class Game(db.Model):
  __tablename__ = 'games'
  id = db.Column(db.Integer, primary_key=True)
  game_request_id = db.Column(db.Integer, db.ForeignKey('game_requests.id'), nullable=False, unique=True)
  challenger_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  opponent_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  challenger_elo = db.Column(db.Float)
  opponent_elo = db.Column(db.Float)
  create_time = db.Column(db.DateTime, default=db.func.now())
  status = db.Column(db.Enum(GameStatus), nullable=False)

  winner_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  loser_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)

  # TODO: Game logs
  # TODO: Bot version

  game_request = db.relationship("GameRequest")
  challenger = db.relationship("Team")
  opponent = db.relationship("Team")
  winner = db.relationship("Team")
  loser = db.relationship("Team")

  def __init__(self, game_request):
    self.game_request = game_request
    self.challenger = game_request.challenger
    self.opponent = game_request.opponent
    self.challenger_elo = game_request.challenger.elo
    self.opponent_elo = game_request.opponent.elo
    self.status = GameStatus.created
