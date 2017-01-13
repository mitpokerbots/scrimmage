from scrimmage import app, db

from scrimmage.models import AdminSetting

DEFAULTS = {
  'spawn_limit_per_team': '1',
  'recent_games_to_show': '15',
  'challenges_enabled': 'false',
  'extra_admins': ''
}

class SettingsClass(object):
  # Key, value pairs. The value represents the defaults if it is not stored in the DB.

  def items(self):
    return DEFAULTS.keys()

  def __getitem__(self, key):
    setting = AdminSetting.query.filter(AdminSetting.key == key).one_or_none()
    if setting is None:
      return DEFAULTS[key]
    else:
      return setting.value

  def __getattr__(self, key):
    return self[key]

  def __setitem__(self, key, value):
    assert key in DEFAULTS
    setting = AdminSetting.query.filter(AdminSetting.key == key).one_or_none()
    if setting is None:
      db.session.add(AdminSetting(key, str(value)))
    else:
      setting.value = str(value)

  def __setattr__(self, key, value):
    self[key] = value

settings = SettingsClass()
