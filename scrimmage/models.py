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


class Announcement(db.Model):
  __tablename__ = 'announcements'
  id = db.Column(db.Integer, primary_key=True)
  author_kerberos = db.Column(db.String(128))
  title = db.Column(db.String(256))
  text = db.Column(db.Text)
  is_public = db.Column(db.Boolean, default=False)
  create_time = db.Column(db.DateTime, default=db.func.now())

  def __init__(self, author_kerberos, title, text, is_public):
    self.author_kerberos = author_kerberos
    self.title = title
    self.text = text
    self.is_public = is_public


class User(db.Model):
  __tablename__ = 'users'
  id = db.Column(db.Integer, primary_key=True)
  kerberos = db.Column(db.String(128), unique=True, index=True)
  team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  team = db.relationship("Team", back_populates="members")

  def __init__(self, kerberos, team):
    self.kerberos = kerberos
    self.team = team


class Bot(db.Model):
  __tablename__ = 'bots'
  id = db.Column(db.Integer, primary_key=True)
  team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  team = db.relationship("Team", foreign_keys=team_id, back_populates="bots")
  name = db.Column(db.String(128), nullable=False)
  s3_key = db.Column(db.String(256), nullable=False)
  wins = db.Column(db.Integer)
  losses = db.Column(db.Integer)
  create_time = db.Column(db.DateTime, default=db.func.now())

  # TODO: Compile logs

  def __init__(self, team, name, s3_key):
    self.team = team
    self.name = name
    self.s3_key = s3_key
    self.wins = 0
    self.losses = 0


class Team(db.Model):
  __tablename__ = 'teams'
  id = db.Column(db.Integer, primary_key=True)
  name = db.Column(db.String(128), unique=True)
  members = db.relationship("User", back_populates="team")
  join_requests = db.relationship("TeamJoinRequest", back_populates="team")
  elo = db.Column(db.Float)
  wins = db.Column(db.Integer)
  losses = db.Column(db.Integer)
  current_bot_id = db.Column(db.Integer, db.ForeignKey('bots.id'))
  is_disabled = db.Column(db.Boolean, default=False)
  must_autoaccept = db.Column(db.Boolean, default=False)

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
    return self.current_bot is not None and not self.is_disabled

  def can_be_challenged(self):
    return self.current_bot is not None and not self.is_disabled

  def can_be_joined(self):
    from scrimmage.settings import settings
    return len(self.members) < int(settings['maximum_team_size'])

  def can_be_requested(self):
    return self.can_be_joined() and not self.must_autoaccept and not self.is_disabled

  def can_initiate(self):
    from scrimmage.settings import settings
    num_games_outstanding = Game.query.filter(Game.initiator == self).filter(Game.status.in_([GameStatus.created, GameStatus.in_progress])).count()
    return num_games_outstanding < int(settings['spawn_limit_per_team'])

  def set_current_bot(self, bot):
    assert bot.team == self
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


class TeamJoinRequest(db.Model):
  __tablename__ = 'join_requests'
  id = db.Column(db.Integer, primary_key=True)
  kerberos = db.Column(db.String(128), unique=True, index=True)
  team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
  team = db.relationship("Team", back_populates="join_requests")

  def __init__(self, kerberos, team):
    assert team.can_be_requested()
    self.kerberos = kerberos
    self.team = team


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

  def should_autoaccept(self):
    return self.opponent.must_autoaccept or (self.opponent.elo > self.challenger.elo)

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

  def accept(self, was_automatic):
    assert self.status == GameRequestStatus.challenged
    self.status = GameRequestStatus.accepted
    initiator = self.challenger if was_automatic else self.opponent
    return Game(self, initiator)


class GameStatus(enum.Enum):
  created = 'created'                # Game has been created, has not been spawned.
  in_progress = 'in_progress'        # Game is currently being played
  internal_error = 'internal_error'  # Game was unable to be played, due to an internal error
  completed = 'completed'            # Game has been completed, there is a winner.


class Game(db.Model):
  __tablename__ = 'games'
  id = db.Column(db.Integer, primary_key=True)
  game_request_id = db.Column(db.Integer, db.ForeignKey('game_requests.id'), nullable=False, unique=True)
  initiator_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
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

  log_s3_key = db.Column(db.String(256))
  challenger_log_s3_key = db.Column(db.String(256))
  opponent_log_s3_key = db.Column(db.String(256))

  game_request = db.relationship("GameRequest")
  initiator = db.relationship("Team", foreign_keys=initiator_id)
  challenger = db.relationship("Team", foreign_keys=challenger_id)
  opponent = db.relationship("Team", foreign_keys=opponent_id)
  challenger_bot = db.relationship("Bot", foreign_keys=challenger_bot_id)
  opponent_bot = db.relationship("Bot", foreign_keys=opponent_bot_id)
  winner = db.relationship("Team", foreign_keys=winner_id)
  loser = db.relationship("Team", foreign_keys=loser_id)

  def __init__(self, game_request, initiator):
    self.game_request = game_request
    self.initiator = initiator
    self.challenger = game_request.challenger
    self.challenger_bot = self.challenger.current_bot
    self.opponent = game_request.opponent
    self.opponent_bot = self.opponent.current_bot

    self.status = GameStatus.created

  def friendly_status(self):
    if self.status == GameStatus.created:
      return "Queued"
    if self.status == GameStatus.in_progress:
      return "In Progress"
    if self.status == GameStatus.internal_error:
      return "Internal error"
    if self.status == GameStatus.completed:
      return "Completed"

  def spawn(self):
    assert self.status == GameStatus.created
    from scrimmage.tasks import play_game_task
    play_game_task.delay(self.id)
