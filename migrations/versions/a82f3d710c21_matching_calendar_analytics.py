"""Matching, UTC viewing intervals and explicit request attribution.

Revision ID: a82f3d710c21
Revises: 60310e117751
"""
from datetime import timedelta
from alembic import op
import sqlalchemy as sa

revision = 'a82f3d710c21'
down_revision = '60310e117751'
branch_labels = None
depends_on = None


def shift_dates(hours):
    table = sa.table('viewings', sa.column('id', sa.Integer()), sa.column('viewing_date', sa.DateTime()))
    connection = op.get_bind()
    for identifier, value in connection.execute(sa.select(table.c.id, table.c.viewing_date)).all():
        if value is not None:
            connection.execute(table.update().where(table.c.id == identifier).values(viewing_date=value + timedelta(hours=hours)))


def upgrade():
    with op.batch_alter_table('viewings') as batch:
        batch.add_column(sa.Column('request_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('duration_minutes', sa.Integer(), nullable=False, server_default='60'))
        batch.add_column(sa.Column('schedule_version', sa.Integer(), nullable=False, server_default='1'))
        batch.create_foreign_key(op.f('fk_viewings_request_id_requests'), 'requests', ['request_id'], ['id'], ondelete='RESTRICT')
        batch.create_index('ix_viewings_request_id', ['request_id'])
        batch.create_check_constraint(op.f('ck_viewings_duration'), 'duration_minutes >= 15 AND duration_minutes <= 480')
        batch.create_check_constraint(op.f('ck_viewings_version'), 'schedule_version >= 1')
    with op.batch_alter_table('viewings') as batch:
        batch.alter_column('duration_minutes', server_default=None, existing_type=sa.Integer(), existing_nullable=False)
        batch.alter_column('schedule_version', server_default=None, existing_type=sa.Integer(), existing_nullable=False)
    # Previous version stored naive Moscow wall time. Preserve the actual instant.
    shift_dates(-3)
    with op.batch_alter_table('deals') as batch:
        batch.add_column(sa.Column('request_id', sa.Integer(), nullable=True))
        batch.create_foreign_key(op.f('fk_deals_request_id_requests'), 'requests', ['request_id'], ['id'], ondelete='RESTRICT')
        batch.create_index('ix_deals_request_id', ['request_id'])
    op.create_table('request_matches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.Integer(), nullable=False),
        sa.Column('property_id', sa.Integer(), nullable=False),
        sa.Column('score', sa.Numeric(5, 2), nullable=False),
        sa.Column('algorithm_version', sa.String(30), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('agent_comment', sa.Text(), nullable=False),
        sa.Column('snapshot_json', sa.Text(), nullable=False),
        sa.Column('history_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_request_matches')),
        sa.ForeignKeyConstraint(['request_id'], ['requests.id'], name=op.f('fk_request_matches_request_id_requests'), ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], name=op.f('fk_request_matches_property_id_properties'), ondelete='RESTRICT'),
        sa.UniqueConstraint('request_id', 'property_id', name=op.f('uq_request_matches_request_id')),
        sa.CheckConstraint('score >= 0 AND score <= 100', name=op.f('ck_request_matches_score')),
        sa.CheckConstraint("status IN ('saved','rejected','viewing')", name=op.f('ck_request_matches_status')))
    op.create_index('ix_request_matches_request_id', 'request_matches', ['request_id'])
    op.create_index('ix_request_matches_property_id', 'request_matches', ['property_id'])


def downgrade():
    op.drop_index('ix_request_matches_property_id', table_name='request_matches')
    op.drop_index('ix_request_matches_request_id', table_name='request_matches')
    op.drop_table('request_matches')
    with op.batch_alter_table('deals') as batch:
        batch.drop_index('ix_deals_request_id')
        batch.drop_constraint(op.f('fk_deals_request_id_requests'), type_='foreignkey')
        batch.drop_column('request_id')
    shift_dates(3)
    with op.batch_alter_table('viewings') as batch:
        batch.drop_index('ix_viewings_request_id')
        batch.drop_constraint(op.f('fk_viewings_request_id_requests'), type_='foreignkey')
        batch.drop_constraint(op.f('ck_viewings_duration'), type_='check')
        batch.drop_constraint(op.f('ck_viewings_version'), type_='check')
        batch.drop_column('request_id')
        batch.drop_column('duration_minutes')
        batch.drop_column('schedule_version')
