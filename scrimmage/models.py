import enum, datetime

from scrimmage import app, db

class AdminSetting(db.Model):
  __tablename__ = 'settings'
  id = db.Column(db.Integer, primary_key=True)
  key = db.Column(db.String(128), unique=True, index=True)
  value = db.Column(db.String(128), unique=True, index=True)

  def __init__(self, key, value):
    self.key = key
    self.value = value


class User(db.Model):
  __tablename__ = 'users'
  id = db.Column(db.Integer, primary_key=True)
  kerberos = db.Column(db.String(128), unique=True, index=True)
  team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  team = db.relationship("Team", back_populates="members")

  def __init__(self, kerberos, team):
    self.kerberos = kerberos
    self.team = team


class BotStatus(enum.Enum):
  uploaded = 'uploaded'    # The bot is uploaded and a task has been created to compile
  compiling = 'compiling'  # The bot is compiling
  error = 'error'          # The bot's compilation errored
  ready = 'ready'          # The bot is ready to be used.


class Bot(db.Model):
  __tablename__ = 'bots'
  id = db.Column(db.Integer, primary_key=True)
  team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  team = db.relationship("Team", foreign_keys=team_id, back_populates="bots")
  name = db.Column(db.String(128), nullable=False)
  wins = db.Column(db.Integer)
  losses = db.Column(db.Integer)
  status = db.Column(db.Enum(BotStatus), nullable=False)
  create_time = db.Column(db.DateTime, default=db.func.now())

  # TODO: Raw link
  # TODO: Compiled link
  # TODO: Compile logs

  def __init__(self, team, name):
    self.team = team
    self.name = name
    self.wins = 0
    self.losses = 0
    self.status = BotStatus.uploaded

  def is_settable(self):
    return self.status == BotStatus.ready

  def friendly_status(self):
    if self.status == BotStatus.uploaded:
      return "Uploaded"
    if self.status == BotStatus.compiling:
      return "Compiling"
    if self.status == BotStatus.error:
      return "Compilation Error"
    if self.status == BotStatus.ready:
      return "Ready"

  def compile(self):
    assert self.status == BotStatus.uploaded
    # Don't actually do this, this is a placeholder
    self.status = BotStatus.ready
    db.session.commit()
    # TODO: Spin off a task to compile the bot


class Team(db.Model):
  __tablename__ = 'teams'
  id = db.Column(db.Integer, primary_key=True)
  name = db.Column(db.String(128), unique=True)
  members = db.relationship("User", back_populates="team")
  elo = db.Column(db.Float)
  wins = db.Column(db.Integer)
  losses = db.Column(db.Integer)
  current_bot_id = db.Column(db.Integer, db.ForeignKey('bots.id'))

  bots = db.relationship("Bot", back_populates="team", primaryjoin=(id == Bot.team_id))
  current_bot = db.relationship("Bot", foreign_keys=current_bot_id)

  def __init__(self, name):
    self.name = name
    self.wins = 0
    self.losses = 0
    self.elo = 1500.0

  def can_be_deleted(self):
    return ((self.wins + self.losses == 0) and
            len(self.members) == 0 and
            len(self.bots) == 0)

  def can_challenge(self):
    return self.current_bot is not None

  def can_be_challenged(self):
    return self.current_bot is not None

  def set_current_bot(self, bot):
    assert bot.team == self
    assert bot.status == BotStatus.ready
    self.current_bot = bot

  def pending_requests(self):
    return (GameRequest.query.filter(GameRequest.opponent_id == self.id)
                             .filter(GameRequest.status == GameRequestStatus.challenged)
                             .order_by(GameRequest.create_time.desc())
                             .all())

  def outgoing_requests(self):
    return (GameRequest.query.filter(GameRequest.challenger_id == self.id)
                             .filter(GameRequest.status == GameRequestStatus.challenged)
                             .order_by(GameRequest.create_time.desc())
                             .all())


class GameRequestStatus(enum.Enum):
  challenged = 'challenged'      # Someone has been challenged to a game
  accepted = 'accepted'          # The game has been accepted, this request is completed
  rejected = 'rejected'          # The game has been rejected, this request is completed


class GameRequest(db.Model):
  __tablename__ = 'game_requests'
  id = db.Column(db.Integer, primary_key=True)
  challenger_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  opponent_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  status = db.Column(db.Enum(GameRequestStatus), nullable=False)
  create_time = db.Column(db.DateTime, default=db.func.now())

  challenger = db.relationship("Team", foreign_keys=challenger_id)
  opponent = db.relationship("Team", foreign_keys=opponent_id)

  def __init__(self, challenger, opponent):
    assert challenger.can_challenge()
    assert opponent.can_be_challenged()
    self.challenger = challenger
    self.opponent = opponent
    self.status = GameRequestStatus.challenged

  def friendly_status(self):
    if self.status == GameRequestStatus.challenged:
      return "Challenged"
    if self.status == GameRequestStatus.accepted:
      return "Accepted"
    if self.status == GameRequestStatus.rejected:
      return "Rejected"

  def reject(self):
    assert self.status == GameRequestStatus.challenged
    self.status = GameRequestStatus.rejected

  def accept(self):
    assert self.status == GameRequestStatus.challenged
    self.status = GameRequestStatus.accepted
    return Game(self)


class GameStatus(enum.Enum):
  created = 'created'          # Game has been created, has not been spawned.
  in_progress = 'in_progress'  # Game is currently being played
  error = 'error'              # Game was unable to be played, due to an internal error
  completed = 'completed'      # Game has been completed, there is a winner.

K = 40

def elo(winner, loser):
  wr = 10**(winner/400.0)
  lr = 10**(loser/400.0)

  e_w = wr/(wr + lr)
  e_l = lr/(wr + lr)

  return (winner + (1.0 - e_w)*K, loser + (0.0 - e_l)*K)


class Game(db.Model):
  __tablename__ = 'games'
  id = db.Column(db.Integer, primary_key=True)
  game_request_id = db.Column(db.Integer, db.ForeignKey('game_requests.id'), nullable=False, unique=True)
  challenger_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  opponent_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  challenger_elo = db.Column(db.Float) # Used just for statistics afterwards
  opponent_elo = db.Column(db.Float)   # Same as above
  create_time = db.Column(db.DateTime, default=db.func.now())
  completed_time = db.Column(db.DateTime)
  status = db.Column(db.Enum(GameStatus), nullable=False)
  challenger_bot_id = db.Column(db.Integer, db.ForeignKey('bots.id'), nullable=False)
  opponent_bot_id = db.Column(db.Integer, db.ForeignKey('bots.id'), nullable=False)

  winner_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
  loser_id = db.Column(db.Integer, db.ForeignKey('teams.id'))

  # TODO: Game logs

  game_request = db.relationship("GameRequest")
  challenger = db.relationship("Team", foreign_keys=challenger_id)
  opponent = db.relationship("Team", foreign_keys=opponent_id)
  challenger_bot = db.relationship("Bot", foreign_keys=challenger_bot_id)
  opponent_bot = db.relationship("Bot", foreign_keys=opponent_bot_id)
  winner = db.relationship("Team", foreign_keys=winner_id)
  loser = db.relationship("Team", foreign_keys=loser_id)

  def __init__(self, game_request):
    self.game_request = game_request
    self.challenger = game_request.challenger
    self.opponent = game_request.opponent

    self.challenger_bot = self.challenger.current_bot
    self.opponent_bot = self.opponent.current_bot

    self.challenger_elo = self.challenger.elo
    self.opponent_elo = self.opponent.elo

    self.status = GameStatus.created

  def friendly_status(self):
    if self.status == GameStatus.created:
      return "Queued"
    if self.status == GameStatus.in_progress:
      return "In Progress"
    if self.status == GameStatus.error:
      return "Errored"
    if self.status == GameStatus.completed:
      return "Completed"

  def spawn(self):
    assert self.status == GameStatus.created
    # Don't actually do this, this is a placeholder
    self.status = GameStatus.in_progress
    db.session.commit()
    self.complete(bool(datetime.datetime.now().microsecond % 2))
    db.session.commit()
    # TODO: Actually spawn game

  def complete(self, challenger_won):
    assert self.status == GameStatus.in_progress
    if challenger_won:
      self.winner = self.challenger
      self.loser = self.opponent
      self.challenger_bot.wins += 1
      self.opponent_bot.losses += 1
    else:
      self.winner = self.opponent
      self.loser = self.challenger
      self.opponent_bot.wins += 1
      self.challenger_bot.losses += 1

    self.winner.wins += 1
    self.loser.losses += 1

    self.winner.elo, self.loser.elo = elo(self.winner.elo, self.loser.elo)

    self.status = GameStatus.completed
    self.completed_time = datetime.datetime.now()
