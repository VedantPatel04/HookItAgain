"""initial schema from docs/database.md

Revision ID: 0001_initial_schema
Revises:
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from hookitagain.db.models import Base

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Runs only when auth.users exists (Supabase). Local Postgres without
# the Auth schema still gets the public tables.
PROFILES_TRIGGER = """
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.profiles (user_id, github_login, created_at)
  VALUES (
    NEW.id,
    COALESCE(
      NEW.raw_user_meta_data->>'user_name',
      NEW.raw_user_meta_data->>'preferred_username'
    ),
    now()
  )
  ON CONFLICT (user_id) DO UPDATE
    SET github_login = EXCLUDED.github_login;
  RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.handle_new_user();
"""


def upgrade() -> None:
    """Create contract tables and, on Supabase, the auth.users to profiles trigger."""
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    op.execute(
        text(
            """
            DO $outer$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'auth' AND table_name = 'users'
              ) THEN
                EXECUTE $inner$
            """
            + PROFILES_TRIGGER
            + """
                $inner$;
              END IF;
            END
            $outer$;
            """
        )
    )


def downgrade() -> None:
    """Drop the profiles trigger if present, then drop all public contract tables."""
    op.execute(
        text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'auth' AND table_name = 'users'
              ) THEN
                DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
              END IF;
            END
            $$;
            DROP FUNCTION IF EXISTS public.handle_new_user();
            """
        )
    )
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
