"""add more user info to users

Revision ID: 69a829b740bf
Revises: ee46a4ecb7e3
Create Date: 2019-01-05 12:50:17.913922

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '69a829b740bf'
down_revision = 'ee46a4ecb7e3'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('users', sa.Column('class_year', sa.String(length=128), nullable=True))
    op.add_column('users', sa.Column('department', sa.String(length=128), nullable=True))
    op.add_column('users', sa.Column('full_name', sa.String(length=128), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('users', 'full_name')
    op.drop_column('users', 'department')
    op.drop_column('users', 'class_year')
    # ### end Alembic commands ###