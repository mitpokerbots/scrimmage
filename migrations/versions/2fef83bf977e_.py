"""Add an index on gamestatus

Revision ID: 2fef83bf977e
Revises: be27713baea3
Create Date: 2019-01-21 20:12:22.659210

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2fef83bf977e'
down_revision = 'be27713baea3'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_index(op.f('ix_tournament_games_status'), 'tournament_games', ['status'], unique=False)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_tournament_games_status'), table_name='tournament_games')
    # ### end Alembic commands ###
